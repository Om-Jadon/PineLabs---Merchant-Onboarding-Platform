import fcntl
import hashlib
import hmac
import os
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(
    os.getenv(
        "FIXTURES_DIR", ROOT / "fixtures" if (ROOT / "fixtures").exists() else ROOT.parent / "fixtures"
    )
)
STATIC = Path(os.getenv("STATIC_DIR", ROOT / "static"))
SIMULATOR_URL = os.getenv("SIMULATOR_URL", "http://127.0.0.1:8001")
CALLBACK_URL = os.getenv("CALLBACK_URL", "http://127.0.0.1:8018")
CHECK_TIMEOUT = int(os.getenv("CHECK_TIMEOUT_SECONDS", "15"))
LEGAL_NAME = "Riverbend Stationery"
PAN = "DEMFR1234K"
OLD_GST = "29DEMFR1234K1ZL"
NEW_GST = "29DEMFR1234K2ZK"


def now():
    return datetime.now(UTC)


@lru_cache
def secret(purpose: str) -> bytes:
    directory = Path(os.getenv("KEY_DIRECTORY", ".local/keys"))
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    path = directory / "demo.key"
    with open(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+b") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        key = file.read()
        if not key:
            key = os.urandom(32)
            file.write(key)
            file.flush()
            os.fsync(file.fileno())
    return hmac.digest(key, purpose.encode(), "sha256")


def sign(provider: str, content: bytes) -> str:
    return hmac.new(secret("webhook/" + provider), content, hashlib.sha256).hexdigest()


def internal_headers():
    return {"X-Simulator-Key": secret("simulator").hex()}
