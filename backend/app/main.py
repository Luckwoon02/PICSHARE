import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import events, photos, guests, auth, payments
from app.services import photo_worker
from app.services.db import connect_to_db, close_db_connection
from app.core.config import get_settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_to_db()

    # NOTE: Recovery tasks and cron jobs are now run in a separate process
    # via cron_worker.py to avoid duplication when running multiple workers.

    # Photo processing queue. Runs inside the API process, so run the API with a
    # single worker (as the Dockerfile does): the queue claims jobs without locking.
    await photo_worker.start()

    yield
    await photo_worker.stop()
    await close_db_connection()

settings = get_settings()

app = FastAPI(title="PicShare API", lifespan=lifespan, redirect_slashes=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length"],
    allow_credentials=True,
)


app.include_router(auth.router)
app.include_router(events.router)
app.include_router(payments.router)
app.include_router(photos.router)
app.include_router(guests.router)

@app.get("/")
def read_root():
    return {"message": "PicShare API is running"}
