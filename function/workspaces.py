from datetime import UTC, datetime

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from data.database.user import UserDocument
from data.database.workspace import MembershipDocument, Role, WorkspaceDocument
from function.access import ROLE_RANK, parse_id, require_role
from function.errors import ConflictError, ForbiddenError, NotFoundError


async def create_workspace(
    db: AsyncDatabase, user: UserDocument, name: str, personal: bool = False
) -> tuple[WorkspaceDocument, MembershipDocument]:
    now = datetime.now(UTC)
    workspace: WorkspaceDocument = {
        "_id": ObjectId(),
        "name": name,
        "owner_id": user["_id"],
        "personal": personal,
        "created_at": now,
        "deleted_at": None,
    }
    membership: MembershipDocument = {
        "_id": ObjectId(),
        "workspace_id": workspace["_id"],
        "user_id": user["_id"],
        "role": "owner",
        "created_at": now,
    }
    await db.workspaces.insert_one(workspace)
    await db.memberships.insert_one(membership)
    return workspace, membership


async def list_workspaces(
    db: AsyncDatabase, user: UserDocument
) -> list[tuple[WorkspaceDocument, Role]]:
    memberships = await db.memberships.find({"user_id": user["_id"]}).to_list()
    roles = {member["workspace_id"]: member["role"] for member in memberships}
    workspaces = await db.workspaces.find(
        {"_id": {"$in": list(roles)}, "deleted_at": None}
    ).to_list()
    # Personal workspace first, then the rest by name.
    workspaces.sort(key=lambda w: (not w["personal"], w["name"].lower()))
    return [(workspace, roles[workspace["_id"]]) for workspace in workspaces]


async def get_workspace(
    db: AsyncDatabase, workspace_id: str, user: UserDocument, min_role: Role
) -> tuple[WorkspaceDocument, MembershipDocument]:
    workspace_oid = parse_id(workspace_id)
    membership = await require_role(db, workspace_oid, user, min_role)
    workspace = await db.workspaces.find_one(
        {"_id": workspace_oid, "deleted_at": None}
    )
    if workspace is None:
        raise NotFoundError
    return workspace, membership


async def rename_workspace(
    db: AsyncDatabase, workspace: WorkspaceDocument, name: str
) -> WorkspaceDocument:
    await db.workspaces.update_one({"_id": workspace["_id"]}, {"$set": {"name": name}})
    return {**workspace, "name": name}


async def delete_workspace(db: AsyncDatabase, workspace: WorkspaceDocument) -> None:
    if workspace["personal"]:
        raise ForbiddenError("Your personal workspace can't be deleted")
    await db.workspaces.update_one(
        {"_id": workspace["_id"]}, {"$set": {"deleted_at": datetime.now(UTC)}}
    )
    # With no memberships left, nobody can reach the workspace or its drawings.
    await db.memberships.delete_many({"workspace_id": workspace["_id"]})
    await db.invites.delete_many({"workspace_id": workspace["_id"]})


async def list_members(
    db: AsyncDatabase, workspace_id: ObjectId
) -> list[tuple[MembershipDocument, UserDocument]]:
    memberships = await db.memberships.find({"workspace_id": workspace_id}).to_list()
    user_ids = [membership["user_id"] for membership in memberships]
    users = {
        user["_id"]: user
        for user in await db.users.find({"_id": {"$in": user_ids}}).to_list()
    }
    members = [(m, users[m["user_id"]]) for m in memberships if m["user_id"] in users]
    # Highest role first, then by name.
    members.sort(key=lambda m: (-ROLE_RANK[m[0]["role"]], m[1]["name"].lower()))
    return members


def _check_can_manage(
    actor: MembershipDocument, target: MembershipDocument, new_role: Role | None = None
) -> None:
    """Owners manage everyone. Admins manage editors and viewers, and can
    promote them as far as admin."""
    if actor["role"] == "owner":
        return
    if actor["role"] != "admin":
        raise ForbiddenError
    if target["role"] not in ("editor", "viewer") or new_role == "owner":
        raise ForbiddenError("Only owners can manage owners and admins")


async def _get_membership(
    db: AsyncDatabase, workspace_id: ObjectId, user_id: str
) -> MembershipDocument:
    membership = await db.memberships.find_one(
        {"workspace_id": workspace_id, "user_id": parse_id(user_id)}
    )
    if membership is None:
        raise NotFoundError
    return membership


async def _ensure_another_owner(
    db: AsyncDatabase, workspace_id: ObjectId, member: MembershipDocument
) -> None:
    """Called before `member` stops being an owner."""
    if member["role"] != "owner":
        return
    owners = await db.memberships.count_documents(
        {"workspace_id": workspace_id, "role": "owner"}
    )
    if owners < 2:
        raise ConflictError(
            "A workspace needs at least one owner. Make someone else an owner first."
        )


async def change_member_role(
    db: AsyncDatabase,
    workspace: WorkspaceDocument,
    actor: MembershipDocument,
    target_user_id: str,
    role: Role,
) -> tuple[MembershipDocument, UserDocument]:
    target = await _get_membership(db, workspace["_id"], target_user_id)
    _check_can_manage(actor, target, role)
    if role != "owner":
        await _ensure_another_owner(db, workspace["_id"], target)
    await db.memberships.update_one({"_id": target["_id"]}, {"$set": {"role": role}})
    user = await db.users.find_one({"_id": target["user_id"]})
    return {**target, "role": role}, user


async def remove_member(
    db: AsyncDatabase,
    workspace: WorkspaceDocument,
    actor: MembershipDocument,
    target_user_id: str,
) -> None:
    target = await _get_membership(db, workspace["_id"], target_user_id)
    # Anyone can leave; removing someone else takes the right to manage them.
    if target["user_id"] != actor["user_id"]:
        _check_can_manage(actor, target)
    await _ensure_another_owner(db, workspace["_id"], target)
    await db.memberships.delete_one({"_id": target["_id"]})
