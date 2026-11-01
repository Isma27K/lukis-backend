"""The emails Lukis sends. Plain text, so they read the same everywhere."""

from urllib.parse import urlencode

from data.database.workspace import Role
from utils.mail import Email

ROLE_NAMES: dict[Role, str] = {
    "owner": "an owner",
    "admin": "an admin",
    "editor": "an editor",
    "viewer": "a viewer",
}


def _link(public_url: str, path: str, **query: str | None) -> str:
    params = urlencode({k: v for k, v in query.items() if v})
    return f"{public_url}{path}" + (f"?{params}" if params else "")


def verification_email(
    public_url: str, to: str, name: str, token: str, next_path: str | None
) -> Email:
    link = _link(public_url, "/verify-email", token=token, next=next_path)
    return Email(
        to=to,
        subject="Confirm your email for Lukis",
        text=(
            f"Hi {name},\n\n"
            f"Confirm your email address by opening this link:\n\n{link}\n\n"
            "The link works for 48 hours. If you didn't create a Lukis "
            "account, you can ignore this email.\n"
        ),
    )


def password_reset_email(public_url: str, to: str, name: str, token: str) -> Email:
    link = _link(public_url, "/reset-password", token=token)
    return Email(
        to=to,
        subject="Reset your Lukis password",
        text=(
            f"Hi {name},\n\n"
            f"Choose a new password here:\n\n{link}\n\n"
            "The link works for 1 hour and only once. If you didn't ask to "
            "reset your password, ignore this email; it stays the same.\n"
        ),
    )


def password_changed_email(
    public_url: str, to: str, name: str, added: bool = False
) -> Email:
    """`added` when an account that logged in with Google and the like
    didn't have a password before."""
    link = _link(public_url, "/forgot-password")
    what = "now has a password" if added else "had its password changed"
    return Email(
        to=to,
        subject=(
            "A password was added to your Lukis account"
            if added
            else "Your Lukis password was changed"
        ),
        text=(
            f"Hi {name},\n\n"
            f"Your Lukis account {what} just now, and every other device was "
            "logged out.\n\n"
            "If this wasn't you, reset your password here right away:\n\n"
            f"{link}\n"
        ),
    )


def account_connected_email(
    public_url: str, to: str, name: str, provider: str
) -> Email:
    link = _link(public_url, "/account")
    return Email(
        to=to,
        subject=f"{provider} was connected to your Lukis account",
        text=(
            f"Hi {name},\n\n"
            f"A {provider} account was just connected to your Lukis account, "
            "and can now be used to log in.\n\n"
            "If this wasn't you, disconnect it and change your password "
            f"under Connected accounts:\n\n{link}\n"
        ),
    )


def account_deleted_email(to: str, name: str) -> Email:
    return Email(
        to=to,
        subject="Your Lukis account was deleted",
        text=(
            f"Hi {name},\n\n"
            "Your Lukis account and your personal drawings have been deleted. "
            "Drawings you made in shared workspaces stay with those "
            "workspaces.\n\n"
            "Thanks for drawing with us.\n"
        ),
    )


def signup_invite_email(public_url: str, to: str, inviter: str, token: str) -> Email:
    link = _link(public_url, "/register", invite=token)
    return Email(
        to=to,
        subject=f"{inviter} invited you to Lukis",
        text=(
            f"{inviter} invited you to create an account on Lukis.\n\n"
            f"Sign up here:\n\n{link}\n\n"
            f"The invite works for 7 days, only for {to}.\n"
        ),
    )


def invite_email(
    public_url: str,
    to: str,
    inviter: str,
    workspace: str,
    role: Role,
    token: str,
) -> Email:
    link = _link(public_url, f"/invite/{token}")
    return Email(
        to=to,
        subject=f"{inviter} invited you to {workspace} on Lukis",
        text=(
            f"{inviter} invited you to join the {workspace} workspace on "
            f"Lukis as {ROLE_NAMES[role]}.\n\n"
            f"Join here:\n\n{link}\n\n"
            f"The invite works for 7 days, only for {to}. If you don't have "
            "an account yet, create one with this address.\n"
        ),
    )
