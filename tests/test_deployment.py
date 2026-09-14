import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def read_settings(**environment):
    env = {key: value for key, value in os.environ.items() if key not in {
        "SECRET_KEY", "DEBUG", "DATABASE_URL", "ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS", "DJANGO_SETTINGS_MODULE", "VERCEL",
    }}
    env.update(environment)
    return subprocess.run([sys.executable, "-c", '''
import json
import config.settings as s
print(json.dumps({key: getattr(s, key, None) for key in [
    "DEBUG", "SECRET_KEY", "ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS", "DATABASES",
    "SECURE_SSL_REDIRECT", "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE",
    "SECURE_HSTS_SECONDS", "SECURE_HSTS_INCLUDE_SUBDOMAINS", "SECURE_CONTENT_TYPE_NOSNIFF",
    "SECURE_PROXY_SSL_HEADER", "X_FRAME_OPTIONS", "MIDDLEWARE", "STORAGES", "STATIC_ROOT", "STATIC_URL"
]}, default=str))
'''], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)


def test_production_settings_use_environment_postgres_and_secure_defaults():
    result = read_settings(SECRET_KEY="a-production-secret-with-more-than-fifty-characters-123456789",
                           DATABASE_URL="postgresql://user:password@db.example.org:5432/events",
                           ALLOWED_HOSTS="events.example.org, preview.example.org",
                           CSRF_TRUSTED_ORIGINS="https://events.example.org, https://preview.example.org")
    assert result.returncode == 0, result.stderr
    settings = json.loads(result.stdout)
    assert settings["DEBUG"] is False
    assert settings["SECRET_KEY"].startswith("a-production-secret")
    assert settings["ALLOWED_HOSTS"] == ["events.example.org", "preview.example.org"]
    assert settings["CSRF_TRUSTED_ORIGINS"] == ["https://events.example.org", "https://preview.example.org"]
    database = settings["DATABASES"]["default"]
    assert database["ENGINE"] == "django.db.backends.postgresql"
    assert database["NAME"] == "events"
    assert database["CONN_MAX_AGE"] == 0
    assert database["OPTIONS"]["sslmode"] == "require"
    for name in ["SECURE_SSL_REDIRECT", "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE", "SECURE_CONTENT_TYPE_NOSNIFF"]:
        assert settings[name] is True
    assert settings["SECURE_HSTS_SECONDS"] >= 31536000
    assert settings["SECURE_HSTS_INCLUDE_SUBDOMAINS"] is True
    assert settings["SECURE_PROXY_SSL_HEADER"] == ["HTTP_X_FORWARDED_PROTO", "https"]
    assert settings["X_FRAME_OPTIONS"] == "DENY"
    assert "django.middleware.clickjacking.XFrameOptionsMiddleware" in settings["MIDDLEWARE"]


def test_production_fails_closed_without_secret_hosts_or_postgres():
    base = {"SECRET_KEY": "a-production-secret-with-more-than-fifty-characters-123456789",
            "DATABASE_URL": "postgresql://user:password@db.example.org/events", "ALLOWED_HOSTS": "events.example.org"}
    for field in base:
        values = {key: value for key, value in base.items() if key != field}
        result = read_settings(**values)
        assert result.returncode != 0
        assert field in result.stderr
    result = read_settings(**{**base, "DATABASE_URL": "sqlite:///db.sqlite3"})
    assert result.returncode != 0
    assert "PostgreSQL" in result.stderr


def test_static_and_vercel_configuration_are_explicit():
    local_result = read_settings(DEBUG="1")
    assert local_result.returncode == 0, local_result.stderr
    local = json.loads(local_result.stdout)
    assert local["STATIC_ROOT"].endswith("staticfiles")
    assert local["STORAGES"]["staticfiles"]["BACKEND"] == "django.contrib.staticfiles.storage.StaticFilesStorage"

    production_result = read_settings(
        SECRET_KEY="a-production-secret-with-more-than-fifty-characters-123456789",
        DATABASE_URL="postgresql://user:password@db.example.org:5432/events",
        ALLOWED_HOSTS="events.example.org",
    )
    assert production_result.returncode == 0, production_result.stderr
    production = json.loads(production_result.stdout)
    assert production["STORAGES"]["staticfiles"]["BACKEND"] == "whitenoise.storage.CompressedManifestStaticFilesStorage"
    security_index = production["MIDDLEWARE"].index("django.middleware.security.SecurityMiddleware")
    assert production["MIDDLEWARE"][security_index + 1] == "whitenoise.middleware.WhiteNoiseMiddleware"

    config = json.loads((ROOT / "vercel.json").read_text())
    assert config["functions"]["config/wsgi.py"]["maxDuration"] == 30


def test_vercel_build_synchronizes_schema_and_active_seed():
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert config["tool"]["vercel"]["scripts"]["build"] == (
        "python manage.py migrate --noinput && python manage.py import_events --sync "
        "&& python manage.py import_theater_deals --sync"
    )
