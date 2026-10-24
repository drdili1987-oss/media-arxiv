"""Vercel kirish nuqtasi (FastAPI). Telegram webhook -> /webhook."""
from __future__ import annotations

import hmac
import logging
import os
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from mediabot.downloader import find_deno, find_ffmpeg
from mediabot.handlers import Bot, Config
from mediabot.store import Store
from mediabot.tg import Telegram

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mediabot.main")
# httpx har so'rov URL'ini INFO'da yozadi — Telegram URL'ida bot tokeni bor, logga tushmasin.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} muhit o'zgaruvchisi berilmagan")
    return value


@lru_cache(maxsize=1)
def bot() -> Bot:
    ids = frozenset(int(x) for x in env("ALLOWED_USER_IDS").replace(" ", "").split(",") if x)
    cfg = Config(
        allowed_ids=ids,
        delete_source=os.environ.get("DELETE_SOURCE", "1") != "0",
        photos_as_document=os.environ.get("PHOTOS_AS", "document") != "photo",
    )
    store = Store(env("SUPABASE_URL"), env("SUPABASE_KEY"), env("DB_SECRET"))
    return Bot(cfg, Telegram(env("TELEGRAM_BOT_TOKEN")), store)


def check_secret(given: str | None) -> None:
    if not given or not hmac.compare_digest(given, env("WEBHOOK_SECRET")):
        raise HTTPException(status_code=403)


@app.get("/")
def health() -> dict:
    return {"ok": True, "ffmpeg": bool(find_ffmpeg()), "deno": bool(find_deno())}


@app.post("/webhook")
async def webhook(request: Request) -> dict:
    check_secret(request.headers.get("x-telegram-bot-api-secret-token"))
    update = await request.json()
    try:
        # Yuklash uzoq davom etadi; Telegram qayta yuborsa, claim_update takrorni to'xtatadi.
        await run_in_threadpool(bot().handle, update)
    except Exception:
        log.exception("update %s ishlanmadi", update.get("update_id"))
    return {"ok": True}


@app.get("/probe")
def probe(key: str = "", url: str = "", clients: str = "") -> dict:
    """Diagnostika: havolani serverda yuklab ko'radi (Telegram'ga yubormaydi)."""
    check_secret(key)
    import tempfile
    import time
    from pathlib import Path

    from mediabot import downloader
    from mediabot.downloader import DownloadError, fetch

    saved = downloader.YT_CLIENTS
    if clients:
        downloader.YT_CLIENTS = [c for c in clients.split(",") if c]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="probe_") as td:
        try:
            r = fetch(url, Path(td))
            out = {"ok": True, "title": r.title, "notes": r.notes,
                   "items": [{"kind": m.kind, "name": m.path.name, "mb": round(m.path.stat().st_size / 2**20, 2)}
                             for m in r.items]}
        except DownloadError as e:
            out = {"ok": False, "error": str(e), "raw": [r[-400:] for r in e.raw]}
        finally:
            downloader.YT_CLIENTS = saved
    out["seconds"] = round(time.monotonic() - started, 1)
    out["deno"] = bool(find_deno())
    return out


@app.get("/setup")
def setup(key: str = "") -> dict:
    """Bir martalik: webhook va buyruqlar menyusini o'rnatadi. /setup?key=<WEBHOOK_SECRET>"""
    check_secret(key)
    host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or env("PUBLIC_HOST")
    tg = bot().tg
    tg.call("setWebhook", url=f"https://{host}/webhook", secret_token=env("WEBHOOK_SECRET"),
            allowed_updates=["message", "callback_query"], max_connections=10)
    tg.call("setMyCommands", commands=[
        {"command": "ulash", "description": "Botni shu guruhga bog'lash"},
        {"command": "bogla", "description": "Shu topicni ro'yxatga olish"},
        {"command": "topiclar", "description": "Ma'lum topiclar ro'yxati"},
        {"command": "help", "description": "Yordam"},
    ])
    return {"ok": True, "webhook": tg.call("getWebhookInfo")}
