"""Database backups to object storage.

    python -m scripts.backup run                 # one backup now
    python -m scripts.backup run --every-hours 24
    python -m scripts.backup list
    python -m scripts.backup restore backups/lukis-20261027T030000Z.tar.gz [--drop]

A backup is a .tar.gz holding each collection as BSON, the same format
mongodump writes, so it needs nothing but pymongo. Images and thumbnails
already live in object storage and aren't copied. Indexes are recreated by the
API when it starts.
"""

import argparse
import io
import json
import logging
import logging.config
import tarfile
import tempfile
import time
from datetime import UTC, datetime, timedelta
from typing import BinaryIO

import bson
from pymongo import MongoClient
from pymongo.database import Database

from utils.config import Settings
from utils.storage import S3Storage

logger = logging.getLogger("lukis.backup")

PREFIX = "backups/"
BATCH = 500


def write_archive(db: Database, out: BinaryIO) -> dict[str, int]:
    """Writes every collection into `out` and returns their document counts."""
    counts: dict[str, int] = {}
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name in sorted(db.list_collection_names()):
            if name.startswith("system."):
                continue
            # Spooled to disk, so big collections don't have to fit in memory.
            with tempfile.TemporaryFile() as dump:
                count = 0
                for document in db[name].find(batch_size=BATCH):
                    dump.write(bson.encode(document))
                    count += 1
                info = tarfile.TarInfo(f"{name}.bson")
                info.size = dump.tell()
                info.mtime = int(time.time())
                dump.seek(0)
                tar.addfile(info, dump)
            counts[name] = count

        manifest = json.dumps(
            {
                "database": db.name,
                "created_at": datetime.now(UTC).isoformat(),
                "collections": counts,
            },
            indent=2,
        ).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(manifest))
    return counts


def read_archive(source: BinaryIO, db: Database, drop: bool) -> dict[str, int]:
    """Restores every collection in the archive into `db`. Refuses to mix with
    existing data unless `drop` replaces it."""
    counts: dict[str, int] = {}
    with tarfile.open(fileobj=source, mode="r:gz") as tar:
        members = [
            m
            for m in tar.getmembers()
            if m.isfile() and m.name.endswith(".bson") and "/" not in m.name
        ]
        names = [m.name.removesuffix(".bson") for m in members]
        if not drop:
            busy = [n for n in names if db[n].estimated_document_count()]
            if busy:
                raise SystemExit(
                    f"Not empty: {', '.join(busy)}. Use --drop to replace them."
                )
        for member, name in zip(members, names):
            if drop:
                db[name].drop()
            batch: list[dict] = []
            count = 0
            for document in bson.decode_file_iter(tar.extractfile(member)):
                batch.append(document)
                if len(batch) == BATCH:
                    db[name].insert_many(batch)
                    count += len(batch)
                    batch = []
            if batch:
                db[name].insert_many(batch)
                count += len(batch)
            counts[name] = count
    return counts


def _bucket(settings: Settings) -> str:
    return settings.backup_bucket or settings.s3_bucket


def run_backup(settings: Settings) -> str:
    client = S3Storage(settings).client
    key = f"{PREFIX}{settings.mongo_db}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.tar.gz"
    with (
        MongoClient(settings.mongo_url) as mongo,
        tempfile.TemporaryFile() as archive,
    ):
        counts = write_archive(mongo[settings.mongo_db], archive)
        size = archive.tell()
        archive.seek(0)
        client.put_object(
            _bucket(settings), key, archive, size, content_type="application/gzip"
        )
    logger.info(
        f"Backed up {settings.mongo_db} to {key}",
        extra={"fields": {"key": key, "bytes": size, "collections": counts}},
    )
    prune(settings)
    return key


def list_backups(settings: Settings) -> list:
    client = S3Storage(settings).client
    backups = client.list_objects(_bucket(settings), PREFIX, recursive=True)
    return sorted(backups, key=lambda o: o.last_modified)


def prune(settings: Settings) -> None:
    """Deletes backups older than backup_keep_days, but never the newest."""
    client = S3Storage(settings).client
    cutoff = datetime.now(UTC) - timedelta(days=settings.backup_keep_days)
    for backup in list_backups(settings)[:-1]:
        if backup.last_modified < cutoff:
            client.remove_object(_bucket(settings), backup.object_name)
            logger.info(f"Deleted old backup {backup.object_name}")


def restore(settings: Settings, key: str, drop: bool) -> None:
    client = S3Storage(settings).client
    response = client.get_object(_bucket(settings), key)
    try:
        with tempfile.TemporaryFile() as archive:
            for chunk in response.stream(1024 * 1024):
                archive.write(chunk)
            archive.seek(0)
            with MongoClient(settings.mongo_url) as mongo:
                counts = read_archive(archive, mongo[settings.mongo_db], drop)
    finally:
        response.close()
        response.release_conn()
    logger.info(
        f"Restored {key} into {settings.mongo_db}",
        extra={"fields": {"collections": counts}},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--log-config", help="a logging config, e.g. logging.json for JSON logs"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="back up the database now")
    run.add_argument(
        "--every-hours", type=float, help="keep running, backing up this often"
    )
    commands.add_parser("list", help="list stored backups")
    restore_cmd = commands.add_parser("restore", help="restore a stored backup")
    restore_cmd.add_argument("key", help="as shown by list")
    restore_cmd.add_argument(
        "--drop", action="store_true", help="replace collections that have data"
    )
    args = parser.parse_args()

    if args.log_config:
        with open(args.log_config) as config:
            logging.config.dictConfig(json.load(config))
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = Settings()

    if args.command == "list":
        for backup in list_backups(settings):
            size = f"{backup.size:,} bytes"
            print(f"{backup.object_name}  {size:>16}  {backup.last_modified}")
    elif args.command == "restore":
        restore(settings, args.key, args.drop)
    elif args.every_hours:
        while True:
            try:
                run_backup(settings)
            except Exception:
                # Try again next time rather than stop backing up for good.
                logger.exception("Backup failed")
            time.sleep(args.every_hours * 3600)
    else:
        run_backup(settings)


if __name__ == "__main__":
    main()
