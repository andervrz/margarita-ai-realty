# src/app/db/models/session.py
"""Modelo Session — sesión de chat del usuario."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Index, Boolean
from src.app.db.base import Base
from src.app.core.constants import Language

class Session(Base):
    """Sesión de chat con estado de calificación y booking."""
    
    __tablename__ = "sessions"
    
    __table_args__ = (
        Index("idx_sessions_tenant_active", "tenant_id", "last_active_at"),
        CheckConstraint(
        "booking_step IN ('name', 'email', 'phone', 'date', 'time', 'notes', 'confirm') OR booking_step IS NULL",
        name="ck_session_booking_step"
        )
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=True)
    
    language: Mapped[str] = mapped_column(String, default=Language.ES.value)
    qualification_score: Mapped[int] = mapped_column(Integer, default=0)
    is_booking_active: Mapped[bool] = mapped_column(Boolean, default=False)
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
    assert s.is_booking_active == False
    assert s.booking_step is None
    print(f"  ✅ Session creada: lang={s.language}, score={s.qualification_score}")
    print("\n🎉 Smoke test pasó")
