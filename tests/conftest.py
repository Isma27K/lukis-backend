from contextlib import AsyncExitStack

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
async def make_client(app):
    """Opens clients with separate cookie jars, i.e. separate users."""
    async with AsyncExitStack() as stack:

        async def make() -> AsyncClient:
            transport = ASGITransport(app=app)
            return await stack.enter_async_context(
                AsyncClient(transport=transport, base_url="http://test")
            )

        yield make


@pytest.fixture
async def client(make_client):
    return await make_client()
