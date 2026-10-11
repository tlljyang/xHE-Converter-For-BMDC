"""SQLite persistence: job queue (resume) + presets + settings.

The engine is the only writer. WAL mode keeps the UI reading while jobs run.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from xhe4bmdc.engine.models import ExportOptions, Job

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    output_name TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    is_xhe INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT '',
    error TEXT,
    options_json TEXT NOT NULL DEFAULT '{}',
    output_dir TEXT NOT NULL DEFAULT '',
    output_path TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);

CREATE TABLE IF NOT EXISTS presets (
    name TEXT PRIMARY KEY,
    options_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def default_db_path() -> Path:
    import sys
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Local" / "xHE4BMDC"
    else:
        base = Path.home() / "Library" / "Application Support" / "xHE4BMDC"
    base.mkdir(parents=True, exist_ok=True)
    return base / "xhe4bmdc.db"


class Store:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.path = Path(db_path) if db_path else default_db_path()
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------------- jobs ----------------

    def upsert_job(self, job: Job) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO jobs (id, project_id, source_path, output_name,
                       display_name, is_xhe, state, progress, stage, error,
                       options_json, output_dir, output_path, attempts,
                       created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       state=excluded.state, progress=excluded.progress,
                       stage=excluded.stage, error=excluded.error,
                       options_json=excluded.options_json,
                       output_name=excluded.output_name,
                       output_dir=excluded.output_dir,
                       output_path=excluded.output_path,
                       attempts=excluded.attempts,
                       updated_at=excluded.updated_at""",
                (job.id, job.project_id, job.source_path, job.output_name,
                 job.display_name, int(job.is_xhe), job.state, job.progress,
                 job.stage, job.error, json.dumps(job.options.to_dict()),
                 job.output_dir, job.output_path, job.attempts,
                 time.time(), time.time()))

    def load_jobs(self, states: Optional[tuple[str, ...]] = None) -> list[Job]:
        q = "SELECT * FROM jobs"
        if states:
            q += f" WHERE state IN ({','.join('?' * len(states))})"
        q += " ORDER BY created_at"
        with self._lock:
            rows = self._conn.execute(q, states or ()).fetchall()
        cols = [d[0] for d in self._conn.execute("SELECT * FROM jobs LIMIT 0").description]
        jobs = []
        for r in rows:
            d = dict(zip(cols, r))
            job = Job(
                id=d["id"], project_id=d["project_id"],
                source_path=d["source_path"], output_name=d["output_name"],
                display_name=d["display_name"], is_xhe=bool(d["is_xhe"]),
                state=d["state"], progress=d["progress"], stage=d["stage"],
                error=d["error"], options=ExportOptions.from_dict(json.loads(d["options_json"])),
                output_dir=d["output_dir"], output_path=d["output_path"],
                attempts=d["attempts"])
            jobs.append(job)
        return jobs

    def resume_on_startup(self) -> list[Job]:
        """Queue-level resume: interrupted/queued jobs stay queued; done stays done."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE jobs SET state='queued', stage='', error=NULL "
                "WHERE state IN ('running')")
        return self.load_jobs()

    def delete_jobs(self, ids: list[str]) -> None:
        with self._lock, self._conn:
            self._conn.executemany("DELETE FROM jobs WHERE id=?", [(i,) for i in ids])

    def clear_finished(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM jobs WHERE state IN ('done','skipped','canceled')")

    # ---------------- presets ----------------

    def save_preset(self, name: str, options: ExportOptions) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO presets (name, options_json, created_at) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET options_json=excluded.options_json",
                (name, json.dumps(options.to_dict(), ensure_ascii=False), time.time()))

    def list_presets(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, options_json, created_at FROM presets ORDER BY name").fetchall()
        return [{"name": n, "options": ExportOptions.from_dict(json.loads(j)), "created_at": c}
                for n, j, c in rows]

    def delete_preset(self, name: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM presets WHERE name=?", (name,))

    # ---------------- settings ----------------

    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
