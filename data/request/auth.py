from typing import Annotated

from pydantic import AfterValidator, BaseModel, EmailStr, StringConstraints

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]
Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]
Token = Annotated[str, StringConstraints(min_length=1, max_length=128)]


def _local_path(value: str) -> str | None:
    # Only paths on this site, so a link in an email can't lead elsewhere.
    if not value.startswith("/") or value.startswith(("//", "/\\")):
        return None
    return value


# Where to send someone after they follow a verification link, e.g. back to
# the invite they were about to accept. Anything else is dropped.
NextPath = Annotated[
    str, StringConstraints(max_length=512), AfterValidator(_local_path)
]


class RegisterRequest(BaseModel):
    email: EmailStr
    name: Name
    password: Password
    next: NextPath | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class ResendVerificationRequest(BaseModel):
    next: NextPath | None = None


class VerifyEmailRequest(BaseModel):
    token: Token


class PasswordResetRequest(BaseModel):
    email: EmailStr


class ConfirmPasswordResetRequest(BaseModel):
    token: Token
    password: Password
