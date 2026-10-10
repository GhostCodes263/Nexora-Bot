from __future__ import annotations

import json
import secrets

from app.services.cache import Cache


def new_id() -> str:
    return secrets.token_hex(4)


async def save(cache: Cache, game_id: str, data: dict, ttl: int = 900) -> None:
    await cache.set(f"g:{game_id}", json.dumps(data), ttl)


async def load(cache: Cache, game_id: str) -> dict | None:
    raw = await cache.get(f"g:{game_id}")
    return json.loads(raw) if raw else None


async def drop(cache: Cache, game_id: str) -> None:
    await cache.delete(f"g:{game_id}")
