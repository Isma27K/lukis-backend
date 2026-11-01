from typing import Annotated

from pydantic import BaseModel, StringConstraints

from data.request.auth import Name, Password
from data.request.drawing import DataUrl

# Checked against the stored hash, so only its length is limited.
CurrentPassword = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class UpdateProfileRequest(BaseModel):
    name: Name


class UploadAvatarRequest(BaseModel):
    data_url: DataUrl


class ChangePasswordRequest(BaseModel):
    # Left out by accounts that don't have a password yet.
    current_password: CurrentPassword | None = None
    new_password: Password


class DeleteAccountRequest(BaseModel):
    # Accounts without a password type their email address instead.
    password: CurrentPassword | None = None
    email: Annotated[str, StringConstraints(max_length=320)] | None = None
