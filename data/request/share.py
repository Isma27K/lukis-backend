from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from data.database.share import ShareAccess

SharePassword = Annotated[str, StringConstraints(min_length=4, max_length=128)]


class ShareSettingsRequest(BaseModel):
    enabled: bool
    access: ShareAccess = "view"
    # None for a link that never expires.
    expires_at: datetime | None = None
    # Leave it out to keep the current password; null removes it.
    password: SharePassword | None = None


class UnlockShareRequest(BaseModel):
    password: Annotated[str, StringConstraints(max_length=128)]
