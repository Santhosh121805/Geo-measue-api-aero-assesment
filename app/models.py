"""Database models for uploaded files and their extracted features."""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UploadedFile(Base):
    """Processing state and metadata for one uploaded source file."""

    __tablename__ = "uploaded_files"
    __table_args__ = (Index("ix_uploaded_file_hash_status", "file_hash", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    crs: Mapped[str | None] = mapped_column(String(255))
    projected_crs_used: Mapped[str | None] = mapped_column(Text)
    feature_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    features: Mapped[list["Feature"]] = relationship(
        back_populates="uploaded_file", cascade="all, delete-orphan"
    )


class Feature(Base):
    """A feature geometry, properties, and calculated measurements."""

    __tablename__ = "features"
    __table_args__ = (Index("ix_feature_file_geometry", "file_id", "geometry_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="CASCADE"), nullable=False
    )
    feature_index: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry_type: Mapped[str] = mapped_column(String(40), nullable=False)
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_supported: Mapped[bool] = mapped_column(Boolean, nullable=False)
    area_m2: Mapped[float | None] = mapped_column(Float)
    length_m: Mapped[float | None] = mapped_column(Float)
    geodesic_area_m2: Mapped[float | None] = mapped_column(Float)
    geodesic_length_m: Mapped[float | None] = mapped_column(Float)
    difference_percent: Mapped[float | None] = mapped_column(Float)
    was_repaired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    uploaded_file: Mapped[UploadedFile] = relationship(back_populates="features")