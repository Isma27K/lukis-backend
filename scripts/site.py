"""The site owner and sign-up, from the server, for when the owner can't log in.

    python -m scripts.site show
    python -m scripts.site set-owner ada@example.com
    python -m scripts.site registration open|closed

There's deliberately no way to do this from the web: whoever can run this
already controls the server. To hand the site to a new account while sign-up
is closed, open it, let them register, set them as owner and close it again.
"""

import argparse
from datetime import UTC, datetime

from pymongo import MongoClient
from pymongo.database import Database

from data.database.site import SITE_ID
from function.auth import normalize_email
from utils.config import Settings


def set_owner(db: Database, email: str) -> None:
    user = db.users.find_one({"email": normalize_email(email)}, {"_id": 1})
    if user is None:
        raise SystemExit(f"No account with the email {email}")
    db.site.update_one(
        {"_id": SITE_ID},
        {
            "$set": {
                "owner_id": user["_id"],
                "updated_at": datetime.now(UTC),
                "updated_by": None,
            },
            "$unset": {"setup_code_hash": ""},
            "$setOnInsert": {"registration_open": True},
        },
        upsert=True,
    )


def set_registration(db: Database, registration_open: bool) -> None:
    db.site.update_one(
        {"_id": SITE_ID},
        {
            "$set": {
                "registration_open": registration_open,
                "updated_at": datetime.now(UTC),
                "updated_by": None,
            },
            "$setOnInsert": {"owner_id": None},
        },
        upsert=True,
    )


def describe(db: Database) -> str:
    site = db.site.find_one({"_id": SITE_ID})
    if site is None:
        return "Not set up yet; the API does that when it starts."
    owner = site["owner_id"] and db.users.find_one(
        {"_id": site["owner_id"]}, {"email": 1}
    )
    sign_up = "open to anyone" if site["registration_open"] else "invite-only"
    return (
        f"Owner: {owner['email'] if owner else 'none yet'}\n"
        f"Sign-up: {sign_up}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show", help="show the owner and whether sign-up is open")
    owner = commands.add_parser("set-owner", help="make an account the site owner")
    owner.add_argument("email")
    registration = commands.add_parser("registration", help="open or close sign-up")
    registration.add_argument("state", choices=["open", "closed"])
    args = parser.parse_args()

    settings = Settings()
    with MongoClient(settings.mongo_url) as client:
        db = client[settings.mongo_db]
        if args.command == "set-owner":
            set_owner(db, args.email)
        elif args.command == "registration":
            set_registration(db, args.state == "open")
        print(describe(db))


if __name__ == "__main__":
    main()
