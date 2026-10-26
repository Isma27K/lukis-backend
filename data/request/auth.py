from typing import Annotated

from pydantic import BaseModel, EmailStr, StringConstraints

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]


class RegisterRequest(BaseModel):
    email: EmailStr
    name: Name
    password: Annotated[str, StringConstraints(min_length=8, max_length=128)]


class LoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]
