"""Application settings loaded from environment variables."""

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass
class Settings:
    """Runtime configuration for uploads, archives, and persistence."""

    upload_dir: Path = Path(os.getenv("UPLOAD_DIR", "uploads"))
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/geo_measure.db")
    max_file_size_bytes: int = 50 * 1024 * 1024
    max_archive_size_bytes: int = 250 * 1024 * 1024
    max_archive_entries: int = 1000
    upload_chunk_size_bytes: int = 1024 * 1024


settings = Settings()