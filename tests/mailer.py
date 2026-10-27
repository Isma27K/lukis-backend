import re
from urllib.parse import parse_qs, urlsplit

from utils.mail import Email


class MemoryMailer:
    """Keeps sent emails for tests to read."""

    def __init__(self) -> None:
        self.sent: list[Email] = []
        # Set to make sending fail, as if the mail server were down.
        self.down = False

    async def send(self, email: Email) -> None:
        if self.down:
            raise ConnectionRefusedError("mail server down")
        self.sent.append(email)

    def to(self, address: str) -> list[Email]:
        return [email for email in self.sent if email.to == address]


def link_in(email: Email) -> tuple[str, dict[str, str]]:
    """The path and query of the (only) link in an email."""
    url = urlsplit(re.search(r"https?://\S+", email.text).group())
    return url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
