import os

import pytest

if "TEST_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    from desk.settings import secret

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("KEY_DIRECTORY", str(tmp_path / "keys"))
    secret.cache_clear()
    yield
    secret.cache_clear()
