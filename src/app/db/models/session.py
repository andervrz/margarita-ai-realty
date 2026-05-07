# src/app/db/models/session.py
"""Modelo Session — sesión de chat del usuario."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Index
from src.app.db.base import Base


class Session(Base):
    """Sesión de chat con estado de calificación y booking."""
    
    __tablename__ = "sessions"
    
    __table_args__ = (
        Index("idx_sessions_tenant_active", "tenant_id", "last_active_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=False)
    
    language: Mapped[str] = mapped_column(String, default="es")
    qualification_score: Mapped[int] = mapped_column(Integer, default=0)
    is_booking_active: Mapped[int] = mapped_column(Integer, default=0)
    booking_step: Mapped[str | None] = mapped_column(String, nullable=True)
    
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_active_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — session.py")
    
    s = Session(tenant_id="tenant-123", language="en")
    assert s.language == "en"
    assert s.qualification_score == 0
    assert s.is_booking_active == 0
    assert s.booking_step is None
    print(f"  ✅ Session creada: {s.id[:8]}... (lang={s.language})")
    print("\n🎉 Smoke test pasó")