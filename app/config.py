"""Application settings loaded from environment variables."""

from dataclasses import dataclass, field
import os
from pathlib import Path


@dataclass
class Settings:
    """Runtime configuration for uploads, archives, and persistence."""

    upload_dir: Path = field(default_factory=lambda: Path(os.getenv("UPLOAD_DIR", "uploads")))
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/geo_measure.db")
    )
    max_file_size_bytes: int = field(
        default_factory=lambda: int(os.getenv("MAX_FILE_SIZE_BYTES", str(50 * 1024 * 1024)))
    )
    max_archive_size_bytes: int = field(
        default_factory=lambda: int(os.getenv("MAX_ARCHIVE_SIZE_BYTES", str(250 * 1024 * 1024)))
    )
    max_archive_entries: int = field(
        default_factory=lambda: int(os.getenv("MAX_ARCHIVE_ENTRIES", "1000"))
    )
    upload_chunk_size_bytes: int = 1024 * 1024


settings = Settings()