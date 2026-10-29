# Deploying Lukis

`compose.yaml` runs the whole app with Docker Compose, from images CI publishes:

| Service    | What it does                                                        |
| ---------- | ------------------------------------------------------------------- |
| `caddy`    | Ports 80/443: HTTPS, compression. Optional, see First deploy.       |
| `frontend` | Next.js. Proxies `/api` and the live-editing WebSockets to backend. |
| `backend`  | FastAPI. Logs JSON lines to stdout.                                 |
| `mongo`    | MongoDB 8, data in the `mongo_data` volume. Optional, likewise.     |
| `backup`   | Backs MongoDB up to object storage once a day.                      |

Images, thumbnails and backups live in your S3-compatible store (RustFS),
which stays where it is.

## Where the images come from

Each push to `main` runs CI in the frontend and backend repos. Once the
checks pass, CI builds an image and pushes it to GitHub's container registry:

- `ghcr.io/isma27k/lukis-frontend`
- `ghcr.io/isma27k/lukis-backend`

Each image is tagged `latest` and `sha-<short commit>`. The packages are
private like the repos, and CI keeps the 10 most recent versions of each.
The server only pulls them, so it needs nothing but this folder.

The images are built for `linux/amd64`. An ARM server (such as a Raspberry Pi)
would need `platforms:` added to the workflows.

## First deploy

1. Copy this `deploy` folder to the server (`compose.yaml`, `Caddyfile`,
   `.env.example`, and for your own proxy or database `compose.own-proxy.yaml`,
   `nginx.conf.example` and `compose.own-mongo.yaml`).

2. Let the server read the private images. On GitHub, go to Settings →
   Developer settings → Personal access tokens → **Tokens (classic)**. Create
   a token with only the `read:packages` scope, then on the server:

   ```sh
   echo "<token>" | docker login ghcr.io -u Isma27K --password-stdin
   ```

   Docker remembers the login in `~/.docker/config.json`.

3. Configure:

   ```sh
   cp .env.example .env    # then fill it in
   ```

   For a public domain, point its DNS at the server and open ports 80 and 443;
   Caddy fetches certificates on first start. For a LAN-only setup use
   `SITE_ADDRESS=:80`, `PUBLIC_URL=http://<server-ip>` and `COOKIE_SECURE=false`.

   Behind a Cloudflare Tunnel, Cloudflare does HTTPS. Use `SITE_ADDRESS=:80`
   with `PUBLIC_URL=https://<domain>` and `COOKIE_SECURE=true`, point the
   tunnel's service at Caddy over plain HTTP (`http://localhost:80` when
   cloudflared runs on the server), and turn on Cloudflare's "Always Use
   HTTPS". With a domain in `SITE_ADDRESS`, Caddy would redirect the tunnel's
   HTTP requests back to HTTPS and visitors land in a redirect loop.

   With a reverse proxy the server already runs (nginx…), leave Caddy out:
   uncomment `COMPOSE_FILE` (keeping `compose.own-proxy.yaml`) and
   `FRONTEND_PORT` at the top of `.env`, and the frontend listens on
   `127.0.0.1:3000` for the proxy. `nginx.conf.example` has what the proxy
   must do: pass WebSockets through, accept 16 MB request bodies, and set
   `X-Forwarded-For` to the visitor's address. If Caddy already ran on this
   server, remove it first with `docker compose rm -sf caddy`.

   With a MongoDB you already run, leave the bundled one out: put
   `compose.own-mongo.yaml` in `COMPOSE_FILE` and set `MONGO_URL` (and
   `MONGO_DB` if it isn't `lukis`). Its user needs `readWrite` on that
   database; a single server is fine, no replica set needed. This uses
   Compose's `!reset`, so it needs Docker Compose 2.24.4 or newer
   (`docker compose version`). To move existing data over, run a backup
   first and restore it after switching (see Backups).

4. Start:

   ```sh
   docker compose pull
   docker compose up -d
   docker compose ps          # backend should become "healthy"
   ```

5. Create your account. The first account becomes the site owner, and on a
   fresh install it needs a one-time setup code, so a stranger who reaches
   the site before you can't claim it. The backend prints the code when it
   starts:

   ```sh
   docker compose logs backend | grep "setup code"
   ```

   Enter it on the sign-up page. A restart prints a new one until the owner
   exists. Setting `SETUP_CODE` in `.env` uses that code instead and prints
   nothing. See [Site owner and sign-up](#site-owner-and-sign-up) for what
   the owner can do.

### If `mongo` exits right away

MongoDB 8.0 refuses to start on Linux kernels 6.19 to 7.0.13
([SERVER-121912](https://jira.mongodb.org/browse/SERVER-121912)), with
"Linux kernel versions 6.19 and newer has a known incompatibility" in
`docker compose logs mongo`. Containers use the host's kernel, so check
`uname -r` on the server. Either update the kernel (7.0.14+ works), or switch
`compose.yaml` to `image: mongo:7.0` before the first start: 7.0 can't open
data files that 8.0 created.

## Updating

Push to `main` and wait for CI to finish, then on the server:

```sh
docker compose pull && docker compose up -d
```

To pin a version or roll back, set `FRONTEND_TAG` / `BACKEND_TAG` in `.env` to
a `sha-<short commit>` tag (listed under the repo's Packages on GitHub), then
run the same command.

## Site owner and sign-up

The site owner gets "Site settings" in the user menu. Anyone can sign up until
the owner switches that off there. After that, only people the owner invites
can create an account: with a sign-up invite from Site settings, or a
workspace invite the owner sends. Workspace invites from other members only
work for people who already have an account. Everyone who has one can still
log in.

On an install from before there was a site owner, the oldest account becomes
the owner when the backend starts, and sign-up stays open.

If the owner is locked out, fix it from the server (there's no way to do this
from the web):

```sh
docker compose exec backend python -m scripts.site show
docker compose exec backend python -m scripts.site set-owner you@example.com
docker compose exec backend python -m scripts.site registration open   # or closed
```

To hand the site to a new account while sign-up is closed, open it, register,
`set-owner` that account and close it again.

## Building from source instead

`compose.build.yaml` builds the images locally instead of pulling them. The
frontend and backend checkouts need to sit side by side, with the Excalidraw
clone next to them:

```
lukis/
  backend/     this repo (compose files are in backend/deploy)
  frontend/
  excalidraw/  git clone https://github.com/excalidraw/excalidraw
```

```sh
# The editor packages the frontend image installs; they're gitignored.
git -C excalidraw checkout "$(cat frontend/.excalidraw-ref)"
(cd frontend && npm run excalidraw:build)   # needs Node 24 and yarn 1

cd backend/deploy
docker compose -f compose.yaml -f compose.build.yaml up -d --build
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
