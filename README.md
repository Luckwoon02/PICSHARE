<div align="center">

# 📸 Pixello

**Event photos, found by face.**
Photographers upload an event once. Guests take a selfie and get back only the photos they're in.

![Status](https://img.shields.io/badge/status-beta-orange)
![Backend](https://img.shields.io/badge/backend-FastAPI-009688)
![Frontend](https://img.shields.io/badge/frontend-Next.js%2016-black)
![AI](https://img.shields.io/badge/faces-AWS%20Rekognition-FF9900)
![License](https://img.shields.io/badge/license-MIT-blue)

</div>

---

## Contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Tech stack](#tech-stack)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Local development](#local-development)
- [Project structure](#project-structure)
- [API overview](#api-overview)
- [Database](#database)
- [Roadmap and known limits](#roadmap-and-known-limits)
- [Contributing, security, license](#contributing-security-license)

---

## What it does

**For guests**
- 🤳 **Find my photos** — upload a selfie (webcam or file) and get a gallery of just your photos, matched with AWS Rekognition.
- ⬇️ **Download** single photos, or everything you were matched in as one ZIP.
- 🔒 **Protected events** — an event can require a secret code before the guest portal opens.

**For photographers and event owners**
- 🗂️ **Dashboard** — create events, upload photos in bulk, watch processing status, and see storage used against your plan.
- ⚡ **Resilient uploads** — files go straight from the browser to S3 through presigned URLs, using a web worker, so the server never proxies photo bytes. Failed photos can be retried.
- 🖼️ **Client photo selection** — send each client a private link and code. They pick favourites from previews (never originals), and you export the picks as a ZIP or a list of file names.
- 📱 **QR cards** — generate a printable QR card that sends guests straight to the event page.
- 💳 **Plans and checkout** — events are priced by storage (and optional add-ons). Checkout is simulated for now; see [Roadmap](#roadmap-and-known-limits).

**Accounts**
- Email and password sign-up with **email confirmation** and **password reset**.
- **Continue with Google** (optional).
- Sign-up and reset flows never reveal whether an email is already registered.
- Login is rate-limited and tokens are typed, so a reset or verify token can't be used as a login token.

---

## How it works

### Photo processing

```
Browser ── presigned PUT ──▶ S3  events/{event_id}/originals/{photo_id}.ext
   │
   └─ POST /photos/upload-complete ─▶ photo row (status = pending)
                                          │
                                  background photo worker
                                          │
              ┌───────────────────────────┼────────────────────────────┐
              ▼                           ▼                            ▼
     read dimensions            make thumbnail + preview      Rekognition IndexFaces
        (Pillow)                  ─▶ S3 (thumbnails/)       (event's own collection)
                                          │
                                          ▼
                  faces rows written, photo marked processed
```

- The worker lives inside the API process and claims jobs from the database. It has concurrency and retry limits (`PHOTO_WORKER_CONCURRENCY`, `REKOGNITION_MAX_TPS`).
- **Run the API with a single Uvicorn worker.** The queue claims jobs without cross-process locking (the Dockerfile already does this).
- `cron_worker.py` runs once on container start and resets photos or guests left stuck by an interrupted run.

### Guest face match

```
Guest selfie ─▶ Rekognition SearchFacesByImage (event collection)
            ─▶ matched photo ids, de-duplicated, best match first
            ─▶ guest record updated
            ─▶ gallery rendered with presigned S3 URLs
```

Each event gets its own Rekognition collection, so guests can only ever match inside the event they opened.

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn |
| Frontend | Next.js 16, React 19, Tailwind CSS v4, shadcn/ui |
| Database | SQLite via `aiosqlite` |
| Face recognition | AWS Rekognition |
| File storage | AWS S3 |
| Auth | JWT, bcrypt, optional Google OAuth 2.0 |
| Email | Any SMTP server (printed to the console if none is set) |
| Packaging | Docker and Docker Compose; `pip` (backend), `bun` (frontend) |

---

## Quick start

### Prerequisites

| Need | Why |
|---|---|
| Docker and Docker Compose | Runs the whole stack |
| An AWS account, an S3 bucket, and an IAM user | Photo storage and face matching |
| Python 3.12+ and Bun 1.3+ | Only for [local development](#local-development) |

<details>
<summary><b>Minimum IAM policy</b></summary>

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::YOUR_BUCKET_NAME",
        "arn:aws:s3:::YOUR_BUCKET_NAME/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "rekognition:CreateCollection",
        "rekognition:DeleteCollection",
        "rekognition:IndexFaces",
        "rekognition:SearchFacesByImage",
        "rekognition:ListCollections"
      ],
      "Resource": "*"
    }
  ]
}
```

</details>

Browsers upload straight to S3, so the bucket also needs a CORS rule for your frontend's origin. `backend/set_s3_cors.py` sets one up.

### 1. Create `backend/.env`

At minimum:

```env
SECRET_KEY=<32+ random characters>
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
S3_BUCKET_NAME=your-bucket
```

Generate a key with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Everything else has a default. See [Configuration](#configuration).

### 2. Start the stack

```bash
docker-compose up -d
```

| Service | Container | URL |
|---|---|---|
| Frontend | `pixello-frontend` | http://localhost:3005 |
| Backend API | `pixello-backend` | http://localhost:8000 |
| Interactive API docs | | http://localhost:8000/docs |

The `data/` folder is mounted into the backend container, so the SQLite database and staging files survive restarts.

### 3. Try it

1. Open http://localhost:3005 and **register**. With no `SMTP_HOST` set, the confirmation link is printed in the backend logs (`docker logs pixello-backend`).
2. Create an event, upload some photos, and wait for them to show as processed.
3. Open the event's public page, submit a selfie, and see your matches.

---

## Configuration

All backend settings are environment variables, read from `backend/.env`. Defaults are in [backend/app/core/config.py](backend/app/core/config.py).

### Core and auth

| Variable | Description | Default |
|---|---|---|
| `SECRET_KEY` | **Required**, 32+ characters. Signs JWTs. Changing it logs everyone out and invalidates client selection sessions. | none (app won't start) |
| `ALGORITHM` | JWT algorithm | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Login token lifetime | `1440` |
| `FRONTEND_URL` | Used for links in emails and for the CORS origin | `http://localhost:3000` |

### Email

| Variable | Description | Default |
|---|---|---|
| `SMTP_HOST` | Mail server. **Empty means emails are printed to the backend console** (fine locally, not for real users). | empty |
| `SMTP_PORT` | Mail server port | `587` |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | Mail login, if the server needs one | empty |
| `SMTP_USE_SSL` | `true` = TLS from the start (usually port 465); `false` = STARTTLS | `false` |
| `EMAIL_FROM` | Sender shown on emails | `Pixello <no-reply@localhost>` |
| `EMAIL_VERIFY_EXPIRE_HOURS` | How long the confirmation link works | `24` |
| `PASSWORD_RESET_EXPIRE_MINUTES` | How long the reset link works | `60` |

### Sign in with Google (optional)

| Variable | Description | Default |
|---|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | From Google Cloud Console → APIs & Services → Credentials → OAuth client ID → Web application. Empty hides the button. | empty |
| `GOOGLE_REDIRECT_URI` | The API's `/auth/google/callback` as the browser reaches it (behind the frontend proxy: `https://your-site/api/auth/google/callback`). Add the same URL under *Authorized redirect URIs*. | `http://localhost:8000/auth/google/callback` |

### AWS and processing

| Variable | Description | Default |
|---|---|---|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | IAM credentials | required |
| `AWS_REGION` | Region for S3 and Rekognition | `us-east-1` |
| `S3_BUCKET_NAME` | Bucket for photos and thumbnails | required |
| `REKOGNITION_FACE_MATCH_THRESHOLD` | Minimum match confidence, 0–100 | `80.0` |
| `REKOGNITION_MAX_TPS` | Max IndexFaces calls per second. AWS's default quota is 50 in us-east-1, us-west-2 and eu-west-1, and 5 elsewhere. | `5` |
| `PHOTO_WORKER_CONCURRENCY` | Photos processed at once. Each decodes a full-size image, so keep it low on a laptop. | `4` |

### Payments and pricing

| Variable | Description | Default |
|---|---|---|
| `PAYMENT_REQUIRED` | If `false`, owners can skip checkout (dev only) | `false` |
| `PAYMENT_PROVIDER` | Only `mock` (simulated checkout) exists today | `mock` |
| `PAYMENT_CURRENCY` | Currency code | `usd` |
| `PRICE_PER_GB_CENTS` | Price per GB of event storage, in cents | `100` |
| `PRICE_FACE_SCAN_CENTS` / `PRICE_SELECTION_CENTS` | Flat per-event add-on, in cents. `0` = included. | `0` / `0` |
| `MIN_STORAGE_GB` / `MAX_STORAGE_GB` | Allowed storage when creating an event | `1` / `1000` |

### Paths and live mode

| Variable | Description | Default |
|---|---|---|
| `DB_PATH` | SQLite file | `data/app.db` |
| `UPLOAD_ROOT` | Local staging for originals | `data/uploads/originals` |
| `THUMBNAIL_ROOT` | Local staging for thumbnails | `data/thumbnails` |
| `GUEST_SELFIES_DIR` | Guest selfies | `data/uploads/selfies` |
| `INGEST_QUEUE_URL`, `LIVE_POLL_SECONDS`, `LIVE_SAFETY_SCAN_SECONDS` | Live-mode settings (see [limits](#roadmap-and-known-limits)) | empty / `15` / `300` |

### Frontend

The frontend reads one variable, `NEXT_PUBLIC_API_URL`. In Docker it is `/api` (a proxy to the backend). For local development set it to `http://localhost:8000`.

---

## Local development

### Backend

```bash
cd backend
python -m venv venv
.\venv\Scripts\activate        # Windows
# source venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API runs at http://localhost:8000. To run the stuck-record recovery pass by hand:

```bash
python cron_worker.py
```

`reset_app.py` wipes the local database and data folders. It is a development utility, so use it carefully.

### Frontend

```bash
cd frontend
bun install
echo NEXT_PUBLIC_API_URL=http://localhost:8000 > .env.local
bun run dev
```

The app runs at http://localhost:3000, matching the default `FRONTEND_URL`.

---

## Project structure

```text
PICSHARE/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── auth.py        # register, login, verify email, password reset
│   │   │   ├── oauth.py       # Sign in with Google
│   │   │   ├── events.py      # event CRUD, public listing, storage stats
│   │   │   ├── photos.py      # presigned uploads, status, gallery, delete, download
│   │   │   ├── guests.py      # selfie requests, matches, ZIP download
│   │   │   ├── selection.py   # client photo-selection links and picks
│   │   │   └── payments.py    # checkout, confirm, skip
│   │   ├── core/config.py     # settings (reads .env)
│   │   ├── services/
│   │   │   ├── db.py                   # SQLite connection, schema, migrations
│   │   │   ├── photo_worker.py         # background processing queue
│   │   │   ├── rekognition_service.py  # collections, IndexFaces, SearchFaces
│   │   │   ├── s3_service.py           # presigned URLs, delete, storage size
│   │   │   ├── thumbnail_service.py    # thumbnails and previews
│   │   │   ├── email_service.py        # SMTP or console output
│   │   │   ├── payment_service.py      # payment provider hook (mock)
│   │   │   ├── live_ingest.py          # FTP/SFTP live mode (switched off)
│   │   │   └── recovery.py             # reset stuck photos and guests
│   │   └── main.py            # app, CORS, routers, worker lifecycle
│   ├── cron_worker.py         # one-shot recovery on container start
│   ├── set_s3_cors.py         # configure bucket CORS for browser uploads
│   ├── setup_live_ingest.py   # SQS setup for live mode
│   └── Dockerfile
├── frontend/
│   └── src/
│       ├── app/               # App Router pages
│       │   ├── dashboard/     # events, new event, payment, event detail
│       │   ├── event/[slug]/  # guest portal and gallery
│       │   ├── select/[token] # client photo selection
│       │   ├── login, register, forgot-password, reset-password, verify-email, oauth/
│       │   └── events/        # public event listing
│       ├── components/        # UI, dashboard, auth and gallery components
│       └── lib/               # API client, upload engine and worker, QR card, auth hook
├── docker-compose.yml
├── CONTRIBUTING.md
└── SECURITY.md
```

---

## API overview

Full, interactive docs are at `/docs` on the running backend.

| Prefix | Purpose |
|---|---|
| `/auth` | Register, login, email verification, password reset, `/me`, Google sign-in |
| `/events` | Event CRUD, public listing and lookup, secret-code check, storage usage |
| `/photos` | Upload URLs and completion, status, gallery, retry, delete (single and bulk), original, thumbnail and download URLs |
| `/guests` | Selfie request, status polling, matches, ZIP download, delete |
| `/selection` | Owner side: create and manage client selection links, export picks |
| `/select` | Client side: enter code, browse previews, pick, submit |
| `/payments` | Pricing config, checkout, confirm, skip |

---

## Database

SQLite, created automatically on first start at `DB_PATH`. New columns are added by small startup migrations in [backend/app/services/db.py](backend/app/services/db.py).

| Table | Holds |
|---|---|
| `users`, `auth_tokens`, `oauth_identities` | Accounts, email and reset tokens, linked Google identities |
| `events` | Event details, owner, plan, storage, payment status, optional secret code |
| `photos` | S3 keys, dimensions, face count, processing status, retries |
| `faces` | Rekognition face id per detected face |
| `guests` | Guest requests, selfie path, match results |
| `selection_links`, `selections` | Client selection links and the photos each client picked |

---

## Roadmap and known limits

- **Payments are simulated.** Only the `mock` provider exists. A real gateway (for example Stripe) plugs into `app/services/payment_service.py`.
- **Live mode is switched off.** The code that pulls camera photos from FTP/SFTP through S3 and SQS is written but disabled in `main.py` until it is finished.
- **Google sign-in is untested against real Google.** It has only been exercised against a fake. Before relying on it, test it with real credentials and a matching redirect URI.
- **No automated tests or CI yet.**

The full list of what's done, in progress and still to do is in [CHANGELOG.md](CHANGELOG.md).
- **SQLite and a single API worker** suit small deployments. Scaling past that means a different database and an external job queue.

---

## Contributing, security, license

- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Reporting vulnerabilities: [SECURITY.md](SECURITY.md)
- License: MIT
