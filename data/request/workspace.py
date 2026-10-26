from typing import Annotated

from pydantic import BaseModel, EmailStr, StringConstraints

from data.database.workspace import Role

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]


class CreateWorkspaceRequest(BaseModel):
    name: Name


class UpdateWorkspaceRequest(BaseModel):
    name: Name


class UpdateMemberRequest(BaseModel):
    role: Role


class CreateInviteRequest(BaseModel):
    email: EmailStr
    role: Role = "editor"
