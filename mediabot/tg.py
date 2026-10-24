"""Telegram Bot API uchun yupqa sinxron mijoz (serverless muhitda state yo'q)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx


class TelegramError(Exception):
    def __init__(self, description: str, code: int | None = None):
        super().__init__(description)
        self.description = description
        self.code = code

    @property
    def thread_missing(self) -> bool:
        d = self.description.lower()
        return "thread not found" in d or "topic_deleted" in d or "topic_closed" in d


class Telegram:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.http = client or httpx.Client(timeout=httpx.Timeout(30, read=240, write=240))

    def call(self, method: str, files: dict[str, Path] | None = None, **params: Any) -> Any:
        data = {
            k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list, bool)) else v
            for k, v in params.items()
            if v is not None
        }
        handles = {}
        try:
            for key, path in (files or {}).items():
                handles[key] = (path.name, path.open("rb"))
            r = self.http.post(self.base + method, data=data, files=handles or None)
        finally:
            for _, fh in handles.values():
                fh.close()
        try:
            body = r.json()
        except ValueError:
            raise TelegramError(f"HTTP {r.status_code}", r.status_code) from None
        if not body.get("ok"):
            raise TelegramError(body.get("description", "unknown error"), body.get("error_code"))
        return body["result"]

    # Qulay qisqartmalar
    def send_message(self, chat_id: int, text: str, thread_id: int | None = None, **kw: Any) -> dict:
        return self.call("sendMessage", chat_id=chat_id, text=text, message_thread_id=thread_id,
                         parse_mode="HTML", link_preview_options={"is_disabled": True}, **kw)

    def edit_text(self, chat_id: int, message_id: int, text: str, **kw: Any) -> None:
        try:
            self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text,
                      parse_mode="HTML", link_preview_options={"is_disabled": True}, **kw)
        except TelegramError as e:
            if "not modified" not in e.description:
                raise

    def delete(self, chat_id: int, message_id: int) -> bool:
        try:
            self.call("deleteMessage", chat_id=chat_id, message_id=message_id)
            return True
        except TelegramError:
            return False
