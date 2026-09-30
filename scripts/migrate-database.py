import os
from pathlib import Path
import sys
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
from gameplan.persistence.database import migrate

if __name__ == "__main__":
    migrate()
    print("Database migrations applied.")
