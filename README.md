# Lukis (backend)

The API behind [Lukis](https://github.com/Isma27K/lukis-front), a hand-drawn
whiteboard built on Excalidraw. It owns accounts, workspaces and roles,
drawings and their scenes, version history, share links, image storage and
the live-collaboration relay.

**Want to run Lukis?** The Docker Compose stack and the self-hosting guide
live in the frontend repo:
[lukis-front → docs/self-hosting.md](https://github.com/Isma27K/lukis-front/blob/main/docs/self-hosting.md).

- **FastAPI** on Python 3.12+
- **MongoDB** 7 or 8 for everything except files
- **S3-compatible object storage** (RustFS, MinIO, AWS S3…) for images,
  thumbnails, profile pictures and backups
- **SMTP** (optional) for email verification, password reset and emailed
  invites
- **Google, GitHub and Microsoft login** (optional), each switched on by
  setting its client ID and secret

## Running it locally

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env      # point it at your MongoDB and object storage
uvicorn main:app --reload # http://localhost:8000
```

Then run the [frontend](https://github.com/Isma27K/lukis-front) on
`http://localhost:3000`. It proxies `/api` to this server.

On a fresh database the backend logs a one-time **setup code**. The first
account to register needs it and becomes the site owner, who can open or
close sign-up and send sign-up invites. Set `SETUP_CODE` in `.env` to choose
the code yourself.

## Tests

```sh
pytest
```

The tests need a MongoDB on `MONGO_URL` (default `mongodb://localhost:27017`);
each test uses its own throwaway database. Object storage and email are
replaced by in-memory fakes.

## Layout

| Folder | What's in it |
| --- | --- |
| `routes/` | HTTP and WebSocket endpoints |
| `function/` | The logic behind them |
| `data/` | Request, response and database models |
| `middleware/` | Session auth and the CSRF guard |
| `utils/` | Settings, database, storage, mail, logging |
| `scripts/` | Server-side tools: `site` (owner and sign-up) and `backup` |

## License

[MIT](LICENSE)
