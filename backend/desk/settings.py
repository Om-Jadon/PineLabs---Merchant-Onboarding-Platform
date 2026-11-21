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


def key_material(path: Path, *, read_only=False) -> bytes:
    if read_only:
        with path.open("rb") as file:
            fcntl.flock(file, fcntl.LOCK_SH)
            key = file.read(33)
    else:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with open(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), "r+b") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            key = file.read(33)
            if not key:
                key = os.urandom(32)
                file.write(key)
                file.flush()
                os.fsync(file.fileno())
    if len(key) != 32:
        raise RuntimeError("The stored key is incomplete. Restore the key volume.")
    return key


@lru_cache
def secret(purpose: str) -> bytes:
    directory_name = {"session": "SESSION_KEY_DIRECTORY", "simulator": "SIMULATOR_KEY_DIRECTORY"}.get(
        purpose, "KEY_DIRECTORY"
    )
    directory = Path(os.getenv(directory_name) or os.getenv("KEY_DIRECTORY", ".local/keys"))
    key = key_material(directory / "demo.key", read_only=os.getenv("KEYS_READ_ONLY") == "1")
    return hmac.digest(key, purpose.encode(), "sha256")


def sign(provider: str, content: bytes) -> str:
    return hmac.new(secret("webhook/" + provider), content, hashlib.sha256).hexdigest()


def internal_headers():
    return {"X-Simulator-Key": secret("simulator").hex()}
