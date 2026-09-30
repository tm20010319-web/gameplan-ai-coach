import asyncio
import logging

from gameplan.ai import player_profile
from gameplan.persistence import profiles
from gameplan.persistence.database import as_user, connection, enabled

log = logging.getLogger(__name__)


def process(job):
    sources = job["input"]["sources"]
    changes = list(job["changes"])
    model = job["model"] or "no-new-evidence"
    try:
        for start in range(job["progress"], len(sources), 30):
            # Frozen inputs and durable chunk checkpoints survive retries.
            profile = player_profile.merge(job["input"]["profile"], changes, sources)
            projection = {"facts": [{k: f[k] for k in ("scope", "key", "category", "value", "basis", "status")} for f in profile["facts"]]}
            batch, model = player_profile.extract(projection, sources[start:start+30])
            changes.extend(batch)
            if not profiles.checkpoint(job, min(start+30, len(sources)), changes, model):
                return
        profiles.complete(job, changes, model)
    except Exception:
        profiles.fail(job)


def recover_reviews():
    from gameplan.monitoring.review_runtime import recover
    from gameplan.persistence.database import redis_enabled
    if redis_enabled():
        from gameplan.persistence.redis_accounts import recover_users
        for uid in recover_users():
            with as_user(uid):
                recover()
        return
    with connection() as db:
        rows = db.execute("SELECT DISTINCT user_id FROM matches WHERE status='pending' OR (status='analyzing' AND updated<extract(epoch from now())-120) LIMIT 20").fetchall()
    for row in rows:
        with as_user(row["user_id"]):
            recover()


async def run():
    while enabled():
        try:
            await asyncio.to_thread(recover_reviews)
            job = await asyncio.to_thread(profiles.claim)
            if job:
                await asyncio.to_thread(process, job)
            else:
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never log connection strings, chat text, model responses or keys.
            log.warning("Profile worker unavailable; durable tasks will be retried.")
            await asyncio.sleep(5)
