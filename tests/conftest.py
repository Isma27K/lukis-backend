import uuid
from contextlib import AsyncExitStack

import pytest
from httpx import ASGITransport, AsyncClient

from main import create_app
from tests.helpers import SETUP_CODE
from tests.mailer import MemoryMailer
from tests.storage import MemoryStorage
from utils.config import Settings


@pytest.fixture
def storage():
    return MemoryStorage()


@pytest.fixture
def mailer():
    """Email is on in tests unless a module overrides this to return None."""
    return MemoryMailer()


@pytest.fixture
def oauth_providers():
    """None set up, even if .env has client IDs; test_oauth.py sets fakes."""
    return {}


@pytest.fixture
async def app(storage, mailer, oauth_providers):
    # A fresh database per test, so test runs that overlap (say, one in the
    # IDE and one in a terminal) can't drop each other's data.
    db_name = f"lukis_test_{uuid.uuid4().hex[:12]}"
    # No SMTP host, so a real mail server is never used even if .env has one.
    settings = Settings(mongo_db=db_name, smtp_host="", setup_code=SETUP_CODE)
    app = create_app(settings, storage, mailer, oauth_providers)
    # httpx's ASGITransport doesn't run lifespan events, so run them here.
    async with app.router.lifespan_context(app):
        yield app
        await app.state.db.client.drop_database(db_name)


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
