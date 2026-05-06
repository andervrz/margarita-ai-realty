# src/app/db/models/message.py
"""Modelo Message — mensaje de chat."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Message(Base):
    """Mensaje individual en una conversación."""
    
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenants.id"), nullable=False)
    
    role: Mapped[str] = mapped_column(String, nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — message.py")
    
    m = Message(
        session_id="session-123",
        tenant_id="tenant-123",
        role="user",
        content="Busco apartamento en Pampatar",
    )
    assert m.role == "user"
    assert "Pampatar" in m.content
    print(f"  ✅ Message creado: {m.role} - '{m.content[:30]}...'")
    print("\n🎉 Smoke test pasó")