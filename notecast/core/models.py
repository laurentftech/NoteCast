"""Domain models for the NoteCast application."""
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class User(BaseModel):
    """Unified user model - single source of truth for user data."""

    model_config = {"frozen": True}

    name: str
    email: str = ""

    # Paths
    auth_file: Path
    db_file: Path
    history_file: Path
    episodes_dir: Path
    feed_dir: Path  # directory; individual feed files live inside

    # Auth / feed access
    feed_token: str  # single token, single file: DATA_BASE/{name}/.feed_token

    # Webhook (optional, per-user or global fallback)
    webhook_url: str = ""
    webhook_headers: dict = {}
    webhook_link: str = ""


class Job(BaseModel):
    """Represents a podcast generation job."""

    id: str
    user_name: str
    feed_name: str
    feed_title: str
    episode_url: str       # MP3/enclosure URL — used for deduplication
    source_url: str = ""   # article page URL — used as NotebookLM source
    title: str
    status: Literal["pending", "processing", "generating", "done", "failed"]
    style: str = "deep-dive"
    instructions: str = ""
    language: str = "en"
    notebook_id: str | None = None
    artifact_id: str | None = None
    duration: int | None = None
    retries: int = 0
    max_retries: int = 1
    created_at: datetime
    updated_at: datetime


class Feed(BaseModel):
    """RSS feed configuration."""

    name: str
    title: str = ""
    url: str
    style: str = "deep-dive"
    instructions: str = ""
    language: str = "en"
    max_episodes: int = 1  # max new episodes to queue per poll


class Episode(BaseModel):
    """Podcast episode."""

    url: str             # MP3/enclosure URL — used for deduplication
    source_url: str = "" # article page URL — used as NotebookLM source
    title: str
    feed_name: str
    feed_title: str
    style: str
    instructions: str = ""
    language: str = "en"


class Artifact(BaseModel):
    """Generated audio artifact from NotebookLM."""

    id: str
    notebook_id: str
    local_path: Path | None = None
    duration: int | None = None

