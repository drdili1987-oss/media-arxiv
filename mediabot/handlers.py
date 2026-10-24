"""Bot mantig'i: guruhdagi topiclar, havolalarni yuklash, shaxsiy xabardan topic tanlash."""
from __future__ import annotations

import html
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .downloader import DownloadError, Fetched, fetch, platform_of
from .store import Store
from .tg import Telegram, TelegramError

log = logging.getLogger("mediabot")

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
MAX_URLS = 3
GENERAL = 0  # callback'da "General" topic belgisi

HELP = (
    "<b>Kontent arxiv boti</b>\n\n"
    "<b>1-usul — topic ichida:</b> guruhda kerakli topicga havola tashlang. "
    "Bot videoni/rasmni yuklab, o'sha topicga joylaydi va havolali xabarni o'chiradi.\n\n"
    "<b>2-usul — shu yerda:</b> menga havola yuboring, topicni tugmadan tanlang.\n\n"
    "<b>Guruh buyruqlari:</b>\n"
    "/ulash — botni shu guruhga bog'lash (bir marta)\n"
    "/bogla — topic ichida yozing: eski topicni ro'yxatga oladi "
    "(<code>/bogla Yangi nom</code> — nomini ham beradi)\n"
    "/topiclar — bot biladigan topiclar ro'yxati\n\n"
    "Qo'llab-quvvatlanadi: Instagram, YouTube, TikTok, Pinterest, X, Facebook. "
    "Telegram cheklovi: bitta fayl 50 MB gacha."
)


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def extract_urls(msg: dict) -> list[str]:
    text = msg.get("text") or msg.get("caption") or ""
    found: list[str] = []
    for ent in (msg.get("entities") or msg.get("caption_entities") or []):
        if ent.get("type") == "text_link" and ent.get("url"):
            found.append(ent["url"])
    found += [u.rstrip(".,;:!?)»”'\"") for u in URL_RE.findall(text)]
    out: list[str] = []
    for u in found:
        if u not in out:
            out.append(u)
    return out[:MAX_URLS]


def command(msg: dict) -> tuple[str, str] | None:
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return None
    head, _, arg = text.partition(" ")
    return head[1:].split("@", 1)[0].lower(), arg.strip()


def thread_of(msg: dict) -> int | None:
    return msg.get("message_thread_id") if msg.get("is_topic_message") else None


@dataclass
class Config:
    allowed_ids: frozenset[int]
    delete_source: bool = True
    photos_as_document: bool = True


class Bot:
    def __init__(self, cfg: Config, tg: Telegram, store: Store,
                 fetcher: Callable[[str, Path], Fetched] = fetch):
        self.cfg, self.tg, self.store, self.fetcher = cfg, tg, store, fetcher

    # ---------- kirish nuqtasi ----------
    def handle(self, update: dict) -> None:
        if "update_id" in update and not self.store.claim_update(update["update_id"]):
            return
        if msg := update.get("message"):
            chat_type = msg.get("chat", {}).get("type")
            if chat_type in ("group", "supergroup"):
                self.on_group_message(msg)
            elif chat_type == "private":
                self.on_private_message(msg)
        elif cq := update.get("callback_query"):
            self.on_callback(cq)

    def allowed(self, user: dict | None) -> bool:
        return bool(user) and user.get("id") in self.cfg.allowed_ids

    # ---------- guruh ----------
    def learn_topic(self, msg: dict) -> None:
        chat_id = msg["chat"]["id"]
        try:
            if created := msg.get("forum_topic_created"):
                self.store.upsert_topic(chat_id, msg["message_thread_id"], created["name"])
            elif (edited := msg.get("forum_topic_edited")) and edited.get("name"):
                self.store.upsert_topic(chat_id, msg["message_thread_id"], edited["name"])
            elif msg.get("is_topic_message"):
                # Topicdagi har xabar topic ochilgan xabarga "javob" bo'ladi — undan nomni olamiz,
                # lekin qayta nomlangan topicning yangi nomini eskisi bilan almashtirmaymiz.
                root = (msg.get("reply_to_message") or {}).get("forum_topic_created")
                if root and root.get("name"):
                    self.store.upsert_topic(chat_id, msg["message_thread_id"], root["name"], overwrite=False)
        except Exception:
            log.exception("topicni saqlab bo'lmadi")

    def on_group_message(self, msg: dict) -> None:
        self.learn_topic(msg)
        if not self.allowed(msg.get("from")):
            return
        chat = msg["chat"]
        thread = thread_of(msg)
        cmd = command(msg)
        if cmd:
            name, arg = cmd
            if name == "ulash":
                return self.cmd_ulash(chat, thread)
            if name == "bogla":
                return self.cmd_bogla(msg, chat, thread, arg)
            if name == "topiclar":
                return self.cmd_topiclar(chat["id"], thread)
            if name in ("start", "help", "yordam"):
                self.tg.send_message(chat["id"], HELP, thread)
                return
        urls = extract_urls(msg)
        if not urls:
            return
        if self.store.get_group() is None and chat.get("is_forum"):
            self.store.set_group(chat["id"])
        self.group_download(msg, urls)

    def cmd_ulash(self, chat: dict, thread: int | None) -> None:
        if not chat.get("is_forum"):
            self.tg.send_message(chat["id"], "⚠️ Bu guruhda <b>Topics</b> yoqilmagan. "
                                 "Guruh sozlamalari → Topics ni yoqing va qayta /ulash yozing.", thread)
            return
        self.store.set_group(chat["id"])
        self.tg.send_message(chat["id"], "✅ Bot shu guruhga bog'landi. Shaxsiy xabarda yuborilgan "
                             "havolalar shu guruh topiclariga joylanadi.\n\n"
                             "Eski topiclarni tanitish uchun har birining ichida /bogla yozing.", thread)

    def cmd_bogla(self, msg: dict, chat: dict, thread: int | None, arg: str) -> None:
        if thread is None:
            self.tg.send_message(chat["id"], "Bu buyruqni kerakli <b>topic ichida</b> yozing.", None)
            return
        root = (msg.get("reply_to_message") or {}).get("forum_topic_created") or {}
        name = arg or root.get("name")
        if not name:
            self.tg.send_message(chat["id"], "Topic nomini aniqlay olmadim. Nomini yozing: "
                                 "<code>/bogla AI</code>", thread)
            return
        self.store.upsert_topic(chat["id"], thread, name)
        self.tg.send_message(chat["id"], f"✅ «{esc(name)}» topici ro'yxatga olindi.", thread)

    def cmd_topiclar(self, chat_id: int, thread: int | None) -> None:
        topics = self.store.list_topics(chat_id)
        if not topics:
            text = ("Hali birorta topic ma'lum emas. Har bir topic ichida /bogla yozing — "
                    "yangi ochilgan topiclarni bot o'zi ko'radi.")
        else:
            text = "<b>Bot biladigan topiclar:</b>\n" + "\n".join(f"• {esc(t['name'])}" for t in topics)
        self.tg.send_message(chat_id, text, thread)

    def group_download(self, msg: dict, urls: list[str]) -> None:
        chat_id = msg["chat"]["id"]
        thread = thread_of(msg)
        status = self.tg.send_message(chat_id, "⏳ Yuklanmoqda…", thread,
                                      reply_parameters={"message_id": msg["message_id"]})
        errors = []
        for url in urls:
            try:
                self.deliver(url, chat_id, thread)
            except DownloadError as e:
                errors.append(f"• {esc(url)}\n  {esc(str(e))}")
            except TelegramError as e:
                errors.append(f"• {esc(url)}\n  Telegram xatosi: {esc(e.description)}")
        if errors:
            self.tg.edit_text(chat_id, status["message_id"], "❌ Yuklab bo'lmadi:\n" + "\n".join(errors))
            return
        self.tg.delete(chat_id, status["message_id"])
        if self.cfg.delete_source:
            self.tg.delete(chat_id, msg["message_id"])

    # ---------- shaxsiy xabar ----------
    def on_private_message(self, msg: dict) -> None:
        chat_id = msg["chat"]["id"]
        if not self.allowed(msg.get("from")):
            self.tg.send_message(chat_id, "⛔ Bu shaxsiy bot, faqat egalari foydalana oladi.")
            return
        cmd = command(msg)
        if cmd and cmd[0] in ("start", "help", "yordam"):
            self.tg.send_message(chat_id, HELP)
            return
        urls = extract_urls(msg)
        if not urls:
            self.tg.send_message(chat_id, "Havola yuboring (Instagram, YouTube, TikTok, Pinterest, X…).")
            return
        group = self.store.get_group()
        if group is None:
            self.tg.send_message(chat_id, "Avval botni guruhga qo'shing (admin qilib) va guruhda "
                                 "/ulash yozing.")
            return
        topics = self.store.list_topics(group)
        if not topics:
            self.tg.send_message(chat_id, "Guruhdagi topiclar hali noma'lum. Har bir topic ichida "
                                 "/bogla yozing.")
            return
        for url in urls:
            pid = self.store.add_pending(msg["from"]["id"], url)
            self.tg.send_message(chat_id, f"📂 Qaysi topicga joylay?\n{esc(url)}",
                                 reply_markup=topic_keyboard(pid, topics))

    def on_callback(self, cq: dict) -> None:
        data = cq.get("data") or ""
        msg = cq.get("message") or {}
        if not self.allowed(cq.get("from")):
            self.tg.call("answerCallbackQuery", callback_query_id=cq["id"], text="⛔ Ruxsat yo'q")
            return
        m = re.fullmatch(r"p:(\d+):(\d+|x)", data)
        if not m or not msg:
            self.tg.call("answerCallbackQuery", callback_query_id=cq["id"])
            return
        self.tg.call("answerCallbackQuery", callback_query_id=cq["id"])
        chat_id, mid = msg["chat"]["id"], msg["message_id"]
        pid, target = int(m.group(1)), m.group(2)
        url = self.store.take_pending(pid, cq["from"]["id"])
        if url is None:
            self.tg.edit_text(chat_id, mid, "Bu so'rov eskirgan yoki bajarilgan. Havolani qayta yuboring.")
            return
        if target == "x":
            self.tg.edit_text(chat_id, mid, f"❌ Bekor qilindi\n{esc(url)}")
            return
        group = self.store.get_group()
        thread = int(target) or None
        names = {t["thread_id"]: t["name"] for t in self.store.list_topics(group)}
        name = names.get(thread, "General") if thread else "General"
        self.tg.edit_text(chat_id, mid, f"⏳ «{esc(name)}» ga yuklanmoqda…\n{esc(url)}")
        try:
            self.deliver(url, group, thread)
        except DownloadError as e:
            self.tg.edit_text(chat_id, mid, f"❌ {esc(str(e))}\n{esc(url)}")
            return
        except TelegramError as e:
            if e.thread_missing and thread:
                self.store.delete_topic(group, thread)
                text = f"❌ «{esc(name)}» topici guruhda topilmadi (o'chirilgan yoki yopilgan). Ro'yxatdan olib tashladim."
            else:
                text = f"❌ Telegram xatosi: {esc(e.description)}"
            self.tg.edit_text(chat_id, mid, f"{text}\n{esc(url)}")
            return
        self.tg.edit_text(chat_id, mid, f"✅ «{esc(name)}» ga joylandi\n{esc(url)}")

    # ---------- yuklash va yuborish ----------
    def deliver(self, url: str, chat_id: int, thread: int | None) -> None:
        with tempfile.TemporaryDirectory(prefix="mb_") as td:  # chiqishda fayllar darhol o'chadi
            fetched = self.fetcher(url, Path(td))
            self.send_media(chat_id, thread, fetched, url)

    def caption(self, fetched: Fetched, url: str) -> str:
        parts = [f'🔗 <a href="{html.escape(url)}">{esc(platform_of(url))}</a>']
        if fetched.title:
            parts.append(esc(fetched.title[:300]))
        parts += [f"<i>{esc(n)}</i>" for n in fetched.notes]
        return "\n".join(parts)[:1000]

    def send_media(self, chat_id: int, thread: int | None, fetched: Fetched, url: str) -> None:
        cap = self.caption(fetched, url)
        photo_type = "document" if self.cfg.photos_as_document else "photo"
        videos = [m for m in fetched.items if m.kind == "video"]
        photos = [m for m in fetched.items if m.kind == "photo"]
        others = [m for m in fetched.items if m.kind == "other"]

        batches: list[tuple[str, list]] = []
        if photo_type == "photo":
            visual = videos + photos  # rasm va video bitta albomda bo'la oladi
            batches += [("mixed", visual[i:i + 10]) for i in range(0, len(visual), 10)]
        else:
            batches += [("video", videos[i:i + 10]) for i in range(0, len(videos), 10)]
            batches += [("document", photos[i:i + 10]) for i in range(0, len(photos), 10)]
        batches += [("document", [o]) for o in others]

        first = True
        for kind, items in batches:
            c = cap if first else None
            first = False
            if len(items) == 1:
                self._send_single(chat_id, thread, items[0], kind, photo_type, c)
                continue
            media, files = [], {}
            for i, it in enumerate(items):
                key = f"f{i}"
                files[key] = it.path
                t = ("video" if it.kind == "video" else photo_type) if kind == "mixed" else kind
                entry = {"type": t, "media": f"attach://{key}"}
                if t == "video":
                    entry["supports_streaming"] = True
                if i == 0 and c:
                    entry.update(caption=c, parse_mode="HTML")
                media.append(entry)
            self.tg.call("sendMediaGroup", files=files, chat_id=chat_id, message_thread_id=thread, media=media)

    def _send_single(self, chat_id, thread, item, kind, photo_type, cap) -> None:
        common = dict(chat_id=chat_id, message_thread_id=thread, caption=cap,
                      parse_mode="HTML" if cap else None)
        if item.kind == "video" and kind != "document":
            self.tg.call("sendVideo", files={"video": item.path}, supports_streaming=True, **common)
        elif item.kind == "photo" and photo_type == "photo":
            self.tg.call("sendPhoto", files={"photo": item.path}, **common)
        else:
            self.tg.call("sendDocument", files={"document": item.path}, **common)


def topic_keyboard(pid: int, topics: list[dict]) -> dict:
    buttons = [{"text": t["name"][:40], "callback_data": f"p:{pid}:{t['thread_id']}"} for t in topics]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([{"text": "📌 General", "callback_data": f"p:{pid}:{GENERAL}"},
                 {"text": "❌ Bekor", "callback_data": f"p:{pid}:x"}])
    return {"inline_keyboard": rows}
