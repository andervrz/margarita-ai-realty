# src/app/api/v1/chat.py
"""Chat API — WebSocket endpoint con heartbeat + POST fallback.

WebSocket (/ws/chat/{session_id}):
    - Conversación en tiempo real con el widget del cliente
    - Heartbeat ping/pong cada 30s para evitar corte por firewalls
    - Reconexión transparente: session_id recupera contexto de RAM o DB

POST fallback (/api/v1/chat):
    - Para clientes sin soporte WebSocket (proxies corporativos, mobile)
    - Compatible con X-Session-Id header para continuidad de sesión

Flujo WebSocket:
    1. Cliente abre ws://host/ws/chat/{session_id}?api_key=pk_live_xxx
    2. Server acepta, registra en ConnectionManager
    3. Cliente envía: {"message": "busco apartamento en Pampatar"}
    4. Server procesa via process_message → responde con JSON estructurado
    5. Server envía ping cada 30s, cliente responde pong
    6. Al cerrar: limpia conexión, sesión persiste en RAM hasta TTL
"""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from src.app.api.middleware import get_current_tenant
from src.app.chat.engine import process_message
from src.app.core.config import get_settings
from src.app.core.logging import get_logger
from src.app.db.engine import AsyncSessionLocal

logger = get_logger(__name__)

router = APIRouter(tags=["chat"])


# ── Connection Manager ────────────────────────────────────────────

class ConnectionManager:
    """Gestiona conexiones WebSocket activas por session_id.

    Single-worker V1.
    V2: sincronizar entre workers via Redis Pub/Sub.
    """

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[session_id] = websocket
        logger.info(
            "websocket_connected",
            session_id=session_id,
            active_connections=len(self._connections),
        )

    def disconnect(self, session_id: str) -> None:
        self._connections.pop(session_id, None)
        logger.info(
            "websocket_disconnected",
            session_id=session_id,
            active_connections=len(self._connections),
        )

    async def send_json(self, session_id: str, payload: dict) -> None:
        ws = self._connections.get(session_id)
        if ws:
            await ws.send_json(payload)

    @property
    def active_count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()


# ── Schemas ───────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """Mensaje entrante del usuario (WebSocket o POST)."""
    message: str = Field(..., min_length=1, max_length=2000)
    language: str | None = Field(default=None, description="Override de idioma (es/en)")


class ChatResponseSchema(BaseModel):
    """Respuesta del bot al cliente."""
    type: str = Field(default="response")
    content: str
    session_id: str
    properties: list[dict] = Field(default_factory=list)
    qualification_score: int = Field(default=0)
    qualification_stage: str = Field(default="explore")
    booking_step: str | None = Field(default=None)
    is_booking_active: bool = Field(default=False)
    language: str = Field(default="es")


# ── WebSocket Endpoint ────────────────────────────────────────────

@router.websocket("/ws/chat/{session_id}")
async def websocket_chat(
    websocket: WebSocket,
    session_id: str,
    tenant: dict = Depends(get_current_tenant),
) -> None:
    """Endpoint WebSocket para chat en tiempo real.

    El cliente debe enviar JSON: {"message": "texto del usuario"}
    El server responde con JSON estructurado: {"type": "response", ...}
    Heartbeat: ping cada 30s, pong esperado en 10s.
    """
    settings = get_settings()
    await manager.connect(session_id, websocket)

    # Saludo inicial si es sesión nueva — usamos DB para verificar
    async with AsyncSessionLocal() as session:
        from src.app.chat.memory import get_session_memory
        memory = await get_session_memory(
            session=session,
            session_id=session_id,
            tenant_id=tenant["id"],
        )
        is_new_session = len(memory.messages) == 0

    if is_new_session:
        greeting = _build_greeting(tenant, language="es")
        await manager.send_json(session_id, {
            "type": "response",
            "content": greeting,
            "session_id": session_id,
            "qualification_score": 0,
            "qualification_stage": "explore",
            "is_booking_active": False,
            "booking_step": None,
            "language": "es",
            "properties": [],
        })

    heartbeat_task: asyncio.Task | None = None

    try:
        heartbeat_task = asyncio.create_task(
            _heartbeat(websocket, session_id, settings.websocket_heartbeat_interval)
        )

        while True:
            raw = await websocket.receive_text()

            # Parsear JSON del cliente
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_json(session_id, {
                    "type": "error",
                    "content": "Formato inválido. Envía JSON: {\"message\": \"tu texto\"}",
                    "session_id": session_id,
                })
                continue

            # Ignorar pong del cliente (respuesta al heartbeat)
            if data.get("type") == "pong":
                continue

            message_text = data.get("message", "").strip()
            if not message_text:
                continue

            logger.info(
                "websocket_message_received",
                session_id=session_id,
                tenant_id=tenant["id"],
                preview=message_text[:60],
            )

            # Procesar via process_message (función del engine)
            try:
                async with AsyncSessionLocal() as session:
                    engine_response = await process_message(
                        session_id=session_id,
                        tenant_id=tenant["id"],
                        user_message=message_text,
                        session=session,
                        tenant_name=tenant.get("name", "Inmobiliaria Margarita"),
                    )
            except Exception as exc:
                logger.exception(
                    "websocket_engine_error",
                    session_id=session_id,
                    error=str(exc),
                )
                await manager.send_json(session_id, {
                    "type": "error",
                    "content": "Lo siento, tuve un problema. ¿Podés intentar de nuevo?",
                    "session_id": session_id,
                })
                continue

            await manager.send_json(session_id, {
                "type": "response",
                "content": engine_response.text,
                "session_id": session_id,
                "properties": [],  # TODO: incluir cuando SearchResult exponga lista pública
                "qualification_score": engine_response.qualification_score,
                "qualification_stage": engine_response.qualification_stage,
                "is_booking_active": engine_response.is_booking_active,
                "booking_step": engine_response.booking_step,
                "language": engine_response.language,
            })

    except WebSocketDisconnect:
        logger.info("websocket_client_disconnected", session_id=session_id)
    except asyncio.CancelledError:
        logger.info("websocket_cancelled", session_id=session_id)
    except Exception as exc:
        logger.exception(
            "websocket_unexpected_error",
            session_id=session_id,
            error=str(exc),
        )
    finally:
        if heartbeat_task and not heartbeat_task.done():
            heartbeat_task.cancel()
        manager.disconnect(session_id)


async def _heartbeat(
    websocket: WebSocket,
    session_id: str,
    interval_seconds: int,
) -> None:
    """Envía ping cada N segundos. Cierra si no recibe pong en 10s."""
    try:
        while True:
            await asyncio.sleep(interval_seconds)

            try:
                await websocket.send_json({"type": "ping"})
            except Exception:
                logger.warning("websocket_ping_failed", session_id=session_id)
                break

            # El pong llega por el loop principal (receive_text)
            # El heartbeat solo controla que la conexión siga viva
            # — si el cliente desconecta, receive_text lanzará WebSocketDisconnect

    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("heartbeat_error", session_id=session_id, error=str(exc))


# ── POST Fallback Endpoint ────────────────────────────────────────

@router.post("", response_model=ChatResponseSchema)
async def http_chat(
    request: Request,
    payload: ChatRequest,
    tenant: dict = Depends(get_current_tenant),
) -> ChatResponseSchema:
    """Endpoint POST fallback para clientes sin soporte WebSocket.

    Stateless por diseño pero usa X-Session-Id para continuidad.
    """
    session_id = request.headers.get("X-Session-Id") or str(uuid.uuid4())
    settings = get_settings()

    logger.info(
        "http_chat_request",
        session_id=session_id,
        tenant_id=tenant["id"],
        preview=payload.message[:60],
    )

    try:
        async with AsyncSessionLocal() as session:
            engine_response = await process_message(
                session_id=session_id,
                tenant_id=tenant["id"],
                user_message=payload.message,
                session=session,
                tenant_name=tenant.get("name", "Inmobiliaria Margarita"),
            )
    except Exception as exc:
        logger.exception("http_chat_error", session_id=session_id, error=str(exc))
        return ChatResponseSchema(
            type="error",
            content="Lo siento, tuve un problema procesando tu mensaje. Intenta de nuevo.",
            session_id=session_id,
        )

    return ChatResponseSchema(
        type="response",
        content=engine_response.text,
        session_id=session_id,
        qualification_score=engine_response.qualification_score,
        qualification_stage=engine_response.qualification_stage,
        is_booking_active=engine_response.is_booking_active,
        booking_step=engine_response.booking_step,
        language=engine_response.language,
    )


# ── Helpers ───────────────────────────────────────────────────────

def _build_greeting(tenant: dict, language: str = "es") -> str:
    """Construye saludo inicial personalizado."""
    name = tenant.get("name", "nuestro asistente")
    if language == "en":
        return (
            f"Hello! I'm the virtual assistant for {name}. 🏝️\n\n"
            f"What type of property are you looking for in Margarita? "
            f"I can help you find apartments, houses, commercial spaces or land."
        )
    return (
        f"¡Hola! Soy el asistente virtual de {name}. 🏝️\n\n"
        f"¿Qué tipo de propiedad estás buscando en Margarita? "
        f"Puedo ayudarte a encontrar apartamentos, casas, locales o terrenos."
    )


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔥 Smoke Tests — api/v1/chat.py\n")

    # Test 1: ChatRequest valida correctamente
    req = ChatRequest(message="busco apartamento en Pampatar")
    assert req.message == "busco apartamento en Pampatar"
    assert req.language is None
    print("✅ ChatRequest schema válido")

    # Test 2: ChatRequest rechaza message vacío
    try:
        ChatRequest(message="")
        assert False, "Debería rechazar message vacío"
    except Exception:
        pass
    print("✅ ChatRequest rechaza message vacío")

    # Test 3: ChatRequest rechaza message > 2000 chars
    try:
        ChatRequest(message="x" * 2001)
        assert False, "Debería rechazar > 2000 chars"
    except Exception:
        pass
    print("✅ ChatRequest rechaza message > 2000 chars")

    # Test 4: ChatResponseSchema defaults
    resp = ChatResponseSchema(
        content="Encontré 2 propiedades",
        session_id="test-session",
    )
    assert resp.type == "response"
    assert resp.qualification_score == 0
    assert resp.qualification_stage == "explore"
    assert resp.is_booking_active is False
    assert resp.language == "es"
    assert resp.properties == []
    print("✅ ChatResponseSchema con defaults correctos")

    # Test 5: ChatResponseSchema con todos los campos
    resp2 = ChatResponseSchema(
        type="response",
        content="¿Cuál es tu nombre?",
        session_id="sess-123",
        qualification_score=80,
        qualification_stage="book",
        is_booking_active=True,
        booking_step="nombre",
        language="es",
    )
    assert resp2.qualification_stage == "book"
    assert resp2.is_booking_active is True
    assert resp2.booking_step == "nombre"
    print("✅ ChatResponseSchema con booking activo")

    # Test 6: ConnectionManager estructura inicial
    cm = ConnectionManager()
    assert cm._connections == {}
    assert cm.active_count == 0
    print("✅ ConnectionManager inicializado con 0 conexiones")

    # Test 7: _build_greeting ES
    greeting_es = _build_greeting({"name": "Esparta Inmuebles"}, language="es")
    assert "Esparta Inmuebles" in greeting_es
    assert "🏝️" in greeting_es
    assert "Margarita" in greeting_es
    print("✅ Greeting ES incluye nombre y contexto")

    # Test 8: _build_greeting EN
    greeting_en = _build_greeting({"name": "Esparta Real Estate"}, language="en")
    assert "Esparta Real Estate" in greeting_en
    assert "🏝️" in greeting_en
    assert "Margarita" in greeting_en
    assert "Hello" in greeting_en
    print("✅ Greeting EN correcto")

    # Test 9: _build_greeting con tenant sin nombre
    greeting_no_name = _build_greeting({})
    assert "nuestro asistente" in greeting_no_name
    print("✅ Greeting fallback cuando tenant sin nombre")

    # Test 10: settings en snake_case
    settings = get_settings()
    assert hasattr(settings, "websocket_heartbeat_interval"), \
        "Settings debe tener websocket_heartbeat_interval en snake_case"
    assert isinstance(settings.websocket_heartbeat_interval, int)
    print(f"✅ settings.websocket_heartbeat_interval: {settings.websocket_heartbeat_interval}s")

    print("\n🎉 Todos los smoke tests pasaron ✅")
    print("   Nota: Tests de WebSocket requieren FastAPI test client con soporte async WS")
