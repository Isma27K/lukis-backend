from bson import ObjectId
from bson.errors import InvalidId
from pymongo.asynchronous.database import AsyncDatabase

from data.database.user import UserDocument
from data.database.workspace import MembershipDocument, Role
from function.errors import ForbiddenError, NotFoundError

ROLE_RANK: dict[Role, int] = {
    "viewer": 0,
    "editor": 1,
    "admin": 2,
    "owner": 3
}


def parse_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except InvalidId as error:
        raise NotFoundError from error


async def require_role(
    db: AsyncDatabase, workspace_id: ObjectId, user: UserDocument, min_role: Role
) -> MembershipDocument:
    membership = await db.memberships.find_one(
        {"workspace_id": workspace_id, "user_id": user["_id"]}
    )
    # Non-members get "not found" rather than "forbidden", so ids can't be probed.
    if membership is None:
        raise NotFoundError
    if ROLE_RANK[membership["role"]] < ROLE_RANK[min_role]:
        raise ForbiddenError
    return membership
