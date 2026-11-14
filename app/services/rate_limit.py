"""Atomic shared fixed-window quotas; Redis failures never disable protection."""

from redis.asyncio import Redis


SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('TTL', KEYS[1])}
"""


class RedisRateLimiter:
    def __init__(self, url, *, client=None):
        self.client = client or Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)

    async def allow(self, key, *, limit, window):
        count, ttl = await self.client.eval(SCRIPT, 1, "aranyasetu:quota:"+key, window)
        return int(count) <= limit, max(1, int(ttl))

    async def close(self):
        await self.client.aclose()
