"""Havoladan media yuklash: yt-dlp (video) va gallery-dl (rasmlar, karusellar).
Fayllar faqat chaqiruvchi bergan vaqtinchalik papkaga yoziladi."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("mediabot.dl")

TG_LIMIT = 50 * 1024 * 1024 - 512 * 1024   # Bot API: 50 MB (multipart sarlavhalar uchun zaxira)
MAX_ITEMS = 10
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
SKIP_EXT = {".part", ".ytdl", ".json", ".description", ".tmp", ".temp"}

# Rasmlar ko'p uchraydigan saytlar: avval gallery-dl
GALLERY_FIRST = ("pinterest.", "pin.it", "twitter.com", "x.com")

PLATFORMS = {
    "instagram.com": "Instagram", "youtube.com": "YouTube", "youtu.be": "YouTube",
    "tiktok.com": "TikTok", "pinterest.": "Pinterest", "pin.it": "Pinterest",
    "twitter.com": "X", "x.com": "X", "facebook.com": "Facebook", "fb.watch": "Facebook",
}


class DownloadError(Exception):
    """Foydalanuvchiga ko'rsatiladigan tushunarli xato."""


@dataclass
class Media:
    path: Path
    kind: str  # video | photo | other


@dataclass
class Fetched:
    items: list[Media]
    title: str = ""
    too_large: int = 0
    notes: list[str] = field(default_factory=list)


def platform_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for key, name in PLATFORMS.items():
        if key in host:
            return name
    return host.removeprefix("www.") or "havola"


def _executable(path: str | None) -> str | None:
    """Serverless to'plamda bajarish bayrog'i yo'qolishi mumkin: /tmp ga nusxalab tuzatamiz."""
    if not path or not os.path.isfile(path):
        return None
    if os.access(path, os.X_OK):
        return path
    target = Path("/tmp/bin") / Path(path).name
    try:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            target.chmod(0o755)
        return str(target)
    except OSError:
        return None


def find_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return _executable(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        return _executable(shutil.which("ffmpeg"))


def find_deno() -> str | None:
    try:
        import deno
        return _executable(deno.find_deno_bin())
    except Exception:
        return _executable(shutil.which("deno"))


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in PHOTO_EXT:
        return "photo"
    return "other"


def collect(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() not in SKIP_EXT]
    return sorted(files, key=lambda p: p.name)


def _ytdlp(url: str, out: Path, height: int) -> str:
    from yt_dlp import YoutubeDL

    ffmpeg = find_ffmpeg()
    fmt = (f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/b[height<={height}][ext=mp4]/"
           f"bv*[height<={height}]+ba/b[height<={height}]/b")
    if not ffmpeg:  # birlashtirish imkoni yo'q: faqat tayyor (audio+video) formatlar
        fmt = f"b[height<={height}][ext=mp4]/b[height<={height}]/b"
    opts = {
        "outtmpl": str(out / "%(autonumber)02d_%(id).60s.%(ext)s"),
        "format": fmt,
        "merge_output_format": "mp4",
        "noplaylist": True,          # YouTube ...&list= havolasi butun pleylistni yuklamasin
        "playlistend": MAX_ITEMS,    # Instagram karusel kabi ro'yxatlar uchun
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 20,
        "retries": 2,
        "max_filesize": TG_LIMIT * 3,  # juda katta faylni boshidanoq yuklamaymiz
        "match_filter": lambda info, *, incomplete=False: "Jonli efir" if info.get("is_live") else None,
    }
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    deno_bin = find_deno()
    if deno_bin:
        opts["js_runtimes"] = {"deno": {"path": deno_bin}}
    out.mkdir(parents=True, exist_ok=True)
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    return (info or {}).get("title") or ""


def _gallery(url: str, out: Path, timeout_s: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "gallery_dl", "--range", f"1-{MAX_ITEMS}", "-D", str(out),
           "--no-mtime", "-q", url]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if p.returncode != 0 and not collect(out):
        raise RuntimeError((p.stderr or p.stdout or f"gallery-dl kod {p.returncode}").strip()[-500:])


def explain(errors: list[str], url: str) -> str:
    name = platform_of(url)
    # Bir yuklagich "Unsupported URL" desa, ikkinchisining aniqroq xatosini ishlatamiz
    specific = [e for e in errors if "unsupported url" not in e.lower()]
    text = " ".join(specific or errors).lower()
    if not specific and errors:
        return "Bu havoladan media topilmadi yoki sayt qo'llab-quvvatlanmaydi."
    if any(k in text for k in ("unable to connect", "connection refused", "connection reset",
                                "name resolution", "network is unreachable")):
        return f"{name} bilan aloqa o'rnatib bo'lmadi (server tarmog'i). Birozdan keyin qayta urinib ko'ring."
    if "private" in text or "yopiq" in text:
        return f"{name}: kontent yopiq (private)."
    if any(k in text for k in ("login", "sign in", "cookies", "not a bot", "rate-limit",
                                "rate limit", "401", "403", "authorization", "checkpoint")):
        return (f"🔒 {name} bu kontentni serverga bermadi: login talab qilinyapti "
                f"yoki server IP manzili bloklangan.")
    if any(k in text for k in ("unavailable", "removed", "404", "not found", "does not exist", "deleted")):
        return f"{name}: kontent o'chirilgan yoki mavjud emas."
    if "timed out" in text or "timeout" in text:
        return f"{name} juda sekin javob berdi. Birozdan keyin qayta urinib ko'ring."
    tail = errors[-1].splitlines()[-1][:200] if errors else "noma'lum xato"
    return f"Yuklab bo'lmadi: {tail}"


def fetch(url: str, workdir: Path, timeout_s: int = 150) -> Fetched:
    host = (urlparse(url).hostname or "").lower()
    order = ["gallery", "ytdlp"] if any(k in host for k in GALLERY_FIRST) else ["ytdlp", "gallery"]
    errors: list[str] = []
    title = ""
    files: list[Path] = []
    used = ""
    for i, method in enumerate(order):
        out = workdir / f"{i}_{method}"
        try:
            if method == "ytdlp":
                title = _ytdlp(url, out, 1080)
            else:
                _gallery(url, out, timeout_s)
        except subprocess.TimeoutExpired:
            errors.append("timed out")
        except Exception as e:  # noqa: BLE001 — kutubxonalar turli xil xato tashlaydi
            errors.append(str(e))
            log.info("%s %s: %s", method, url, str(e)[:300])
        files = collect(out)
        if files:
            used = method
            break

        if method == "ytdlp" and not errors:
            break  # yt-dlp xatosiz, lekin fayl bermadi: max_filesize'dan katta — pastda 480p
    if not files and not (order[0] == "ytdlp" and not errors):
        raise DownloadError(explain(errors, url))

    # 1080p video 50 MB'dan oshsa (yoki umuman yuklanmasa) — bir marta 480p bilan qayta urinamiz
    videos = [f for f in files if classify(f) == "video"]
    if (used == "ytdlp" and videos and all(f.stat().st_size > TG_LIMIT for f in videos)) or not files:
        out = workdir / "low"
        try:
            title = _ytdlp(url, out, 480) or title
            if low := collect(out):
                files = low
        except Exception as e:  # noqa: BLE001
            log.info("480p qayta urinish: %s", str(e)[:300])
        if not files:
            raise DownloadError("Video juda katta (50 MB dan ortiq): Telegram bot orqali yuborib bo'lmaydi.")

    result = Fetched(items=[], title=title)
    for f in files[:MAX_ITEMS]:
        if f.stat().st_size > TG_LIMIT:
            result.too_large += 1
            continue
        result.items.append(Media(f, classify(f)))
    if not result.items:
        raise DownloadError("Fayl 50 MB dan katta: Telegram bot orqali yuborib bo'lmaydi.")
    if len(files) > MAX_ITEMS:
        result.notes.append(f"Faqat birinchi {MAX_ITEMS} ta fayl olindi.")
    if result.too_large:
        result.notes.append(f"{result.too_large} ta fayl 50 MB dan katta, tashlab ketildi.")
    return result
