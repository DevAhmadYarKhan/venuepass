"""Exercise authentication interception and real Redis concurrency in isolation."""

import asyncio
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

from app.config import Settings
from app.main import create_app
from app.rate_limiting import AuthRateLimiter
from app.services import auth

SECRET = 'test-signing-secret-that-is-at-least-32-characters'


def settings(**overrides):
    """Avoid real PostgreSQL and environment-derived Redis in HTTP unit tests."""
    values = dict(jwt_secret=SECRET,
        database_url='postgresql+psycopg://unused:unused@127.0.0.1:1/unavailable', redis_url=None)
    values.update(overrides)
    return Settings(_env_file=None, **values)


class StubLimiter:
    """Supply deterministic decisions while recording identity and configuration."""

    def __init__(self, results):
        """Accept ordered decisions or Redis exceptions for successive requests."""
        self.results = iter(results)
        self.calls = []

    async def check(self, *args):
        """Record the request and simulate a backend decision without real I/O."""
        self.calls.append(args)
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize('path', ['/auth/login', '/auth/register', '/auth/login/', '/auth/register/'])
@pytest.mark.parametrize('root_path', ['', '/api'])
async def test_blocked_requests_skip_authentication(path, root_path, monkeypatch):
    """Reject exhausted requests before parsing, including prefixed deployments."""
    async def unexpected(*args, **kwargs):
        """Fail immediately if a rejected request reaches password work."""
        pytest.fail('Blocked request reached authentication')

    monkeypatch.setattr(auth, 'login', unexpected)
    monkeypatch.setattr(auth, 'register', unexpected)
    app = create_app(settings())
    limiter = StubLimiter([(False, 23)])
    async with app.router.lifespan_context(app):
        app.state.auth_rate_limiter = limiter
        # Model Uvicorn's prefixed path and ASGI root_path together.
        async with AsyncClient(transport=ASGITransport(app=app, root_path=root_path), base_url='http://test') as client:
            response = await client.post(root_path + path, content='{invalid')
    assert response.status_code == 429
    assert response.json() == {'detail': 'Too many requests'}
    assert response.headers['retry-after'] == '23'
    assert limiter.calls[0][0] == ('login' if 'login' in path else 'register')


async def test_allowed_malformed_attempts_and_spoofed_headers():
    """Invalid payloads consume attempts and raw forwarding headers change no identity."""
    app = create_app(settings(auth_login_limit=2, auth_login_window_seconds=17))
    limiter = StubLimiter([(True, 17), (False, 16)])
    async with app.router.lifespan_context(app):
        app.state.auth_rate_limiter = limiter
        async with AsyncClient(transport=ASGITransport(app=app, client=('192.0.2.1', 123)),
            base_url='http://test') as client:
            first = await client.post('/auth/login', json={})
            second = await client.post('/auth/login', json={}, headers={
                'X-Forwarded-For': '198.51.100.1', 'X-Real-IP': '198.51.100.2'})
            assert (await client.get('/health')).status_code == 200
            assert (await client.get('/auth/login')).status_code == 405
    assert first.status_code == 422 and second.status_code == 429
    assert limiter.calls == [('login', '192.0.2.1', 2, 17)] * 2


@pytest.mark.parametrize('root_path', ['', '/api'])
async def test_successful_requests_are_counted(root_path, monkeypatch):
    """Successful login routes and consumes quota with or without a deployment prefix."""
    calls = []

    async def login(*args, **kwargs):
        """Return a token without hashing or querying the unavailable test database."""
        calls.append(kwargs)
        return 'test-token'

    monkeypatch.setattr(auth, 'login', login)
    app = create_app(settings())
    limiter = StubLimiter([(True, 60), (False, 59)])
    async with app.router.lifespan_context(app):
        app.state.auth_rate_limiter = limiter
        async with AsyncClient(transport=ASGITransport(app=app, root_path=root_path), base_url='http://test') as client:
            payload = {'email': 'user@example.com', 'password': 'long-enough-password'}
            assert (await client.post(root_path + '/auth/login', json=payload)).status_code == 200
            assert (await client.post(root_path + '/auth/login', json=payload)).status_code == 429
    assert len(calls) == 1 and len(limiter.calls) == 2


@pytest.mark.parametrize('path', ['/auth/login', '/auth/register', '/auth/login/', '/auth/register/'])
@pytest.mark.parametrize('root_path', ['', '/api'])
async def test_redis_outage_only_blocks_authentication(path, root_path):
    """Fail closed even with prefixed/slash routes; public routes remain accessible."""
    app = create_app(settings())
    async with app.router.lifespan_context(app):
        app.state.auth_rate_limiter = StubLimiter([ConnectionError('private backend details')])
        async with AsyncClient(transport=ASGITransport(app=app, root_path=root_path), base_url='http://test') as client:
            response = await client.post(root_path + path, json={})
            assert (await client.get(root_path + '/health')).status_code == 200
            assert (await client.get(root_path + '/docs')).status_code == 200
    assert response.status_code == 503
    assert response.json() == {'detail': 'Authentication temporarily unavailable'}


async def test_disabled_limiter_preserves_validation():
    """Native development without Redis retains the existing validation behavior."""
    app = create_app(settings())
    async with app.router.lifespan_context(app):
        assert app.state.auth_rate_limiter is None
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.post('/auth/register', json={})).status_code == 422


@pytest.mark.parametrize('field', ['auth_login_limit', 'auth_login_window_seconds',
    'auth_register_limit', 'auth_register_window_seconds'])
@pytest.mark.parametrize('value', [0, -1])
def test_invalid_limit_configuration(field, value):
    """Reject disabled or negative windows rather than silently weakening protection."""
    with pytest.raises(ValidationError):
        settings(**{field: value})


@pytest.fixture
async def redis_limiters():
    """Use two independent Redis clients with unique keys, never flushing a database."""
    url = Settings().test_redis_url
    if url is None:
        pytest.skip('Set TEST_REDIS_URL to run real Redis integration tests')
    clients = [Redis.from_url(str(url)) for _ in range(2)]
    namespace = f'venuepass:test:auth:{uuid4()}'
    try:
        await clients[0].ping()
        yield [AuthRateLimiter(client, namespace) for client in clients]
    finally:
        # Remove only this test's namespaced keys, including on assertion failure.
        try:
            keys = [key async for key in clients[0].scan_iter(match=f'{namespace}:*')]
            if keys:
                await clients[0].delete(*keys)
        finally:
            for client in clients:
                await client.aclose()


@pytest.mark.integration
async def test_redis_concurrency_and_counter_isolation(redis_limiters):
    """Independent workers share atomic quotas while endpoints and IPs stay separate."""
    first, second = redis_limiters
    decisions = await asyncio.gather(*[
        redis_limiters[index % 2].check('login', '192.0.2.1', 10, 60)
        for index in range(40)])
    assert sum(allowed for allowed, _ in decisions) == 10
    assert all(1 <= retry <= 60 for _, retry in decisions)
    assert (await first.check('login', '::ffff:192.0.2.1', 10, 60))[0] is False
    assert (await second.check('login', '192.0.2.2', 10, 60))[0] is True
    assert (await first.check('register', '192.0.2.1', 5, 3600))[0] is True
    keys = [key async for key in first.client.scan_iter(match=f'{first.namespace}:*')]
    assert len(keys) == 3
    assert all(b'192.0.2' not in key for key in keys)


@pytest.mark.integration
async def test_http_instances_share_redis_limits(redis_limiters):
    """Concurrent malformed HTTP attempts on two apps share one backend quota."""
    url = Settings().test_redis_url
    apps = [create_app(settings(redis_url=url, auth_login_limit=3)) for _ in range(2)]
    async with apps[0].router.lifespan_context(apps[0]), apps[1].router.lifespan_context(apps[1]):
        # Lifespan-created clients remain independent, but test keys stay isolated.
        for app in apps:
            app.state.auth_rate_limiter = AuthRateLimiter(
                app.state.auth_rate_limiter.client, redis_limiters[0].namespace)
        async with AsyncClient(transport=ASGITransport(app=apps[0]), base_url='http://test') as first, \
            AsyncClient(transport=ASGITransport(app=apps[1]), base_url='http://test') as second:
            responses = await asyncio.gather(*[
                (first if index % 2 else second).post('/auth/login', json={})
                for index in range(20)])
    assert sum(response.status_code == 422 for response in responses) == 3
    assert sum(response.status_code == 429 for response in responses) == 17
    assert all(int(response.headers['retry-after']) > 0
        for response in responses if response.status_code == 429)


@pytest.mark.integration
async def test_redis_expiration_without_extending_rejected_window(redis_limiters):
    """Denials preserve the original deadline and the quota resets after expiry."""
    first, second = redis_limiters
    assert (await first.check('login', '192.0.2.1', 1, 1))[0] is True
    key = await anext(first.client.scan_iter(match=f'{first.namespace}:*'))
    original_deadline = await first.client.execute_command('PEXPIRETIME', key)
    assert (await second.check('login', '192.0.2.1', 1, 1)) == (False, 1)
    assert await first.client.execute_command('PEXPIRETIME', key) == original_deadline
    # Wait for observed expiry with a bound; don't assume exact scheduler timing.
    async with asyncio.timeout(5):
        while await first.client.exists(key):
            await asyncio.sleep(.02)
    assert (await second.check('login', '192.0.2.1', 1, 1))[0] is True
