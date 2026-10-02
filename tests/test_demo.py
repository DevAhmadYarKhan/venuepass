"""Static customer assets are public and do not require a database connection."""

from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


async def test_demo_assets_and_api_routes():
    """Serve the demo at its own prefix while retaining health, OpenAPI, and Swagger."""
    app = create_app(Settings(_env_file=None, jwt_secret='test-signing-secret-that-is-at-least-32-characters',
        database_url='postgresql+psycopg://unused:unused@127.0.0.1:1/unavailable'))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        for path, content_type in [('/demo/', 'text/html'), ('/demo/styles.css', 'text/css'),
                                   ('/demo/app.js', 'javascript'), ('/demo/api.js', 'javascript'), ('/demo/ui.js', 'javascript'), ('/demo/auth.js', 'javascript')]:
            response = await client.get(path)
            assert response.status_code == 200
            assert content_type in response.headers['content-type']
        assert (await client.get('/demo/missing')).status_code == 404
        assert (await client.get('/health')).json() == {'status': 'ok'}
        assert (await client.get('/docs')).status_code == 200
        assert 'get' in (await client.get('/openapi.json')).json()['paths']['/events']
