# src/app/db/models/ingestion_log.py
"""Modelo IngestionLog — registro de procesamiento de CSV."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IngestionLog(Base):
    """Log de ingestion de catálogo CSV."""
    
    __tablename__ = "ingestion_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=False)
    
    filename: Mapped[str] = mapped_column(String, nullable=False)
    file_checksum: Mapped[str] = mapped_column(String, nullable=False)
    
    # Estadísticas
    total_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inserted_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skipped_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    errors: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    status: Mapped[str] = mapped_column(String, nullable=False)  # success|partial|failed
    
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — ingestion_log.py")
    
    log = IngestionLog(
        tenant_id="tenant-123",
        filename="catalogo_junio.csv",
        file_checksum="a1b2c3d4",
        total_rows=50,
        valid_rows=48,
        inserted_rows=45,
        updated_rows=2,
        skipped_rows=1,
        failed_rows=2,
        status="partial",
    )
    assert log.filename == "catalogo_junio.csv"
    assert log.status == "partial"
    assert log.skipped_rows == 1
    print(f"  ✅ Log creado: {log.filename} ({log.status})")
    print("\n🎉 Smoke test pasó")