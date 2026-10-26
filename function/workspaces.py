from pymongo.asynchronous.database import AsyncDatabase

from data.database.user import UserDocument
from data.database.workspace import Role, WorkspaceDocument


async def list_workspaces(
    db: AsyncDatabase, user: UserDocument
) -> list[tuple[WorkspaceDocument, Role]]:
    memberships = await db.memberships.find({"user_id": user["_id"]}).to_list()
    roles = {member["workspace_id"]: member["role"] for member in memberships}
    workspaces = await db.workspaces.find({"_id": {"$in": list(roles)}}).to_list()
    # Personal workspace first, then the rest by name.
    workspaces.sort(key=lambda w: (not w["personal"], w["name"].lower()))
    return [(workspace, roles[workspace["_id"]]) for workspace in workspaces]
