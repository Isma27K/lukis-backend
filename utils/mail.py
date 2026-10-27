"""Outgoing email over SMTP. Optional: without an SMTP server the app runs
with its email features (verification, password reset, invite emails) off."""

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Annotated, Protocol

from fastapi import Depends, Request

from utils.config import Settings

logger = logging.getLogger("lukis.mail")


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str


class Mailer(Protocol):
    async def send(self, email: Email) -> None: ...


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _message(self, email: Email) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self.settings.mail_from
        message["To"] = email.to
        message["Subject"] = email.subject
        message.set_content(email.text)
        return message

    def _send(self, email: Email) -> None:
        s = self.settings
        context = ssl.create_default_context()
        if s.smtp_security == "ssl":
            smtp = smtplib.SMTP_SSL(
                s.smtp_host, s.smtp_port, timeout=15, context=context
            )
        else:
            smtp = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15)
        with smtp:
            if s.smtp_security == "starttls":
                smtp.starttls(context=context)
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password)
            smtp.send_message(self._message(email))

    async def send(self, email: Email) -> None:
        # smtplib blocks, so keep it off the event loop.
        await asyncio.to_thread(self._send, email)


def create_mailer(settings: Settings) -> Mailer | None:
    """None when no SMTP server is set up: then everything that needs email
    (verification, password reset, invite emails) is switched off."""
    if settings.smtp_host:
        return SmtpMailer(settings)
    logger.info("SMTP_HOST isn't set, so email features are off")
    return None


async def send_safely(mailer: Mailer, email: Email) -> None:
    """For background tasks, after the response is sent: a failed email is
    logged rather than lost silently."""
    try:
        await mailer.send(email)
    except Exception:
        logger.exception(
            f"Couldn't send email to {email.to}",
            extra={"fields": {"to": email.to, "subject": email.subject}},
        )


def get_mailer(request: Request) -> Mailer | None:
    return request.app.state.mailer


# None means email is off; see create_mailer.
AppMailer = Annotated[Mailer | None, Depends(get_mailer)]
