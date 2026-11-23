from main import ensure_bucket as ensure_bucket_at_startup
from utils.config import Settings


async def test_health_pings_database(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_reports_storage_down(client, storage):
    storage.down = True

    response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"detail": "File storage unavailable"}


async def test_missing_bucket_is_created_at_startup(storage):
    # A fresh object store, like the one docker compose starts, has none.
    calls = []

    async def ensure_bucket():
        calls.append(True)
        return True

    storage.ensure_bucket = ensure_bucket
    await ensure_bucket_at_startup(storage, Settings(_env_file=None))

    assert calls == [True]


async def test_storage_down_at_startup_doesnt_stop_the_app(storage):
    storage.down = True

    # Logs a warning instead of raising.
    await ensure_bucket_at_startup(storage, Settings(_env_file=None))
