"""Supabase RPC orqali saqlash. Jadvallarga to'g'ridan-to'g'ri kirish yopiq (RLS),
har bir funksiya maxfiy kalitni (DB_SECRET) tekshiradi."""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("mediabot.store")


class Store:
    def __init__(self, url: str, key: str, secret: str, client: httpx.Client | None = None):
        self.base = url.rstrip("/") + "/rest/v1/rpc/"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        self.secret = secret
        self.http = client or httpx.Client(timeout=15)

    def _rpc(self, fn: str, **args: Any) -> Any:
        r = self.http.post(self.base + fn, json={"p_secret": self.secret, **args}, headers=self.headers)
        r.raise_for_status()
        return r.json() if r.content else None

    def claim_update(self, update_id: int) -> bool:
        """Telegram webhook'ni qayta yuborsa, ikkinchi marta ishlamaslik uchun."""
        try:
            return bool(self._rpc("media_claim_update", p_update_id=update_id))
        except Exception:
            log.exception("claim_update ishlamadi, update baribir ishlanadi")
            return True

    def get_group(self) -> int | None:
        return self._rpc("media_get_group")

    def set_group(self, chat_id: int) -> None:
        self._rpc("media_set_group", p_chat_id=chat_id)

    def upsert_topic(self, chat_id: int, thread_id: int, name: str, overwrite: bool = True) -> None:
        self._rpc("media_upsert_topic", p_chat_id=chat_id, p_thread_id=thread_id,
                  p_name=name, p_overwrite=overwrite)

    def delete_topic(self, chat_id: int, thread_id: int) -> None:
        self._rpc("media_delete_topic", p_chat_id=chat_id, p_thread_id=thread_id)

    def list_topics(self, chat_id: int) -> list[dict]:
        return self._rpc("media_list_topics", p_chat_id=chat_id) or []

    def add_pending(self, user_id: int, url: str) -> int:
        return int(self._rpc("media_add_pending", p_user_id=user_id, p_url=url))

    def take_pending(self, pending_id: int, user_id: int) -> str | None:
        return self._rpc("media_take_pending", p_id=pending_id, p_user_id=user_id)
