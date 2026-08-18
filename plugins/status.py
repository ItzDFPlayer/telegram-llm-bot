"""
Online/offline status feature: periodic model health checks and the shutdown hook.

Responsible for scheduling the JobQueue health-check task and for marking the
bot offline right before the Application tears down its HTTP client.
"""
import logging

from config import HEALTH_CHECK_INTERVAL_SECONDS
from core.llm import health_check_job, push_description_update

logger = logging.getLogger("bot.plugins.status")


async def on_stop(application):
    """
    Runs at the end of Application.stop() — deliberately NOT post_shutdown.
    Application.shutdown() tears down the bot's underlying HTTPXRequest
    client, and post_shutdown fires after that, so any bot API call made
    there raises "This HTTPXRequest is not initialized!". post_stop runs
    earlier, while the bot is still usable, so the offline description
    update can actually go out before the connection is closed.
    """
    await push_description_update(online=False, force=True)


def register(app):
    if app.job_queue is None:
        logger.error(
            "❌ JobQueue is unavailable — the online/offline status description will NEVER "
            'auto-update. This means the "job-queue" extra (APScheduler) isn\'t installed. Fix: '
            'pip install "python-telegram-bot[job-queue]"'
        )
        return

    # The very first check already runs at boot via post_init (see bot.py), so
    # the repeating job starts one interval later to avoid a redundant check.
    app.job_queue.run_repeating(
        health_check_job,
        interval=HEALTH_CHECK_INTERVAL_SECONDS,
        first=HEALTH_CHECK_INTERVAL_SECONDS,
    )
    logger.info(
        f"🩺 Health check job scheduled — polling every {HEALTH_CHECK_INTERVAL_SECONDS}s "
        "(initial check runs at startup)."
    )
