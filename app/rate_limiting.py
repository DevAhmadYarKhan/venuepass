"""Atomic Redis windows and ASGI protection for expensive authentication requests."""

from hashlib import sha256
from ipaddress import ip_address
from math import ceil

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette._utils import get_route_path
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Redis executes the entire script atomically across application instances.
# A denied attempt leaves both count and expiration unchanged.
WINDOW_SCRIPT = """
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
if count >= tonumber(ARGV[1]) then
    return {0, redis.call('PTTL', KEYS[1])}
end
count = redis.call('INCR', KEYS[1])
if count == 1 then
    -- Anchor expiration only once, so later attempts cannot extend the window.
    redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return {1, redis.call('PTTL', KEYS[1])}
"""


class AuthRateLimiter:
    """Share per-endpoint, per-address counters without storing raw client IPs."""

    def __init__(self, client: Redis, namespace: str = 'venuepass:auth:v1'):
        """Keep one pooled client and allow isolated namespaces in integration tests."""
        self.client = client
        self.namespace = namespace
        self.script = client.register_script(WINDOW_SCRIPT)

    async def check(self, endpoint: str, host: str, limit: int, seconds: int) -> tuple[bool, int]:
        """Consume an allowed attempt and return a rounded-up retry delay."""
        try:
            address = ip_address(host)
            # Treat IPv4-mapped IPv6 addresses as the same client as plain IPv4.
            host = str(getattr(address, 'ipv4_mapped', None) or address)
        except ValueError:
            # ASGI test clients and transports may supply a non-IP peer name.
            pass
        digest = sha256(host.encode()).hexdigest()
        allowed, ttl = await self.script(
            keys=[f'{self.namespace}:{endpoint}:{digest}'], args=[limit, seconds * 1000])
        return bool(allowed), max(1, ceil(ttl / 1000))


class AuthRateLimitMiddleware:
    """Reject excessive attempts before request parsing or authentication work."""

    def __init__(self, app: ASGIApp):
        """Wrap the application without buffering request bodies."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Limit only authentication POSTs, including their redirecting slash forms."""
        paths = {'/auth/login': 'login', '/auth/register': 'register'}
        # Use the router's root-relative path so deployment prefixes cannot bypass
        # protection. Non-HTTP scopes have no route path (for example, lifespan).
        path = get_route_path(scope) if scope['type'] == 'http' else ''
        endpoint = paths.get(path[:-1] if path.endswith('/') else path)
        if scope['type'] == 'http' and scope.get('method') == 'POST' and endpoint:
            state = scope['app'].state
            limiter = state.auth_rate_limiter
            if limiter is not None:
                settings = state.settings
                # Uvicorn resolves trusted proxies; never parse user-supplied headers.
                host = scope['client'][0] if scope.get('client') else 'unknown'
                try:
                    allowed, retry = await limiter.check(
                        endpoint, host, getattr(settings, f'auth_{endpoint}_limit'),
                        getattr(settings, f'auth_{endpoint}_window_seconds'))
                except (RedisError, OSError):
                    # Fail closed only for authentication, without leaking backend details.
                    response = JSONResponse(
                        {'detail': 'Authentication temporarily unavailable'}, status_code=503)
                    await response(scope, receive, send)
                    return
                if not allowed:
                    response = JSONResponse({'detail': 'Too many requests'}, status_code=429,
                        headers={'Retry-After': str(retry)})
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
