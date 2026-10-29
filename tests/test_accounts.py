import json
import logging
from datetime import UTC, datetime, timedelta

from tests.helpers import PASSWORD, SETUP_CODE, confirm_email, signup
from tests.mailer import link_in
from utils.logs import JsonFormatter


async def me(client) -> dict:
    return (await client.get("/auth/me")).json()


async def test_features_say_email_is_on(client):
    features = (await client.get("/features")).json()
    assert features == {"email": True, "registration": "setup"}


async def test_signup_sends_a_verification_link(make_client, mailer):
    client = await make_client()
    response = await client.post(
        "/auth/register",
        json={
            "email": "Ada@Example.com",
            "name": "Ada",
            "password": PASSWORD,
            "next": "/invite/abc",
            "setup_code": SETUP_CODE,
        },
    )
    assert response.json()["email_verified"] is False

    [email] = mailer.to("ada@example.com")
    path, query = link_in(email)
    assert email.subject == "Confirm your email for Lukis"
    assert path == "/verify-email"
    assert query["next"] == "/invite/abc"

    # The link works from any browser, logged in or not, and only once.
    elsewhere = await make_client()
    verified = await elsewhere.post("/auth/verify-email", json=query)
    assert verified.status_code == 204
    assert (await me(client))["email_verified"] is True
    again = await elsewhere.post("/auth/verify-email", json=query)
    assert again.status_code == 400
    assert "invalid or has expired" in again.json()["detail"]


async def test_resending_replaces_the_link(client, mailer):
    await signup(client)
    first = link_in(mailer.sent[-1])[1]["token"]

    # Links to other sites are dropped from what's emailed.
    resent = await client.post(
        "/auth/verify-email/resend", json={"next": "//evil.example"}
    )
    assert resent.status_code == 204
    path, query = link_in(mailer.sent[-1])
    assert "next" not in query

    old = await client.post("/auth/verify-email", json={"token": first})
    assert old.status_code == 400
    await confirm_email(client, mailer, "ada@example.com")
    # Nothing more to confirm.
    sent = len(mailer.sent)
    await client.post("/auth/verify-email/resend", json={})
    assert len(mailer.sent) == sent


async def test_expired_links_do_nothing(app, client, mailer):
    await signup(client)
    token = link_in(mailer.sent[-1])[1]["token"]
    await app.state.db.email_tokens.update_many(
        {}, {"$set": {"expires_at": datetime.now(UTC) - timedelta(minutes=1)}}
    )

    response = await client.post("/auth/verify-email", json={"token": token})

    assert response.status_code == 400
    assert (await me(client))["email_verified"] is False


async def test_password_reset(make_client, mailer):
    ada, stranger = await make_client(), await make_client()
    await signup(ada)

    # Same answer for unknown addresses, and no email.
    unknown = await stranger.post(
        "/auth/password-reset", json={"email": "nobody@example.com"}
    )
    assert unknown.status_code == 204
    assert mailer.to("nobody@example.com") == []

    asked = await stranger.post(
        "/auth/password-reset", json={"email": "ADA@example.com"}
    )
    assert asked.status_code == 204
    email = mailer.to("ada@example.com")[-1]
    path, query = link_in(email)
    assert (email.subject, path) == ("Reset your Lukis password", "/reset-password")

    too_short = await stranger.post(
        "/auth/password-reset/confirm", json={**query, "password": "short"}
    )
    assert too_short.status_code == 422
    reset = await stranger.post(
        "/auth/password-reset/confirm", json={**query, "password": "a new password"}
    )
    assert reset.status_code == 204

    # Logged out everywhere; only the new password works now.
    assert (await ada.get("/auth/me")).status_code == 401
    old = await ada.post(
        "/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    assert old.status_code == 401
    new = await ada.post(
        "/auth/login", json={"email": "ada@example.com", "password": "a new password"}
    )
    assert new.status_code == 200
    # Getting the email proved the address is hers.
    assert new.json()["email_verified"] is True
    reused = await stranger.post(
        "/auth/password-reset/confirm", json={**query, "password": "another one"}
    )
    assert reused.status_code == 400


async def test_login_failures_lock_the_account_for_a_while(make_client):
    ada, attacker = await make_client(), await make_client()
    await signup(ada)
    await ada.post("/auth/logout", json={})

    for _ in range(10):
        wrong = await attacker.post(
            "/auth/login", json={"email": "ada@example.com", "password": "guess"}
        )
        assert wrong.status_code == 401
    locked = await ada.post(
        "/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )

    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 0
    assert "Too many attempts" in locked.json()["detail"]
    # Other accounts aren't affected.
    other = await attacker.post(
        "/auth/login", json={"email": "bob@example.com", "password": "guess"}
    )
    assert other.status_code == 401


async def test_signups_are_limited_per_address(client):
    for i in range(10):
        await signup(client, email=f"user{i}@example.com")

    response = await client.post(
        "/auth/register",
        json={"email": "one@example.com", "name": "One", "password": PASSWORD},
    )

    assert response.status_code == 429


async def test_reset_emails_are_limited_per_address(client, mailer):
    await signup(client)

    for _ in range(5):
        response = await client.post(
            "/auth/password-reset", json={"email": "ada@example.com"}
        )
        assert response.status_code == 204

    resets = [e for e in mailer.to("ada@example.com") if "Reset" in e.subject]
    assert len(resets) == 3


async def test_a_failing_mail_server_doesnt_break_signup(client, mailer, caplog):
    mailer.down = True

    with caplog.at_level(logging.ERROR, logger="lukis.mail"):
        response = await client.post(
            "/auth/register",
            json={
                "email": "ada@example.com",
                "name": "Ada",
                "password": PASSWORD,
                "setup_code": SETUP_CODE,
            },
        )

    assert response.status_code == 201
    assert "Couldn't send email to ada@example.com" in caplog.text


async def test_requests_are_logged(client, caplog):
    with caplog.at_level(logging.INFO, logger="lukis.requests"):
        await client.get("/auth/me")

    [record] = [r for r in caplog.records if r.name == "lukis.requests"]
    assert record.getMessage() == "GET /auth/me 401"
    assert record.fields["status"] == 401
    assert record.fields["duration_ms"] >= 0


def test_json_log_lines():
    record = logging.LogRecord(
        "lukis.test", logging.INFO, __file__, 1, "Hello %s", ("you",), None
    )
    record.fields = {"user": "ada"}

    line = json.loads(JsonFormatter().format(record))

    assert line["message"] == "Hello you"
    assert line["level"] == "info"
    assert line["logger"] == "lukis.test"
    assert line["user"] == "ada"
    assert line["time"].endswith("+00:00")
