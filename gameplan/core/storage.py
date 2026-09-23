import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DATA = Path(os.getenv("COACH_DATA_DIR", str(Path(__file__).resolve().parents[2] / "data")))
DATA.mkdir(parents=True, exist_ok=True)


@contextmanager
def database():
    connection = sqlite3.connect(DATA / "coach.sqlite3", timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


with database() as connection:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS matches (id TEXT PRIMARY KEY, created REAL, memory TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, match_id TEXT NOT NULL, created REAL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, match_id TEXT, created REAL, data TEXT);
        CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, match_id TEXT, created REAL, data TEXT);
    """)


def memory(match_id):
    with database() as connection:
        connection.execute("INSERT OR IGNORE INTO matches VALUES (?, ?, '{}')", (match_id, time.time()))
        row = connection.execute("SELECT memory FROM matches WHERE id = ?", (match_id,)).fetchone()
    return json.loads(row["memory"])


def save_memory(match_id, value):
    with database() as connection:
        connection.execute("UPDATE matches SET memory = ? WHERE id = ?", (json.dumps(value, ensure_ascii=False), match_id))


def audit(match_id, value):
    with database() as connection:
        connection.execute("INSERT INTO audit(match_id, created, data) VALUES (?, ?, ?)", (match_id, time.time(), json.dumps(value, ensure_ascii=False)))


def save_record(match_id, value):
    with database() as connection:
        connection.execute("INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?)", (value["advice_id"], match_id, value["issued_epoch"], json.dumps(value, ensure_ascii=False)))


def records(match_id):
    with database() as connection:
        rows = connection.execute("SELECT data FROM records WHERE match_id = ? ORDER BY created DESC LIMIT 100", (match_id,)).fetchall()
    return [json.loads(row["data"]) for row in rows]


def save_report(report):
    with database() as connection:
        connection.execute("INSERT INTO reports VALUES (?, ?, ?, ?)", (report["id"], report["match_id"], time.time(), json.dumps(report, ensure_ascii=False)))


def get_report(report_id):
    with database() as connection:
        row = connection.execute("SELECT data FROM reports WHERE id = ?", (report_id,)).fetchone()
    return json.loads(row["data"]) if row else None
