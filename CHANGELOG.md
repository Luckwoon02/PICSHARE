# Changelog and to-do

The one place that tracks what has changed in Pixello and what is still to do.

**How to use this file**
- Starting something? Add it under **In progress**.
- Finished it? Move it to **Done**, under the right date, and tick it off in **Next up** if it was listed there.
- Thought of something new? Add it to **Next up** with a priority tag: `[P1]` do soon, `[P2]` worth doing, `[P3]` nice to have.
- Keep entries to one line each. Put the detail in the commit message.

---

## In progress

Work in the working tree that isn't committed yet.

- [ ] Sign in with Google (`backend/app/api/oauth.py`, `frontend/src/app/oauth/`). Built, but only tested against a fake Google. See *Next up*.
- [ ] Event QR card (`event-qr.tsx`, `lib/qr-card.ts`) on the event detail page.
- [ ] README rewrite and this changelog.

---

## Next up

### Must do before real users
- [ ] `[P1]` Test Google sign-in with a real client id and secret, and a matching redirect URI.
- [ ] `[P1]` Set `SMTP_HOST` and `FRONTEND_URL`. Without them, new users can't confirm their email or reset a password.
- [ ] `[P1]` Add a real payment provider (for example Stripe) behind `payment_service.py`, then set `PAYMENT_REQUIRED=true`.

### Quality and safety net
- [ ] `[P1]` Backend tests with pytest: auth flows, photo status transitions, payments, selection links. Use `moto` for S3 and a stub for Rekognition.
- [ ] `[P1]` CI on each PR: `ruff`, `pytest`, `eslint`, `tsc`, `next build`.
- [ ] `[P2]` One or two Playwright flows (register → create event → upload → selfie → gallery).
- [ ] `[P2]` Add `backend/.env.example`. The repo doesn't have one.
- [ ] `[P2]` Pin Python dependencies, and add `mypy` or `pyright`.

### Backend
- [ ] `[P2]` Replace the `ALTER TABLE` list in `db.py` with real migrations (Alembic or numbered scripts).
- [ ] `[P2]` Split the large route files (`photos.py`, `selection.py`). Move logic into services.
- [ ] `[P2]` Add Pydantic response models to every endpoint.
- [ ] `[P3]` Structured logging with request ids instead of `print`.
- [ ] `[P2]` Finish live mode (FTP/SFTP through S3 and SQS) and switch it on in `main.py`.

### Frontend
- [ ] `[P2]` Generate API types from the OpenAPI spec instead of hand-writing them in `lib/api.ts`.
- [ ] `[P2]` Use TanStack Query for fetching and for polling photo status.
- [ ] `[P2]` Accessibility pass: keyboard use of the photo grid, alt text, focus and contrast in both themes.
- [ ] `[P2]` Virtualize large galleries.
- [ ] `[P3]` Check every route has `loading.tsx`, `error.tsx` and `not-found.tsx`.

### Features
- [ ] `[P2]` Event expiry with automatic cleanup of S3 and the Rekognition collection.
- [ ] `[P2]` Guests can delete their own data (selfie and match results).
- [ ] `[P2]` Email guests when their photos are ready.
- [ ] `[P3]` Upload pause/resume, per-file retry and duplicate detection.
- [ ] `[P3]` Event analytics (views, matches, downloads).
- [ ] `[P3]` Per-event match-confidence setting, and a "this isn't me" action for guests.
- [ ] `[P3]` Watermarked previews.

---

## Done

Newest first. Dates are commit dates.

### 2026-10-31
- Email confirmation and password reset; sign-up no longer reveals whether an email is registered.
- Hardened login: `SECRET_KEY` is required and must be 32+ characters, rate limits, and typed tokens.

### 2026-10-26
- Photo selection for photographers' clients (private link and code, previews only, ZIP export).

### 2026-10-25
- Moved browser upload logic into an upload client, engine and web worker.
- Theme toggle now shows only light and dark.

### 2026-10-11
- Live mode ingest: files landing in S3 under `live/` become photos. Switched off in `main.py` for now.

### 2026-10-09
- Rebranded PicShare to Pixello across the codebase.

### 2026-10-08
- Recover an upload interrupted by a page reload.
- Made upload and processing problems visible; photos no longer look stuck.
- Event plans (selection / face scan) and a reliable bulk-upload pipeline.

### 2026-10-07
- Direct-to-S3 uploads, a durable photo queue and retries.
- Stable thumbnail URLs; failed face scans are retried instead of hidden.

### 2026-10-05
- Payments, event access, user auth and the dashboard.

### 2026-09
- Photo error tracking, admin dashboard improvements and new branding (09-22).
- Proxy fallback in `next.config.ts` for local dev (09-20).
- AWS S3 and Rekognition replaced the earlier storage and matching (08-29 to 09-08).

### 2026-02-21
- Core API endpoints, dedicated cron worker for recovery, Docker setup.
- Guest gallery UI and management dashboard.
- Open-source docs and community files.

### 2026-01-27
- Initial commit.
