"""Object storage for drawing images, thumbnails and profile pictures.

Production uses an S3-compatible server (RustFS) through the minio client;
tests swap in an in-memory store. Objects are keyed by drawing or user, so
permanently deleting one only has to clear its prefix.
"""

import asyncio
import io
from typing import Annotated, Protocol
from urllib.parse import urlsplit

import urllib3
from fastapi import Depends, Request
from minio import Minio
from minio.commonconfig import CopySource
from minio.deleteobjects import DeleteObject
from minio.error import MinioException, S3Error

from utils.config import Settings


class StorageError(Exception):
    """The object store can't be reached or refused the request."""


class Storage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> tuple[bytes, str] | None:
        """The object's bytes and content type, or None if it doesn't exist."""

    async def copy(self, source: str, target: str) -> None: ...

    async def delete_prefix(self, prefix: str) -> None: ...

    async def ping(self) -> None:
        """Raises StorageError unless the bucket is reachable."""


def file_key(drawing_id: object, file_id: str) -> str:
    return f"drawings/{drawing_id}/files/{file_id}"


def thumbnail_key(drawing_id: object, theme: str) -> str:
    return f"drawings/{drawing_id}/thumbnail-{theme}"


def drawing_prefix(drawing_id: object) -> str:
    return f"drawings/{drawing_id}/"


def avatar_key(user_id: object) -> str:
    return f"users/{user_id}/avatar"


def user_prefix(user_id: object) -> str:
    return f"users/{user_id}/"


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        endpoint = urlsplit(settings.s3_endpoint)
        self.bucket = settings.s3_bucket
        self.client = Minio(
            endpoint.netloc,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=endpoint.scheme == "https",
            # Known up front, so the client never asks the server for it.
            region=settings.s3_region,
            # minio's default waits minutes to connect; fail fast instead.
            http_client=urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=5, read=30),
                maxsize=20,
                retries=urllib3.Retry(
                    total=3,
                    backoff_factor=0.2,
                    status_forcelist=[500, 502, 503, 504],
                ),
            ),
        )

    async def _run(self, function, *args):
        # The minio client blocks, so keep it off the event loop.
        try:
            return await asyncio.to_thread(function, *args)
        except (MinioException, urllib3.exceptions.HTTPError) as error:
            raise StorageError(str(error)) from error

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await self._run(
            lambda: self.client.put_object(
                self.bucket, key, io.BytesIO(data), len(data), content_type
            )
        )

    async def get(self, key: str) -> tuple[bytes, str] | None:
        def read() -> tuple[bytes, str] | None:
            try:
                response = self.client.get_object(self.bucket, key)
            except S3Error as error:
                if error.code == "NoSuchKey":
                    return None
                raise
            try:
                content_type = response.headers.get(
                    "content-type", "application/octet-stream"
                )
                return response.read(), content_type
            finally:
                response.close()
                response.release_conn()

        return await self._run(read)

    async def copy(self, source: str, target: str) -> None:
        await self._run(
            lambda: self.client.copy_object(
                self.bucket, target, CopySource(self.bucket, source)
            )
        )

    async def delete_prefix(self, prefix: str) -> None:
        def delete() -> None:
            objects = self.client.list_objects(self.bucket, prefix, recursive=True)
            errors = self.client.remove_objects(
                self.bucket, (DeleteObject(o.object_name) for o in objects)
            )
            # remove_objects is lazy: nothing is deleted until this is consumed.
            failed = list(errors)
            if failed:
                raise StorageError(f"Couldn't delete {len(failed)} objects")

        await self._run(delete)

    async def ping(self) -> None:
        if not await self._run(self.client.bucket_exists, self.bucket):
            raise StorageError(f"Bucket {self.bucket!r} doesn't exist")


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


ObjectStorage = Annotated[Storage, Depends(get_storage)]
