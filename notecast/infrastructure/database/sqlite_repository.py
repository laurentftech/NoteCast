import sqlite3
from pathlib import Path
from typing import List, Optional
import asyncio
from datetime import datetime

from notecast.core.interfaces import JobRepository
from notecast.core.models import User, Job, Episode


class SQLiteJobRepository(JobRepository):
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self, user: User) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    user_name TEXT NOT NULL,
                    feed_name TEXT NOT NULL,
                    feed_title TEXT NOT NULL,
                    episode_url TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    style TEXT,
                    notebook_id TEXT,
                    artifact_id TEXT,
                    duration INTEGER,
                    retries INTEGER,
                    error_message TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            for col, definition in [
                ("error_message", "TEXT"),
                ("source_url", "TEXT NOT NULL DEFAULT ''"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {definition}")
                except Exception:
                    pass
            conn.commit()

    def create_job(self, user: User, episode: Episode) -> Job:
        import uuid
        now = datetime.now()
        job_id = str(uuid.uuid4())[:8]

        new_job = Job(
            id=job_id,
            user_name=user.name,
            feed_name=episode.feed_name,
            feed_title=episode.feed_title,
            episode_url=episode.url,
            source_url=episode.source_url,
            title=episode.title,
            status="pending",
            style=episode.style,
            created_at=now,
            updated_at=now,
        )
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                new_job.id, new_job.user_name, new_job.feed_name, new_job.feed_title,
                new_job.episode_url, new_job.source_url, new_job.title, new_job.status,
                new_job.style, new_job.notebook_id, new_job.artifact_id, new_job.duration,
                new_job.retries, None,
                new_job.created_at.isoformat(), new_job.updated_at.isoformat()
            ))
            conn.commit()
        return new_job

    async def get_next_pending(self, user: User) -> Optional[Job]:
        await asyncio.sleep(0)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE user_name=? AND status='pending' "
                "ORDER BY created_at LIMIT 1",
                (user.name,)
            ).fetchone()
        return Job(**dict(row)) if row else None

    def update_job(self, user: User, job_id: str, **fields) -> None:
        set_clauses = [f"{key}=?" for key in fields.keys()]
        query = f"UPDATE jobs SET {', '.join(set_clauses)} WHERE id=?"
        values = list(fields.values()) + [job_id]
        with self._conn() as conn:
            conn.execute(query, values)
            conn.commit()

    def get_done_jobs(self, user: User, feed_name: str) -> List[Job]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE user_name=? AND feed_name=? AND status='done'",
                (user.name, feed_name)
            ).fetchall()
        return [Job(**dict(row)) for row in rows]

    def get_generating_jobs(self, user: User) -> List[Job]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE user_name=? AND status='generating' AND notebook_id IS NOT NULL",
                (user.name,)
            ).fetchall()
        return [Job(**dict(row)) for row in rows]

    def episode_seen(self, user: User, episode_url: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE user_name=? AND episode_url=?",
                (user.name, episode_url)
            ).fetchone()
        return row is not None
