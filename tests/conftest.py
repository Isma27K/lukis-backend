import pytest
from httpx import ASGITransport, AsyncClient

from main import create_app
from utils.config import Settings

TEST_DB = "lukis_test"


@pytest.fixture
async def app():
    app = create_app(Settings(mongo_db=TEST_DB))
    # httpx's ASGITransport doesn't run lifespan events, so run them here.
    async with app.router.lifespan_context(app):
        yield app
        await app.state.db.client.drop_database(TEST_DB)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
