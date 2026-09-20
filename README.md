# 📸 PICSHARE | AI-Powered Event Photography

**PICSHARE** is a full-stack event photo sharing platform that uses **AWS Rekognition** for cloud-based face matching and **AWS S3** for scalable photo storage. Admins upload event photos, and guests find all their pictures instantly by submitting a single selfie.

![Project Status](https://img.shields.io/badge/Status-Beta-orange)

![Stack](https://img.shields.io/badge/Stack-FastAPI%20|%20Next.js%20|%20AWS%20Rekognition-green)

---

## ✨ Features

- 👤 **AI Face Matching** — AWS Rekognition indexes every face in every event photo. Guests submit a selfie and get back a curated gallery of only their photos.
- ☁️ **S3 Photo Storage** — Originals and thumbnails are stored in AWS S3; the backend streams presigned URLs directly to the browser so no files are ever proxied through the server.
- 🚀 **FastAPI Backend** — Async Python backend with `aiosqlite` for non-blocking SQLite access.
- 🎨 **Next.js 16 Frontend** — Responsive admin dashboard and guest portal with dark mode support, built with TailwindCSS v4 and Shadcn UI.
- 📱 **Guest Self-Service Portal** — Guests upload a selfie via webcam or file picker and instantly receive their photo gallery.
- 🛠️ **Admin Dashboard** — Create and manage events, upload photos in bulk, monitor processing status, and view per-event S3 storage usage.
- 🔄 **Startup Recovery Worker** — `cron_worker.py` runs on container start to reset any photos or guests left in a stuck state from interrupted processing.
- 🔐 **Protected Events** — Events can be secured with a secret code that guests must enter before accessing the portal.
- 📦 **Docker Ready** — Single `docker-compose up` starts the entire stack.

---

## 🏗️ Architecture

### Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.12, FastAPI, Uvicorn |
| **Frontend** | Next.js 16, React 19, TailwindCSS v4, Shadcn UI |
| **Database** | SQLite (via `aiosqlite`) |
| **Face Recognition** | AWS Rekognition |
| **File Storage** | AWS S3 |
| **Auth** | JWT (`python-jose`), bcrypt (`passlib`) |
| **Containerisation** | Docker & Docker Compose |
| **Package Managers** | `pip` (backend), `bun 1.3.5` (frontend) |

### Project Structure

```text
PICSHARE/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── auth.py           # JWT login/logout
│   │   │   ├── events.py         # Event CRUD + storage stats
│   │   │   ├── photos.py         # Upload, processing pipeline, gallery, download
│   │   │   └── guests.py         # Selfie upload + face-match search
│   │   ├── core/
│   │   │   └── config.py         # Pydantic settings (reads .env)
│   │   ├── services/
│   │   │   ├── db.py             # aiosqlite connection + schema init
│   │   │   ├── s3_service.py     # Upload, presigned URLs, delete, storage stats
│   │   │   ├── rekognition_service.py  # Collection lifecycle, IndexFaces, SearchFacesByImage
│   │   │   ├── thumbnail_service.py    # Pillow thumbnail generation
│   │   │   └── recovery.py       # Stuck-record recovery logic
│   │   └── main.py               # App entry point, CORS, router registration
│   ├── cron_worker.py            # Startup recovery worker (run once, then exits)
│   ├── reset_app.py              # Dev utility — wipes DB and local data dirs
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── app/                  # Next.js App Router pages
│   │   │   ├── page.tsx          # Landing / home
│   │   │   ├── events/           # Public event listing
│   │   │   ├── event/[slug]/     # Guest portal (selfie upload + gallery)
│   │   │   │   └── guest/[guestId]/  # Per-guest photo gallery
│   │   │   └── admin/
│   │   │       ├── login/        # Admin login
│   │   │       └── dashboard/    # Admin dashboard
│   │   ├── components/           # UI components (client-side)
│   │   └── lib/                  # Shared utilities
│   ├── package.json
│   ├── Dockerfile
│   └── next.config.ts
├── docker-compose.yml
└── README.md
```

### Photo Processing Pipeline

```
Admin uploads photo(s)
        │
        ▼
  Saved locally (staging)
        │
        ▼
  Dimensions read (Pillow)
        │
        ▼
  Original uploaded → S3  (events/{event_id}/originals/{photo_id}.ext)
        │
        ▼
  Thumbnail generated (Pillow 512×512) → S3  (events/{event_id}/thumbnails/{photo_id}.jpg)
        │
        ▼
  AWS Rekognition IndexFaces  (against the event's Rekognition Collection)
        │
        ▼
  Face rows written to SQLite (faces table)
        │
        ▼
  Photo record updated (status=processed, faces_count, s3 keys)
        │
        ▼
  Local staging files deleted
```

### Guest Face-Match Flow

```
Guest submits selfie
        │
        ▼
  Selfie saved to disk  (data/uploads/selfies/)
        │
        ▼
  AWS Rekognition SearchFacesByImage  (searches the event's Collection)
        │
        ▼
  Matched photo_ids returned (deduplicated, sorted by similarity)
        │
        ▼
  Guest record updated with matched_photo_ids
        │
        ▼
  Frontend renders gallery with presigned S3 URLs
```

---

## ⚡ Quick Start

### Prerequisites

| Requirement | Notes |
|---|---|
| **Docker & Docker Compose** | Recommended for running the full stack |
| **AWS Account** | Free-tier eligible for small usage |
| **AWS S3 Bucket** | For photo and thumbnail storage |
| **AWS Rekognition** | For face indexing and search |
| **IAM credentials** | See IAM permissions below |
| **Python 3.12+** | For local development only |
| **Bun 1.3.5+** | For local frontend development only |

#### Required IAM Permissions

Create an IAM user or role and attach a policy with at minimum:

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

---

### 1. Configure Environment

Copy the example file and fill in your values:

```bash
cp backend/.env.example backend/.env
```

| Variable | Description | Default |
|---|---|---|
| `ADMIN_PASSWORD` | Password for the `/admin` dashboard | `admin123` |
| `SECRET_KEY` | Long random string used to sign JWT tokens | `ThisIsMyLongSecretKeyForJWT` |
| `ALGORITHM` | JWT signing algorithm | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime in minutes | `1440` (24 h) |
| `AWS_ACCESS_KEY_ID` | AWS IAM access key | *(required)* |
| `AWS_SECRET_ACCESS_KEY` | AWS IAM secret key | *(required)* |
| `AWS_REGION` | AWS region for S3 and Rekognition | `us-east-1` |
| `S3_BUCKET_NAME` | S3 bucket to store photos | *(required)* |
| `REKOGNITION_FACE_MATCH_THRESHOLD` | Minimum match confidence (0–100) | `80.0` |
| `DB_PATH` | Path to SQLite database file | `data/app.db` |
| `UPLOAD_ROOT` | Local staging dir for originals | `data/uploads/originals` |
| `THUMBNAIL_ROOT` | Local staging dir for thumbnails | `data/thumbnails` |
| `GUEST_SELFIES_DIR` | Local dir for guest selfies | `data/uploads/selfies` |

> **Security note:** Change `ADMIN_PASSWORD` and `SECRET_KEY` before any production deployment.

---

### 2. Run with Docker 🐳

```bash
docker-compose up -d
```

Docker Compose starts two services:

| Service | Container | Port |
|---|---|---|
| FastAPI backend | `picshare-backend` | `8000` |
| Next.js frontend | `picshare-frontend` | `3005` |

Access points:

- **Frontend**: [http://localhost:3005](http://localhost:3005)
- **Admin dashboard**: [http://localhost:3005/admin/login](http://localhost:3005/admin/login)
- **Backend API**: [http://localhost:8000](http://localhost:8000)
- **Swagger / OpenAPI docs**: [http://localhost:8000/docs](http://localhost:8000/docs)

The `data/` directory is bind-mounted from the project root into `/app/data` inside the container, so the SQLite database and any temporary staging files persist across restarts.

---

## 🛠️ Local Development

### Backend

```bash
cd backend

# Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\activate      # Windows
# source venv/bin/activate   # macOS / Linux

# Install dependencies
pip install -r requirements.txt

# Copy and edit environment variables
cp .env.example .env

# Start the API server (auto-reload on file changes)
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.

Run the recovery worker separately if needed:

```bash
python cron_worker.py
```

### Frontend

```bash
cd frontend
bun install
bun run dev
```

The frontend will be available at `http://localhost:3000`.

> For local dev, create `frontend/.env.local` with:
> ```
> NEXT_PUBLIC_API_URL=http://localhost:8000
> ```

---

## � Python Dependencies

```
fastapi              # Web framework
uvicorn              # ASGI server
python-multipart     # Multipart file upload parsing
pillow               # Image processing & thumbnail generation
python-dotenv        # .env file loading
pydantic-settings    # Settings management via environment variables
uuid                 # UUID generation
requests             # HTTP client
pyjwt                # JWT encoding/decoding
passlib[bcrypt]      # Password hashing
python-jose[cryptography]  # JWT with cryptography backend
aiosqlite            # Async SQLite driver
boto3==1.34.131      # AWS SDK — S3 and Rekognition (pinned)
```

---

## 🗄️ Database Schema

SQLite, auto-created on first startup at `DB_PATH`.

| Table | Purpose |
|---|---|
| `events` | Event records (name, slug, date, optional secret code, sync status) |
| `photos` | Photo records per event (S3 keys, dimensions, face count, status) |
| `faces` | Individual face records per photo (Rekognition face ID) |
| `guests` | Guest records per event (selfie path, match results, status) |

---

## 🔌 API Overview

All endpoints are documented interactively at `/docs`.

| Prefix | Tag | Description |
|---|---|---|
| `/auth` | auth | Admin login, token refresh |
| `/events` | events | CRUD for events, storage stats, public listing |
| `/photos` | photos | Bulk upload, gallery, thumbnails, originals, download, delete |
| `/guests` | guests | Selfie submission, face-match results, guest gallery |

---

## 🔐 Security & License

- **Security**: See [SECURITY.md](SECURITY.md) for vulnerability reporting guidelines.
- **Contributing**: See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.
- **License**: Distributed under the **MIT License**.

---

**Built by [Yash Oswal](https://github.com/yashoswalyo) with ❤️**
