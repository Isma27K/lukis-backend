import io
import tarfile
import uuid
from datetime import UTC, datetime

import pytest
from bson import ObjectId
from pymongo import MongoClient

from scripts.backup import read_archive, write_archive
from utils.config import Settings


@pytest.fixture
def databases():
    """Two throwaway databases: one to back up, one to restore into."""
    client = MongoClient(Settings().mongo_url, tz_aware=True)
    names = [f"lukis_test_{uuid.uuid4().hex[:12]}" for _ in range(2)]
    yield [client[name] for name in names]
    for name in names:
        client.drop_database(name)
    client.close()


def test_backup_round_trip(databases):
    source, target = databases
    user_id = ObjectId()
    now = datetime.now(UTC).replace(microsecond=0)
    source.users.insert_one({"_id": user_id, "email": "ada@example.com", "at": now})
    source.scenes.insert_many(
        [{"_id": ObjectId(), "elements": [{"id": str(i)}]} for i in range(1200)]
    )

    archive = io.BytesIO()
    counts = write_archive(source, archive)
    assert counts == {"scenes": 1200, "users": 1}
    archive.seek(0)
    with tarfile.open(fileobj=archive, mode="r:gz") as tar:
        assert "manifest.json" in tar.getnames()

    archive.seek(0)
    assert read_archive(archive, target, drop=False) == counts
    # Same ids and types (ObjectId, datetime) as before.
    assert target.users.find_one({"_id": user_id}) == {
        "_id": user_id,
        "email": "ada@example.com",
        "at": now,
    }
    assert target.scenes.count_documents({}) == 1200

    # Won't mix into existing data unless told to replace it.
    archive.seek(0)
    with pytest.raises(SystemExit, match="Not empty: scenes, users"):
        read_archive(archive, target, drop=False)
    archive.seek(0)
    read_archive(archive, target, drop=True)
    assert target.scenes.count_documents({}) == 1200
