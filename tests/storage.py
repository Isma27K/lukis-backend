from utils.storage import StorageError


class MemoryStorage:
    """Stands in for S3 in tests, so they need no object store."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        # Set to make every call fail, as if the store were down.
        self.down = False

    def _check(self) -> None:
        if self.down:
            raise StorageError("down")

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self._check()
        self.objects[key] = (data, content_type)

    async def get(self, key: str) -> tuple[bytes, str] | None:
        self._check()
        return self.objects.get(key)

    async def copy(self, source: str, target: str) -> None:
        self._check()
        self.objects[target] = self.objects[source]

    async def delete_prefix(self, prefix: str) -> None:
        self._check()
        for key in [key for key in self.objects if key.startswith(prefix)]:
            del self.objects[key]

    async def ping(self) -> None:
        self._check()
