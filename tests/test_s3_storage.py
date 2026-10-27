import os
import uuid

import pytest

from utils.config import Settings
from utils.storage import S3Storage

# Talks to the real object store from .env, so it only runs when asked:
# LUKIS_S3_TESTS=1 pytest tests/test_s3_storage.py
pytestmark = pytest.mark.skipif(
    os.environ.get("LUKIS_S3_TESTS") != "1", reason="set LUKIS_S3_TESTS=1"
)


async def test_s3_round_trip():
    storage = S3Storage(Settings())
    prefix = f"tests/{uuid.uuid4().hex}/"
    try:
        await storage.ping()
        await storage.put(f"{prefix}a", b"hello", "image/png")
        await storage.copy(f"{prefix}a", f"{prefix}b")

        assert await storage.get(f"{prefix}b") == (b"hello", "image/png")
        assert await storage.get(f"{prefix}missing") is None
    finally:
        await storage.delete_prefix(prefix)

    assert await storage.get(f"{prefix}a") is None
