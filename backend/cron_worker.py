"""
cron_worker.py — startup worker for PicShare.

Runs once on container start to:
  1. Connect to the database.
  2. Execute recovery tasks (reset stuck events/photos from interrupted uploads).
  3. Exit cleanly.

Run alongside the main API worker in docker-compose via a separate service,
or as a post-start hook. No long-running loop is required now that No-IP DNS
management has been removed.
"""
import asyncio
import logging
import sys
import os

sys.path.append(os.getcwd())

from app.services.db import connect_to_db, close_db_connection
from app.services.recovery import run_recovery_tasks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)


async def main():
    logging.info("PicShare cron worker starting...")
    await connect_to_db()
    try:
        await run_recovery_tasks()
        logging.info("PicShare cron worker finished.")
    finally:
        await close_db_connection()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
