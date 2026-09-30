"""Create a fresh app database and credentials without reading/migrating SQLite."""
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import quote
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psycopg
from psycopg import sql
from dotenv import dotenv_values, set_key
from gameplan.persistence.database import migrate, connection


def main():
    env = ROOT / ".env"
    settings = dotenv_values(env)
    if settings.get("GAMEPLAN_DATABASE_URL"):
        raise SystemExit("A new database is already configured; use scripts/migrate-database.py to apply migrations.")
    name, role, password = "gameplan", "gameplan_app", secrets.token_urlsafe(32)
    private = ROOT / "data" / "postgres-private"
    admin = (private / "admin-password.txt").read_text(encoding="ascii").strip()
    with psycopg.connect(host="127.0.0.1", port=55432, dbname="postgres", user="postgres", password=admin, autocommit=True) as db:
        if db.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
            raise SystemExit("Database gameplan already exists; refusing to replace it.")
        if db.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
            raise SystemExit("Role gameplan_app already exists; refusing to replace it.")
        db.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
        db.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8'").format(sql.Identifier(name), sql.Identifier(role)))
    url = f"postgresql://{role}:{quote(password)}@127.0.0.1:55432/{name}"
    # Save a recoverable local credential before attempting the migration.
    (private / "application.env").write_text("GAMEPLAN_DATABASE_URL=" + url + "\n", encoding="utf-8")
    os.environ["GAMEPLAN_DATABASE_URL"] = url
    migrate()
    uid = str(uuid4())
    with connection() as db:
        db.execute("INSERT INTO users(id,display_name,auth_provider,auth_subject) VALUES(%s,%s,'local-dev','developer')", (uid, "本地开发玩家"))
        db.execute("INSERT INTO user_roles VALUES(%s,'player')", (uid,))
    set_key(env, "GAMEPLAN_DATABASE_URL", url)
    set_key(env, "GAMEPLAN_DEV_USER_ID", uid)
    set_key(env, "GAMEPLAN_LOCAL_POSTGRES", "1")
    print("Created database gameplan and a local development player. Credentials saved locally; old SQLite files were not opened.")


if __name__ == "__main__":
    main()
