# Live Chat Room

A responsive real-time chat application built with Flask, Flask-SocketIO, vanilla JavaScript, and SQLite. Chat data and accounts persist across restarts. No AI services or external database are required.

## Features

- Account registration and login with hashed passwords, unique usernames, profile-name changes, CSRF protection, and secure session-cookie defaults.
- Persistent public and private rooms, room descriptions, invitations, and owner/admin moderation.
- Persistent room chat and private messages, timestamps, room history, message search, and browser notifications.
- Online presence, typing indicators, unread room counts, read receipts, and private conversations.
- Message reactions, pinning, deletion, reports, user blocking, and room bans.
- Image, PDF, audio, and video attachments, including in-browser voice-note recording. Uploads are limited to 8 MB per file and an allowlist of extensions.
- Responsive layout and a persistent light/dark theme preference.
- SQLite, test coverage, and a Docker image with a persistent data volume.

## Run locally

Python 3.10 or newer is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set a long, random `SECRET_KEY` in `.env` before deployment. For local development the app creates and stores a random key under `instance/` if none is configured. Run:

```bash
python main.py
```

Open <http://127.0.0.1:5000>. On first visit, create an account. `DATABASE_PATH` defaults to `instance/chat.sqlite3`; `UPLOAD_FOLDER` defaults to `instance/uploads`. Both can be moved by setting those environment variables.

Environment variables:

| Name | Purpose | Default |
| --- | --- | --- |
| `SECRET_KEY` | Stable Flask session-signing key; set this in deployments | Generated local key |
| `REQUIRE_SECRET_KEY` | Fail startup if the key is missing or shorter than 32 characters (`1` for non-Docker deployments) | Off |
| `DATABASE_PATH` | SQLite database file | `instance/chat.sqlite3` |
| `UPLOAD_FOLDER` | Uploaded media directory | `instance/uploads` |
| `ADMIN_USERNAME` | Account name granted the admin role at registration | Unset |
| `HOST` / `PORT` | Bind address and port | `127.0.0.1` / `5000` |
| `FLASK_DEBUG` | Enable Flask-SocketIO debug mode | Off |
| `CORS_ORIGINS` | Explicit Socket.IO allowed origins | Same-origin |
| `COOKIE_SECURE` | Set secure-only session cookies behind HTTPS (`1`) | Off |

Load `.env` values with `python-dotenv`; do not commit actual secrets. If `ADMIN_USERNAME` is used, set it before registering that account. Otherwise, room owners can moderate rooms they created, but there will be no global admin account.

## Use Docker

```bash
docker build -t live-chat-room .
docker run --rm -p 5000:5000 \
  -e SECRET_KEY='replace-with-at-least-32-random-characters' \
  -e HOST=0.0.0.0 \
  -v live-chat-data:/data \
  live-chat-room
```

The container refuses to start unless `SECRET_KEY` contains at least 32 characters. Generate a strong value once, store it in your deployment's secret manager, and reuse it across restarts; changing it logs out all users. The `/data` volume holds both SQLite and uploaded files. Back up both together. The container runs as an unprivileged user.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Deployment and limitations

- Run behind HTTPS, set a stable `SECRET_KEY` of at least 32 characters and `COOKIE_SECURE=1`, and explicitly configure `CORS_ORIGINS` if the UI is served from a separate origin.
- For deployments outside Docker, also set `REQUIRE_SECRET_KEY=1` so startup fails instead of creating a local development key.
- This app uses in-process Socket.IO presence and SQLite. Run a single application process/replica; multi-process presence and horizontal scaling are not supported. SQLite WAL helps local concurrency but is not a substitute for a shared production database/message broker.
- Configure backups and disk monitoring for the database and upload directory. Uploaded files are deliberately restricted and authenticated; do not expose the upload directory as a public static directory.
- Voice recording requires microphone permission and a secure context (HTTPS, or localhost).
- The user lookup and search APIs are intentionally bounded, but this demo does not include email verification, password reset, or a third-party identity provider.
- Review your hosting provider's proxy, TLS, persistent-volume, and backup settings before launch. CI runs the tests on Python 3.10–3.14 and builds the Docker image; it does not deploy the app or verify provider-specific configuration.

## Project layout

```text
main.py                 Flask routes, Socket.IO events, and SQLite schema
templates/              Chat and authentication pages
static/                  Browser client and styles
tests/                   Flask and Socket.IO integration tests
Dockerfile               Container setup
requirements.txt         Python dependencies
```

MIT licensed; see [LICENSE](LICENSE).
