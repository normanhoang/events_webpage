"""Deterministic, isolated SQLite tests, regardless of the shell's database URL."""
import os
from unittest.mock import patch

with patch.dict(os.environ, {
    "DEBUG": "1",
    "SECRET_KEY": "test-only-secret-key",
    "DATABASE_URL": "sqlite:///:memory:",
    "ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
}):
    from .settings import *  # noqa: F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MIDDLEWARE = [
    item for item in globals()["MIDDLEWARE"]
    if item != "whitenoise.middleware.WhiteNoiseMiddleware"
]
