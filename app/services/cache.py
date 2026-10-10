from __future__ import annotations

import logging
import time

from redis import asyncio as aioredis
from redis.exceptions import RedisError

log = logging.getLogger(__name__)


class Cache:
    """Redis wrapper that silently falls back to per-process memory if Redis is down.

    The fallback is not shared between processes, which is fine for a single Render instance.
    """

    def __init__(self, url: str = "") -> None:
        self._url = url
        self._r: aioredis.Redis | None = None
        self._mem: dict[str, tuple[float, str]] = {}

    @property
    def redis_ok(self) -> bool:
        return self._r is not None

    async def connect(self) -> None:
        if not self._url:
            log.info("REDIS_URL not set; using in-memory fallback")
            return
        try:
            client = aioredis.from_url(
                self._url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3
            )
            await client.ping()
            self._r = client
            log.info("Redis connected")
        except Exception as exc:  # noqa: BLE001
            log.warning("Redis unavailable (%s); using in-memory fallback", type(exc).__name__)
            self._r = None

    async def close(self) -> None:
        if self._r is not None:
            await self._r.aclose()

    # --- memory fallback -------------------------------------------------
    def _mget(self, key: str) -> str | None:
        item = self._mem.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            self._mem.pop(key, None)
            return None
        return value

    def _mset(self, key: str, value: str, ttl: int) -> None:
        if len(self._mem) > 10_000:
            now = time.monotonic()
            self._mem = {k: v for k, v in self._mem.items() if v[0] >= now}
        self._mem[key] = (time.monotonic() + ttl, value)

    # --- API -------------------------------------------------------------
    async def get(self, key: str) -> str | None:
        if self._r is not None:
            try:
                return await self._r.get(key)
            except RedisError:
                log.warning("Redis get failed; falling back")
        return self._mget(key)

    async def set(self, key: str, value: str, ttl: int) -> None:
        if self._r is not None:
            try:
                await self._r.set(key, value, ex=ttl)
                return
            except RedisError:
                log.warning("Redis set failed; falling back")
        self._mset(key, value, ttl)

    async def delete(self, key: str) -> None:
        self._mem.pop(key, None)
        if self._r is not None:
            try:
                await self._r.delete(key)
            except RedisError:
                log.warning("Redis delete failed")

    async def set_nx(self, key: str, value: str, ttl: int) -> bool:
        if self._r is not None:
            try:
                return bool(await self._r.set(key, value, nx=True, ex=ttl))
            except RedisError:
                log.warning("Redis set_nx failed; falling back")
        if self._mget(key) is not None:
            return False
        self._mset(key, value, ttl)
        return True

    async def ttl(self, key: str) -> int:
        if self._r is not None:
            try:
                return max(int(await self._r.ttl(key)), 0)
            except RedisError:
                pass
        item = self._mem.get(key)
        return max(int(item[0] - time.monotonic()), 0) if item else 0

    async def cooldown(self, key: str, seconds: int) -> int:
        """Return 0 if allowed (and start the cooldown), otherwise seconds remaining."""
        if await self.set_nx(key, "1", seconds):
            return 0
        return max(await self.ttl(key), 1)

    async def hit(self, key: str, window: int) -> int:
        """Fixed-window counter; returns the number of hits in the current window."""
        if self._r is not None:
            try:
                n = int(await self._r.incr(key))
                if n == 1:
                    await self._r.expire(key, window)
                return n
            except RedisError:
                log.warning("Redis incr failed; falling back")
        current = self._mget(key)
        n = int(current) + 1 if current else 1
        item = self._mem.get(key)
        remaining = int(item[0] - time.monotonic()) if item else window
        self._mset(key, str(n), max(remaining, 1) if current else window)
        return n
