"""Settings loaded from .env.

Deliberately not pydantic-settings or similar: this reads one file with no
schema magic, so a missing variable produces a plain message naming the
variable rather than a validation traceback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    pg_host: str
    pg_port: int
    pg_db: str
    pg_user: str
    pg_password: str

    ch_host: str
    ch_http_port: int
    ch_db: str
    ch_user: str
    ch_password: str

    data_root: Path

    @property
    def pg_dsn(self) -> str:
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={self.pg_db} "
            f"user={self.pg_user} password={self.pg_password}"
        )


@lru_cache(maxsize=1)
def settings() -> Settings:
    _load_env_file(ROOT / ".env")

    def need(key: str) -> str:
        val = os.environ.get(key, "").strip()
        if not val:
            raise RuntimeError(
                f"{key} is not set. Copy .env.example to .env and fill it in "
                f"(see docs/setup.md)."
            )
        return val

    return Settings(
        pg_host=os.environ.get("POSTGRES_HOST", "localhost"),
        pg_port=int(os.environ.get("POSTGRES_PORT", "5432")),
        pg_db=need("POSTGRES_DB"),
        pg_user=need("POSTGRES_USER"),
        pg_password=need("POSTGRES_PASSWORD"),
        ch_host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
        ch_http_port=int(os.environ.get("CLICKHOUSE_HTTP_PORT", "8123")),
        ch_db=need("CLICKHOUSE_DB"),
        ch_user=need("CLICKHOUSE_USER"),
        ch_password=need("CLICKHOUSE_PASSWORD"),
        data_root=ROOT / os.environ.get("DATA_ROOT", "data"),
    )
