from pydantic import BaseModel

from data.database.user import UserDocument


class UserResponse(BaseModel):
    id: str
    email: str
    name: str

    @classmethod
    def from_document(cls, user: UserDocument) -> "UserResponse":
        return cls(id=str(user["_id"]), email=user["email"], name=user["name"])
