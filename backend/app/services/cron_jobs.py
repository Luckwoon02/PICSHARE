"""
cron_jobs.py — background job definitions for Pixello.

The No-IP Dynamic DNS updater has been removed: Pixello now runs on AWS
and no longer requires self-hosted DNS management.

Add new periodic tasks here as async functions and call them from
cron_worker.py as needed.
"""
import logging

logger = logging.getLogger(__name__)

# No periodic jobs required at this time.
# cron_worker.py runs run_recovery_tasks() once on startup and exits.
