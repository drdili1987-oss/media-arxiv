from pathlib import Path

import pytest

from mediabot import downloader
from mediabot.downloader import DownloadError, Fetched, Media, explain, platform_of
from mediabot.handlers import Bot, Config, command, extract_urls, topic_keyboard
from mediabot.tg import TelegramError

OWNER, STRANGER, GROUP = 156664, 999, -100123


class FakeTG:
    def __init__(self):
        self.calls, self.mid = [], 100
        self.fail_with: TelegramError | None = None

    def call(self, method, files=None, **params):
        if self.fail_with and method.startswith("send") and method != "sendMessage":
            raise self.fail_with
        self.calls.append((method, params, sorted(files or {})))
        self.mid += 1
        return {"message_id": self.mid}

    def send_message(self, chat_id, text, thread_id=None, **kw):
        return self.call("sendMessage", chat_id=chat_id, text=text, message_thread_id=thread_id, **kw)

    def edit_text(self, chat_id, message_id, text, **kw):
        self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text)

    def delete(self, chat_id, message_id):
        self.call("deleteMessage", chat_id=chat_id, message_id=message_id)
        return True

    def methods(self):
        return [c[0] for c in self.calls]

    def texts(self):
        return [c[1].get("text", "") for c in self.calls]


class FakeStore:
    def __init__(self):
        self.group, self.topics, self.pending, self.seen = None, {}, {}, set()

    def claim_update(self, uid):
        if uid in self.seen:
            return False
        self.seen.add(uid)
        return True

    def get_group(self): return self.group
    def set_group(self, c): self.group = c

    def upsert_topic(self, chat, thread, name, overwrite=True):
        if overwrite or (chat, thread) not in self.topics:
            self.topics[(chat, thread)] = name

    def delete_topic(self, chat, thread): self.topics.pop((chat, thread), None)

    def list_topics(self, chat):
        return [{"thread_id": t, "name": n} for (c, t), n in sorted(self.topics.items()) if c == chat]

    def add_pending(self, user, url):
        pid = len(self.pending) + 1
        self.pending[pid] = (user, url)
        return pid

    def take_pending(self, pid, user):
        v = self.pending.get(pid)
        if v and v[0] == user:
            del self.pending[pid]
            return v[1]
        return None


def make_fetcher(items_kinds=("video",), error=None):
    seen = []

    def fetcher(url, workdir: Path):
        seen.append(workdir)
        if error:
            raise DownloadError(error)
        items = []
        for i, k in enumerate(items_kinds):
            p = workdir / f"{i}.{'mp4' if k == 'video' else 'jpg'}"
            p.write_bytes(b"x")
            items.append(Media(p, k))
        return Fetched(items=items, title="Sarlavha")
    fetcher.seen = seen
    return fetcher


def make_bot(**kw):
    tg, store = FakeTG(), FakeStore()
    fetcher = kw.pop("fetcher", make_fetcher())
    bot = Bot(Config(allowed_ids=frozenset({OWNER}), **kw), tg, store, fetcher)
    return bot, tg, store, fetcher


def gmsg(text="", user=OWNER, thread=None, mid=10, **extra):
    m = {"message_id": mid, "chat": {"id": GROUP, "type": "supergroup", "is_forum": True},
         "from": {"id": user}, "text": text}
    if thread:
        m.update(message_thread_id=thread, is_topic_message=True)
    m.update(extra)
    return m


def pmsg(text, user=OWNER):
    return {"message_id": 5, "chat": {"id": user, "type": "private"}, "from": {"id": user}, "text": text}


uid = iter(range(1, 10_000))


def upd(**kw):
    return {"update_id": next(uid), **kw}


# ---------- yordamchi funksiyalar ----------
def test_extract_urls_strips_punctuation_and_dedupes():
    m = {"text": "qara: https://youtu.be/abc, va https://youtu.be/abc). yana https://x.com/a/status/1!"}
    assert extract_urls(m) == ["https://youtu.be/abc", "https://x.com/a/status/1"]


def test_extract_urls_text_link_and_caption():
    m = {"caption": "rasm", "caption_entities": [{"type": "text_link", "url": "https://pin.it/xyz"}]}
    assert extract_urls(m) == ["https://pin.it/xyz"]


def test_command_parsing():
    assert command({"text": "/bogla@MyBot  AI kurs"}) == ("bogla", "AI kurs")
    assert command({"text": "salom"}) is None


def test_platform_names():
    assert platform_of("https://www.instagram.com/reel/x") == "Instagram"
    assert platform_of("https://vm.tiktok.com/x") == "TikTok"
    assert platform_of("https://example.org/a") == "example.org"


def test_explain_maps_errors():
    assert "login" in explain(["ERROR: Sign in to confirm you're not a bot"], "https://youtu.be/a")
    assert "qo'llab-quvvatlanmaydi" in explain(["ERROR: Unsupported URL: https://a.b"], "https://a.b")
    assert "o'chirilgan" in explain(["HTTP Error 404: Not Found"], "https://x.com/a")


def test_keyboard_callback_data_fits_telegram_limit():
    kb = topic_keyboard(123456789, [{"thread_id": 2147483647, "name": "A" * 80}])
    for row in kb["inline_keyboard"]:
        for b in row:
            assert len(b["callback_data"].encode()) <= 64
            assert len(b["text"]) <= 40


# ---------- topiclar ----------
def test_learns_created_and_renamed_topics():
    bot, tg, store, _ = make_bot()
    bot.handle(upd(message=gmsg(user=STRANGER, thread=7, forum_topic_created={"name": "AI"})))
    bot.handle(upd(message=gmsg(user=STRANGER, thread=7, forum_topic_edited={"name": "AI kurs"})))
    # topicdagi oddiy xabar eski (yaratilgandagi) nomni qaytarmasligi kerak
    bot.handle(upd(message=gmsg("salom", user=STRANGER, thread=7,
                                reply_to_message={"forum_topic_created": {"name": "AI"}})))
    assert store.topics == {(GROUP, 7): "AI kurs"}
    assert tg.calls == []  # begonaga javob yo'q


def test_bogla_registers_old_topic_and_requires_thread():
    bot, tg, store, _ = make_bot()
    bot.handle(upd(message=gmsg("/bogla", thread=9, reply_to_message={"forum_topic_created": {"name": "GitHub"}})))
    assert store.topics[(GROUP, 9)] == "GitHub"
    bot.handle(upd(message=gmsg("/bogla Biznes", thread=9)))
    assert store.topics[(GROUP, 9)] == "Biznes"
    bot.handle(upd(message=gmsg("/bogla X")))
    assert "topic ichida" in tg.texts()[-1]


def test_ulash_requires_forum():
    bot, tg, store, _ = make_bot()
    m = gmsg("/ulash")
    m["chat"]["is_forum"] = False
    bot.handle(upd(message=m))
    assert store.group is None and "Topics" in tg.texts()[-1]
    bot.handle(upd(message=gmsg("/ulash")))
    assert store.group == GROUP


# ---------- guruhda yuklash ----------
def test_link_in_topic_is_downloaded_to_same_topic_and_cleaned():
    bot, tg, store, fetcher = make_bot()
    bot.handle(upd(message=gmsg("https://www.instagram.com/reel/abc", thread=7, mid=55)))
    sent = [c for c in tg.calls if c[0] == "sendVideo"]
    assert len(sent) == 1 and sent[0][1]["message_thread_id"] == 7
    assert "Instagram" in sent[0][1]["caption"]
    deleted = [c[1]["message_id"] for c in tg.calls if c[0] == "deleteMessage"]
    assert 55 in deleted  # asl havola o'chirildi
    assert store.group == GROUP  # birinchi foydalanishda avtomatik bog'landi
    assert not any(p.exists() for p in fetcher.seen)  # vaqtinchalik fayllar o'chirildi


def test_stranger_link_ignored():
    bot, tg, _, fetcher = make_bot()
    bot.handle(upd(message=gmsg("https://youtu.be/a", user=STRANGER)))
    assert tg.calls == [] and fetcher.seen == []


def test_download_error_is_reported_and_source_kept():
    bot, tg, _, _ = make_bot(fetcher=make_fetcher(error="🔒 Instagram login talab qilyapti"))
    bot.handle(upd(message=gmsg("https://instagram.com/stories/a/1", thread=7, mid=77)))
    assert "login" in tg.texts()[-1]
    assert 77 not in [c[1]["message_id"] for c in tg.calls if c[0] == "deleteMessage"]


def test_duplicate_update_processed_once():
    bot, tg, _, fetcher = make_bot()
    u = upd(message=gmsg("https://youtu.be/a", thread=7))
    bot.handle(u)
    bot.handle(u)
    assert len(fetcher.seen) == 1


def test_carousel_photos_as_documents_in_album():
    bot, tg, _, _ = make_bot(fetcher=make_fetcher(("photo", "photo", "photo")))
    bot.handle(upd(message=gmsg("https://www.instagram.com/p/abc", thread=7)))
    groups = [c for c in tg.calls if c[0] == "sendMediaGroup"]
    assert len(groups) == 1
    media = groups[0][1]["media"]
    assert [m["type"] for m in media] == ["document"] * 3
    assert "caption" in media[0] and groups[0][2] == ["f0", "f1", "f2"]


def test_mixed_album_when_photos_as_photo():
    bot, tg, _, _ = make_bot(photos_as_document=False, fetcher=make_fetcher(("video", "photo")))
    bot.handle(upd(message=gmsg("https://www.instagram.com/p/abc", thread=7)))
    media = [c for c in tg.calls if c[0] == "sendMediaGroup"][0][1]["media"]
    assert [m["type"] for m in media] == ["video", "photo"]


# ---------- shaxsiy xabar ----------
def test_pm_flow_choose_topic_and_post():
    bot, tg, store, _ = make_bot()
    store.group = GROUP
    store.topics = {(GROUP, 7): "AI", (GROUP, 8): "Biznes"}
    bot.handle(upd(message=pmsg("https://youtu.be/abc")))
    kb = tg.calls[-1][1]["reply_markup"]["inline_keyboard"]
    labels = [b["text"] for row in kb for b in row]
    assert labels[:2] == ["AI", "Biznes"]
    data = kb[0][0]["callback_data"]
    cq = {"id": "q1", "data": data, "from": {"id": OWNER},
          "message": {"message_id": 50, "chat": {"id": OWNER}}}
    bot.handle(upd(callback_query=cq))
    sent = [c for c in tg.calls if c[0] == "sendVideo"][0][1]
    assert sent["chat_id"] == GROUP and sent["message_thread_id"] == 7
    assert "joylandi" in tg.texts()[-1]
    bot.handle(upd(callback_query=cq))  # ikkinchi bosish: eskirgan
    assert "eskirgan" in tg.texts()[-1]


def test_pm_general_and_cancel():
    bot, tg, store, _ = make_bot()
    store.group = GROUP
    store.topics = {(GROUP, 7): "AI"}
    bot.handle(upd(message=pmsg("https://youtu.be/a https://youtu.be/b")))
    kbs = [c[1]["reply_markup"]["inline_keyboard"] for c in tg.calls if "reply_markup" in c[1]]
    assert len(kbs) == 2
    general, cancel = kbs[0][-1]
    mk = lambda d: {"id": "q", "data": d, "from": {"id": OWNER}, "message": {"message_id": 1, "chat": {"id": OWNER}}}
    bot.handle(upd(callback_query=mk(general["callback_data"])))
    sent = [c for c in tg.calls if c[0] == "sendVideo"][0][1]
    assert sent["message_thread_id"] is None
    bot.handle(upd(callback_query=mk(kbs[1][-1][1]["callback_data"])))
    assert "Bekor" in tg.texts()[-1]


def test_pm_deleted_topic_is_forgotten():
    bot, tg, store, _ = make_bot()
    store.group = GROUP
    store.topics = {(GROUP, 7): "AI"}
    bot.handle(upd(message=pmsg("https://youtu.be/a")))
    data = tg.calls[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    tg.fail_with = TelegramError("Bad Request: message thread not found", 400)
    bot.handle(upd(callback_query={"id": "q", "data": data, "from": {"id": OWNER},
                                   "message": {"message_id": 1, "chat": {"id": OWNER}}}))
    assert store.topics == {} and "topilmadi" in tg.texts()[-1]


def test_pm_stranger_and_no_group():
    bot, tg, store, _ = make_bot()
    bot.handle(upd(message=pmsg("https://youtu.be/a", user=STRANGER)))
    assert "shaxsiy bot" in tg.texts()[-1]
    bot.handle(upd(message=pmsg("https://youtu.be/a")))
    assert "/ulash" in tg.texts()[-1]


def test_callback_from_stranger_rejected():
    bot, tg, store, _ = make_bot()
    store.pending[1] = (OWNER, "https://youtu.be/a")
    bot.handle(upd(callback_query={"id": "q", "data": "p:1:7", "from": {"id": STRANGER},
                                   "message": {"message_id": 1, "chat": {"id": STRANGER}}}))
    assert store.pending  # o'g'irlab bo'lmaydi
    assert tg.calls[-1][1]["text"] == "⛔ Ruxsat yo'q"


# ---------- yuklagich ----------
def test_fetch_drops_files_over_telegram_limit(tmp_path, monkeypatch):
    def fake_ytdlp(url, out, height):
        out.mkdir(parents=True, exist_ok=True)
        (out / "01_a.mp4").write_bytes(b"x" * 10)
        (out / "02_b.mp4.part").write_bytes(b"x")
        return "T"
    monkeypatch.setattr(downloader, "_ytdlp", fake_ytdlp)
    monkeypatch.setattr(downloader, "TG_LIMIT", 5)
    with pytest.raises(DownloadError, match="50 MB"):
        downloader.fetch("https://youtu.be/a", tmp_path)


def test_fetch_falls_back_to_gallery(tmp_path, monkeypatch):
    def bad_ytdlp(url, out, height):
        raise RuntimeError("ERROR: [Instagram] abc: Requested content is not available, rate-limit reached or login required")

    def gallery(url, out, timeout_s):
        out.mkdir(parents=True, exist_ok=True)
        (out / "1.jpg").write_bytes(b"x")
    monkeypatch.setattr(downloader, "_ytdlp", bad_ytdlp)
    monkeypatch.setattr(downloader, "_gallery", gallery)
    r = downloader.fetch("https://www.instagram.com/p/abc", tmp_path)
    assert [m.kind for m in r.items] == ["photo"]


def test_fetch_both_fail_explains(tmp_path, monkeypatch):
    def bad(*a, **k):
        raise RuntimeError("login required")
    monkeypatch.setattr(downloader, "_ytdlp", bad)
    monkeypatch.setattr(downloader, "_gallery", bad)
    with pytest.raises(DownloadError, match="login"):
        downloader.fetch("https://www.instagram.com/stories/a/1", tmp_path)


def test_explain_prefers_specific_error_over_unsupported():
    errs = ["ERROR: [youtube] a: Unable to connect to proxy: Tunnel connection failed: 403 Forbidden",
            "[gallery-dl][error] Unsupported URL 'https://youtu.be/a'"]
    assert "aloqa" in explain(errs, "https://youtu.be/a")
    assert "qo'llab-quvvatlanmaydi" in explain(["Unsupported URL"], "https://a.b")
