"""Configurações do ambiente carregadas de .env."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")

SCRAPE_DELAY = float(os.getenv("SCRAPE_DELAY", "3"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "2"))
PAGE_TIMEOUT = int(os.getenv("PAGE_TIMEOUT", "20000"))

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_DIR = Path(__file__).parent.parent / "output"
COOKIES_FILE = DATA_DIR / "ig_cookies.json"

DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
