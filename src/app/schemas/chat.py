# src/app/schemas/chat.py
"""Schemas Pydantic para Chat."""

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Request de mensaje del usuario."""
    
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    """Respuesta del chatbot al usuario."""
    
    message: str
    session_id: str
    stage: str  # explore | qualify | book
    properties: list[dict] | None = None


class SessionState(BaseModel):
    """Estado actual de la sesión."""
    
    session_id: str
    tenant_id: str
    language: str
    qualification_score: int
    is_booking_active: bool
    message_count: int


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — schemas/chat.py")
    
    req = ChatRequest(message="Busco apartamento en Pampatar")
    assert req.message == "Busco apartamento en Pampatar"
    
    resp = ChatResponse(
        message="Encontré 2 propiedades",
        session_id="sess-123",
        stage="explore",
        properties=[{"id": "p1", "title": "Apto Pampatar"}],
    )
    assert resp.stage == "explore"
    
    state = SessionState(
        session_id="sess-123",
        tenant_id="tenant-123",
        language="es",
        qualification_score=25,
        is_booking_active=False,
        message_count=3,
    )
    assert state.qualification_score == 25
    print("  ✅ ChatRequest, ChatResponse, SessionState válidos")
    print("\n🎉 Smoke test pasó")
