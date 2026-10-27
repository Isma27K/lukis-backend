# Deploying Lukis

`compose.yaml` runs the whole app with Docker Compose:

| Service    | What it does                                                        |
| ---------- | ------------------------------------------------------------------- |
| `caddy`    | The only published service (ports 80/443). HTTPS, compression.      |
| `frontend` | Next.js. Proxies `/api` and the live-editing WebSockets to backend. |
| `backend`  | FastAPI. Logs JSON lines to stdout.                                 |
| `mongo`    | MongoDB 8 with a password, data in the `mongo_data` volume.         |
| `backup`   | Backs MongoDB up to object storage once a day.                      |

Images, thumbnails and backups live in your S3-compatible store (RustFS),
which stays where it is.

## First deploy

The frontend and backend checkouts need to sit side by side, with the
Excalidraw clone next to them:

```
lukis/
  backend/     this repo (compose.yaml is in backend/deploy)
  frontend/
  excalidraw/  git clone https://github.com/excalidraw/excalidraw
```

1. Build the editor packages that the frontend image installs. They're
   gitignored, so this is needed on every machine that builds the image:

   ```sh
   git -C excalidraw checkout "$(cat frontend/.excalidraw-ref)"
   cd frontend && npm run excalidraw:build   # needs Node 24 and yarn 1
   ```

2. Configure:

   ```sh
   cd backend/deploy
   cp .env.example .env    # then fill it in
   ```

   For a public domain, point its DNS at the server and open ports 80 and 443;
   Caddy fetches certificates on first start. For a LAN-only setup use
   `SITE_ADDRESS=:80`, `PUBLIC_URL=http://<server-ip>` and `COOKIE_SECURE=false`.

3. Start:

   ```sh
   docker compose up -d --build
   docker compose ps          # backend should become "healthy"
   ```

### If `mongo` exits right away

MongoDB 8.0 refuses to start on Linux kernels 6.19 to 7.0.13
([SERVER-121912](https://jira.mongodb.org/browse/SERVER-121912)), with
"Linux kernel versions 6.19 and newer has a known incompatibility" in
`docker compose logs mongo`. Containers use the host's kernel, so check
`uname -r` on the server. Either update the kernel (7.0.14+ works), or switch
`compose.yaml` to `image: mongo:7.0` before the first start: 7.0 can't open
data files that 8.0 created.

## Updating

```sh
git -C backend pull && git -C frontend pull
# Only if frontend/.excalidraw-ref changed:
git -C excalidraw fetch && git -C excalidraw checkout "$(cat frontend/.excalidraw-ref)"
(cd frontend && npm run excalidraw:build)

cd backend/deploy && docker compose up -d --build
```

## Logs

Backend, backup and Next.js server errors are one JSON object per line:

```sh
docker compose logs -f backend
docker compose logs backend | grep '"level": "error"'
```

## Email

Email is optional. With the `SMTP_*` settings filled in, Lukis:

- asks new users to confirm their address, and requires it before they join
  a workspace
- offers password reset
- emails invite links

Without `SMTP_HOST`, none of that appears. Admins copy invite links from the
settings page and send them themselves.

## Backups

The `backup` service writes `backups/<db>-<time>.tar.gz` to the bucket every
24 hours and deletes ones older than `BACKUP_KEEP_DAYS` (never the newest).
Each holds every collection as BSON, the format `mongodump` uses.

```sh
docker compose run --rm backup python -m scripts.backup run    # one now
docker compose run --rm backup python -m scripts.backup list
# Restore. --drop replaces collections that already have data.
docker compose run --rm backup python -m scripts.backup restore \
  backups/lukis-20261027T030000Z.tar.gz --drop
docker compose restart backend   # recreates indexes
```

Images and thumbnails aren't in these backups; they're already in object
storage, so back up the bucket itself with your storage's own tools if it
isn't replicated.
