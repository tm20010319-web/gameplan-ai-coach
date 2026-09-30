from gameplan.persistence.database import connection, jsonb, owner


def memory(match_id):
    with connection() as db:
        db.execute("INSERT INTO timer_sessions(user_id,id) VALUES(%s,%s) ON CONFLICT DO NOTHING", (owner(), match_id))
        return db.execute("SELECT memory FROM timer_sessions WHERE user_id=%s AND id=%s", (owner(), match_id)).fetchone()["memory"]


def save_memory(match_id, value):
    with connection() as db:
        db.execute("UPDATE timer_sessions SET memory=%s,updated_at=now() WHERE user_id=%s AND id=%s", (jsonb(value), owner(), match_id))


def audit(match_id, value):
    with connection() as db:
        db.execute("INSERT INTO timer_audit(user_id,session_id,data) VALUES(%s,%s,%s)", (owner(), match_id, jsonb(value)))


from gameplan.persistence.database import dispatch
dispatch(globals(), 'gameplan.persistence.redis_timers', 'memory save_memory audit')
