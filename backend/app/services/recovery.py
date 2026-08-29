"""
recovery.py — startup recovery tasks for PicShare.

Resets events and photos that were left in intermediate states by a
previous process crash or container restart.  Called once by cron_worker.py
on startup.
"""
from app.services.db import db
from collections import defaultdict
import logging


async def run_recovery_tasks():
    """
    1. Reset events stuck in 'syncing' status back to 'idle'.
       (sync_status is repurposed for tracking bulk-upload progress.)
    2. Log any photos still in 'pending' or 'pending_upload' status so
       operators can decide whether to re-upload them.
    """
    logging.info("Checking for interrupted uploads...")

    # --- 1. Reset stuck events ---
    stuck_events = await db.fetch_all(
        "SELECT id, name FROM events WHERE sync_status = 'syncing'"
    )
    if stuck_events:
        logging.warning(f"Found {len(stuck_events)} event(s) stuck in 'syncing' status — resetting to 'idle'")
        for event in stuck_events:
            await db.execute(
                "UPDATE events SET sync_status = 'idle' WHERE id = ?",
                (event["id"],),
            )
            logging.info(f"  Reset event: {event['name']}")
    else:
        logging.info("No stuck events found.")

    # --- 2. Report pending photos ---
    pending_photos = await db.fetch_all(
        """
        SELECT p.id, p.event_id, p.status, p.original_file_name
        FROM photos p
        WHERE p.status IN ('pending', 'pending_upload')
        """
    )
    if pending_photos:
        logging.warning(
            f"Found {len(pending_photos)} photo(s) in pending state. "
            "Re-upload via POST /photos/upload to reprocess."
        )
        by_event: dict[str, int] = defaultdict(int)
        for photo in pending_photos:
            by_event[photo["event_id"]] += 1
        for event_id, count in by_event.items():
            logging.info(f"  Event {event_id[:8]}…: {count} pending photo(s)")
    else:
        logging.info("No pending photos found.")

    logging.info("Recovery check complete.")
