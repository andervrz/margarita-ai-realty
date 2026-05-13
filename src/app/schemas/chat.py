# src/app/schemas/chat.py
"""Schemas Pydantic para Chat."""
from src.app.core.constants import Language, QualificationStage, BookingStep
from pydantic import BaseModel, Field, ConfigDict
from src.app.schemas.property import PropertyChatSummary



class ChatRequest(BaseModel):
    """Request de mensaje del usuario."""
    
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = None


class ChatResponse(BaseModel):
    """Respuesta del chatbot al usuario."""
    
    message: str
    session_id: str
    stage: QualificationStage # ← tipado y validado automáticamente por Pydantic
    properties: list[PropertySummary] | None = None
    booking_step: BookingStep | None = None


class SessionState(BaseModel):
    """Estado actual de la sesión."""
    model_config = ConfigDict(from_attributes=True)
    
    session_id: str
    tenant_id: str
    language: Language
    qualification_score: int
    is_booking_active: bool
    message_count: int
    booking_step: BookingStep | None = None


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
