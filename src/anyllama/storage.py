"""Small, transaction-protected store. Conversation bodies never enter this module."""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Settings

ACTIVITY_METRICS = {
    "streamed": "INTEGER",
    "generation_ms": "REAL",
    "tokens_per_second": "REAL",
    "throughput_source": "TEXT",
    "cached_input_tokens": "INTEGER",
    "reasoning_tokens": "INTEGER",
    "cost_usd": "REAL",
    "cost_source": "TEXT",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS providers (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                base_url TEXT NOT NULL, has_key INTEGER NOT NULL DEFAULT 0,
                refreshed_at TEXT, error TEXT
            );
            CREATE TABLE IF NOT EXISTS models (
                id TEXT PRIMARY KEY, provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
                upstream_id TEXT NOT NULL, metadata TEXT NOT NULL,
                overrides TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 0,
                UNIQUE(provider_id, upstream_id)
            );
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS update_cache (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS activity (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, model TEXT NOT NULL,
                provider TEXT NOT NULL, status INTEGER NOT NULL, duration_ms REAL NOT NULL,
                first_token_ms REAL, input_tokens INTEGER, output_tokens INTEGER,
                error TEXT
            );
        """)
        existing = {row[1] for row in self.db.execute("PRAGMA table_info(activity)")}
        for name, column_type in ACTIVITY_METRICS.items():
            if name not in existing:
                self.db.execute(f"ALTER TABLE activity ADD COLUMN {name} {column_type}")
        self.db.execute("PRAGMA user_version=3")
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def providers(self) -> list[dict]:
        with self.lock:
            return [
                {**dict(r), "has_key": bool(r["has_key"])}
                for r in self.db.execute("SELECT * FROM providers ORDER BY rowid")
            ]

    def provider(self, provider_id: str) -> dict | None:
        return next((p for p in self.providers() if p["id"] == provider_id), None)

    def save_provider(self, data: dict, provider_id: str | None = None) -> dict:
        provider_id = provider_id or uuid.uuid4().hex[:12]
        with self.lock, self.db:
            self.db.execute(
                """
                INSERT INTO providers(id,name,kind,base_url,has_key) VALUES(?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,
                base_url=excluded.base_url,has_key=excluded.has_key
            """,
                (provider_id, data["name"], data["kind"], data["base_url"], data["has_key"]),
            )
        return self.provider(provider_id)

    def delete_provider(self, provider_id: str):
        with self.lock, self.db:
            self.db.execute("DELETE FROM providers WHERE id=?", (provider_id,))

    def refresh(self, provider_id: str, catalog: list[dict], error: str | None = None):
        with self.lock, self.db:
            if error:
                self.db.execute("UPDATE providers SET error=? WHERE id=?", (error, provider_id))
                return
            for model in catalog:
                upstream_id = model["upstream_id"]
                self.db.execute(
                    """
                    INSERT INTO models(id,provider_id,upstream_id,metadata) VALUES(?,?,?,?)
                    ON CONFLICT(provider_id,upstream_id) DO UPDATE SET metadata=excluded.metadata
                """,
                    (f"{provider_id}/{upstream_id}", provider_id, upstream_id, json.dumps(model)),
                )
            self.db.execute(
                "UPDATE providers SET refreshed_at=?,error=NULL WHERE id=?", (utcnow(), provider_id)
            )

    def models(self, enabled_only=False) -> list[dict]:
        with self.lock:
            rows = self.db.execute("""
                SELECT m.*,p.name AS provider_name,p.kind AS provider_kind
                FROM models m JOIN providers p ON p.id=m.provider_id ORDER BY p.name,m.upstream_id
            """).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            if enabled_only and not data["enabled"]:
                continue
            metadata = json.loads(data.pop("metadata"))
            overrides = json.loads(data.pop("overrides"))
            result.append(
                {
                    **metadata,
                    **overrides,
                    **data,
                    "enabled": bool(data["enabled"]),
                    "overridden_fields": list(overrides),
                }
            )
        return result

    def model(self, model_id: str, enabled_only=False) -> dict | None:
        models = self.models(enabled_only)
        exact = next((m for m in models if m["id"] == model_id), None)
        # Some Ollama clients add :latest. Exact matches always take precedence.
        if not exact and model_id.endswith(":latest"):
            exact = next((m for m in models if m["id"] == model_id[:-7]), None)
        return exact

    def add_model(self, provider_id: str, data: dict):
        model_id = f"{provider_id}/{data['upstream_id']}"
        with self.lock, self.db:
            self.db.execute(
                """
                INSERT INTO models(id,provider_id,upstream_id,metadata,enabled) VALUES(?,?,?,?,?)
            """,
                (model_id, provider_id, data["upstream_id"], json.dumps(data), data.get("enabled", False)),
            )
        return self.model(model_id)

    def patch_model(self, model_id: str, patch: dict):
        with self.lock, self.db:
            row = self.db.execute("SELECT overrides FROM models WHERE id=?", (model_id,)).fetchone()
            if row is None:
                return None
            overrides = json.loads(row[0])
            if "enabled" in patch:
                self.db.execute(
                    "UPDATE models SET enabled=? WHERE id=?", (bool(patch.pop("enabled")), model_id)
                )
            overrides.update(patch)
            self.db.execute("UPDATE models SET overrides=? WHERE id=?", (json.dumps(overrides), model_id))
        return self.model(model_id)

    def settings(self) -> Settings:
        with self.lock:
            row = self.db.execute("SELECT data FROM settings WHERE id=1").fetchone()
        return Settings.model_validate_json(row[0]) if row else Settings()

    def update_state(self) -> dict:
        with self.lock:
            row = self.db.execute("SELECT data FROM update_cache WHERE id=1").fetchone()
        try:
            value = json.loads(row[0]) if row else {}
        except (ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def save_update_state(self, data: dict):
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO update_cache VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (json.dumps(data),),
            )

    def save_settings(self, settings: Settings):
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (settings.model_dump_json(),),
            )

    def record(
        self,
        *,
        model: str,
        provider: str,
        status: int,
        duration_ms: float,
        first_token_ms: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        error: str | None = None,
        statistics: dict | None = None,
    ):
        values = {
            "id": uuid.uuid4().hex,
            "created_at": utcnow(),
            "model": model,
            "provider": provider,
            "status": status,
            "duration_ms": duration_ms,
            "first_token_ms": first_token_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "error": error,
            **{key: (statistics or {}).get(key) for key in ACTIVITY_METRICS},
        }
        with self.lock, self.db:
            self.db.execute(
                f"INSERT INTO activity ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                tuple(values.values()),
            )
            self.db.execute(
                "DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY rowid DESC LIMIT 1000)"
            )

    def activity(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM activity ORDER BY rowid DESC LIMIT 100")]

    def export(self) -> dict:
        return {
            "version": 1,
            "providers": self.providers(),
            "models": self.models(),
            "settings": self.settings().model_dump(),
        }
