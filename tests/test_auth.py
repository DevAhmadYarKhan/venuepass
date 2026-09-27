"""Verify account persistence, password privacy, and bearer authentication failures."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from sqlalchemy import select

from app.auth import password_hasher
from app.models import User

pytestmark = pytest.mark.integration
SECRET = "test-signing-secret-that-is-at-least-32-characters"
PASSWORD = " a long password with spaces "


async def test_registration_login_and_current_user(event_client):
    """Normalize email, preserve the password, and authenticate the created identity."""
    client, connection = event_client
    response = await client.post('/auth/register', json={'email': ' User@Example.com ', 'password': PASSWORD})
    assert response.status_code == 201
    user = response.json()
    assert set(user) == {'id', 'email', 'created_at'}
    assert user['email'] == 'user@example.com'
    hashed = await connection.scalar(select(User.password_hash))
    assert hashed.startswith('$argon2id$') and hashed != PASSWORD
    assert password_hasher.verify(PASSWORD, hashed)
    duplicate = await client.post('/auth/register', json={'email': 'USER@example.com', 'password': PASSWORD})
    assert duplicate.status_code == 409
    login = await client.post('/auth/login', json={'email': ' USER@EXAMPLE.COM ', 'password': PASSWORD})
    assert login.status_code == 200
    token = login.json()
    assert token['token_type'] == 'bearer' and token['expires_in'] == 1800
    claims = jwt.decode(token['access_token'], SECRET, algorithms=['HS256'])
    assert set(claims) == {'sub', 'iat', 'exp'}
    assert claims['exp'] - claims['iat'] == 1800
    me = await client.get('/users/me', headers={'Authorization': 'Bearer ' + token['access_token']})
    assert me.status_code == 200 and me.json() == user
    # Trimming passwords would incorrectly allow this request.
    wrong = await client.post('/auth/login', json={'email': user['email'], 'password': PASSWORD.strip()})
    unknown = await client.post('/auth/login', json={'email': 'missing@example.com', 'password': PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


@pytest.mark.parametrize('body', [
    {'email': 'invalid', 'password': PASSWORD},
    {'email': 'user@example.com', 'password': 'short-secret'},
    {'email': 'user@example.com', 'password': 'x' * 129},
    {'email': 'user@example.com', 'password': {'secret': 'nested-secret'}},
])
async def test_validation_does_not_echo_passwords(event_client, body):
    """Even malformed credential values must not appear in validation errors."""
    client, _ = event_client
    response = await client.post('/auth/register', json=body)
    assert response.status_code == 422
    for error in response.json()['detail']:
        assert set(error) == {'loc', 'msg', 'type'}
    assert 'nested-secret' not in response.text and PASSWORD not in response.text


@pytest.mark.parametrize('kind', ['missing', 'malformed', 'tampered', 'expired', 'sub', 'iat', 'exp', 'unknown', 'bad_uuid', 'algorithm'])
async def test_invalid_tokens(event_client, kind):
    """All authentication failures produce the same HTTP bearer challenge."""
    client, _ = event_client
    now = datetime.now(timezone.utc)
    claims = {'sub': str(uuid4()), 'iat': now, 'exp': now + timedelta(minutes=30)}
    if kind in ('sub', 'iat', 'exp'):
        del claims[kind]
    if kind == 'expired':
        claims['exp'] = now - timedelta(seconds=1)
    if kind == 'bad_uuid':
        claims['sub'] = 'invalid-uuid'
    token = jwt.encode(claims, SECRET if kind != 'tampered' else 'wrong-secret-that-is-at-least-32-characters', algorithm='HS384' if kind == 'algorithm' else 'HS256')
    if kind == 'malformed':
        token = 'not.a.token'
    headers = {} if kind == 'missing' else {'Authorization': 'Bearer ' + token}
    response = await client.get('/users/me', headers=headers)
    assert response.status_code == 401
    assert response.headers['www-authenticate'] == 'Bearer'


async def test_concurrent_registration():
    """Independent committing sessions must create only one account per email."""
    import asyncio
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import delete, func
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.config import Settings
    from app.main import create_app

    settings = Settings()
    assert settings.test_database_url is not None
    url = str(settings.test_database_url)
    assert make_url(url).database == 'venuepass_db_test'
    email = f'race-{uuid4()}@example.com'
    app = create_app(Settings(database_url=url, jwt_secret=SECRET, _env_file=None))
    engine = create_async_engine(url)
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                responses = await asyncio.gather(*[
                    client.post('/auth/register', json={'email': email, 'password': PASSWORD})
                    for _ in range(2)
                ])
                assert sorted(response.status_code for response in responses) == [201, 409]
        async with engine.connect() as connection:
            assert await connection.scalar(select(func.count()).select_from(User).where(User.email == email)) == 1
    finally:
        # This concurrency test needs real commits, so remove only its unique account.
        async with engine.begin() as connection:
            await connection.execute(delete(User).where(User.email == email))
        await engine.dispose()
