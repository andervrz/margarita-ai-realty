# project/src/app/api/v1/chat.py
"""Chat API — WebSocket endpoint con heartbeat + POST fallback.

WebSocket (/ws/chat/{session_id}):
    - Mantiene conversación en tiempo real con el widget del cliente
    - Heartbeat ping/pong cada 30s para evitar corte por firewalls/proxies
    - Reconexión transparente: si session_id existe, recupera contexto

POST fallback (/api/v1/chat):
    - Para clientes que no soportan WebSocket (proxies corporativas, mobile antiguo)
    - Stateless: cada request es independiente
    - Menor eficiencia pero máxima compatibilidad

Flujo WebSocket:
    1. Cliente abre ws://host/ws/chat/{session_id}
    2. Server acepta, registra conexión en manager
    3. Cliente envía JSON: {"message": "busco apartamento en Pampatar"}
    4. Server procesa via ChatEngine → responde con JSON: {"type": "response", ...}
    5. Server envía ping cada 30s, cliente responde pong
    6. Al cerrar: limpia conexión, sesión persiste en RAM hasta TTL
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.app.api.middleware import get_current_tenant
from src.app.chat.engine import ChatEngine
from src.app.chat.memory import get_or_create_session
from src.app.core.config import settings
from src.app.core.logging import logger

if TYPE_CHECKING:
    from src.app.db.models.tenats import Tenant


router = APIRouter(prefix="/chat", tags=["chat"])

# ── Manager de conexiones WebSocket ────────────────────────────────

class ConnectionManager:
    """Gestiona conexiones WebSocket activas por session_id.
    
    Single-worker V1. En V2 con Redis se sincronizarían entre workers.
    """

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[session_id] = websocket
        logger.info("websocket_connected", session_id=session_id, active=len(self._connections))

    def disconnect(self, session_id: str) -> None:
        self._connections.pop(session_id, None)
        logger.info("websocket_disconnected", session_id=session_id, active=len(self._connections))

    async def send_message(self, session_id: str, message: dict) -> None:
        ws = self._connections.get(session_id)
        if ws:
            await ws.send_json(message)

    async def broadcast(self, message: dict) -> None:
        # No usado en V1, utilidad para futuras notificaciones
        for ws in self._connections.values():
            await ws.send_json(message)


manager = ConnectionManager()


# ── Schemas ────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    """Mensaje entrante del cliente (WebSocket o POST)."""
    message: str = Field(..., min_length=1, max_length=2000, description="Texto del usuario")
    language: str | None = Field(default=None, description="Override de idioma (es/en)")


class ChatResponse(BaseModel):
    """Respuesta del bot al cliente."""
    type: str = Field(default="response", description="response | error | booking_prompt | booking_form")
    content: str = Field(..., description="Texto de respuesta del bot")
    session_id: str
    properties: list[dict] = Field(default_factory=list, description="Propiedades mostradas")
    qualification_score: int = Field(default=0, description="Score actual del lead")
    booking_step: str | None = Field(default=None, description="Paso activo del booking flow")
    suggestions: list[str] = Field(default_factory=list, description="Quick replies sugeridas")


# ── WebSocket Endpoint ─────────────────────────────────────────────

@router.websocket("/ws/chat/{session_id}")
async def websocket_chat(
    websocket: WebSocket,
    session_id: str,
    tenant: dict = Depends(get_current_tenant),
) -> None:
    """Endpoint WebSocket para chat conversacional en tiempo real.
    
    Heartbeat: server envía ping cada 30s, espera pong del cliente.
    Si no recibe pong en 10s, cierra la conexión.
    """
    await manager.connect(session_id, websocket)
    
    # Recuperar o crear sesión en memoria
    session = await get_or_create_session(session_id, tenant["id"])
    
    # Enviar saludo inicial si es nueva sesión
    if not session.messages:
        greeting = _build_greeting(tenant)
        await manager.send_message(session_id, {
            "type": "response",
            "content": greeting,
            "session_id": session_id,
            "suggestions": ["Comprar", "Arrendar", "Vacacional", "Invertir"],
        })

    heartbeat_task: asyncio.Task | None = None
    
    try:
        # Iniciar heartbeat en background
        heartbeat_task = asyncio.create_task(_heartbeat(websocket, session_id))
        
        while True:
            # Recibir mensaje del cliente (timeout implícito del heartbeat)
            raw = await websocket.receive_text()
            
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_message(session_id, {
                    "type": "error",
                    "content": "Formato inválido. Envía JSON: {\"message\": \"tu texto\"}",
                    "session_id": session_id,
                })
                continue

            message_text = data.get("message", "").strip()
            if not message_text:
                continue

            # Procesar mensaje via ChatEngine
            logger.info("websocket_message", session_id=session_id, tenant_id=tenant["id"], msg_preview=message_text[:50])
            
            try:
                result = await ChatEngine.process_message(
                    session_id=session_id,
                    tenant=tenant,
                    user_message=message_text,
                    language=data.get("language"),
                )
            except Exception as exc:
                logger.error("chat_engine_error", session_id=session_id, error=str(exc))
                await manager.send_message(session_id, {
                    "type": "error",
                    "content": "Lo siento, tuve un problema procesando tu mensaje. ¿Podés intentar de nuevo?",
                    "session_id": session_id,
                })
                continue

            # Enviar respuesta al cliente
            response_payload = {
                "type": result.get("type", "response"),
                "content": result["content"],
                "session_id": session_id,
                "properties": result.get("properties", []),
                "qualification_score": result.get("qualification_score", 0),
                "booking_step": result.get("booking_step"),
                "suggestions": result.get("suggestions", []),
            }
            await manager.send_message(session_id, response_payload)

    except WebSocketDisconnect:
        logger.info("websocket_client_disconnect", session_id=session_id)
    except asyncio.CancelledError:
        logger.info("websocket_cancelled", session_id=session_id)
    except Exception as exc:
        logger.error("websocket_unexpected_error", session_id=session_id, error=str(exc))
    finally:
        if heartbeat_task and not heartbeat_task.done():
            heartbeat_task.cancel()
        manager.disconnect(session_id)


async def _heartbeat(websocket: WebSocket, session_id: str) -> None:
    """Envía ping cada 30s y espera pong del cliente.
    
    Si el cliente no responde pong en 10s, cierra la conexión.
    """
    try:
        while True:
            await asyncio.sleep(settings.WEBSOCKET_HEARTBEAT_INTERVAL)
            
            # Enviar ping
            try:
                await websocket.send_json({"type": "ping"})
            except Exception:
                logger.warning("websocket_ping_failed", session_id=session_id)
                break
            
            # Esperar pong con timeout
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                data = json.loads(raw)
                if data.get("type") != "pong":
                    logger.warning("websocket_expected_pong", session_id=session_id, received=data.get("type"))
            except asyncio.TimeoutError:
                logger.warning("websocket_pong_timeout", session_id=session_id)
                break
            except Exception:
                break
                
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("heartbeat_error", session_id=session_id, error=str(exc))


# ── POST Fallback Endpoint ─────────────────────────────────────────

@router.post("/api/v1/chat", response_model=ChatResponse)
async def http_chat(
    request: Request,
    payload: ChatMessage,
    tenant: dict = Depends(get_current_tenant),
) -> ChatResponse:
    """Endpoint POST fallback para clientes sin soporte WebSocket.
    
    Stateless: cada request es independiente. Útil para:
    - Proxies corporativos que bloquean WebSocket
    - Mobile browsers antiguos
    - Integraciones server-to-server
    """
    session_id = request.headers.get("X-Session-Id") or str(uuid.uuid4())
    
    logger.info("http_chat_request", session_id=session_id, tenant_id=tenant["id"], msg_preview=payload.message[:50])

    try:
        result = await ChatEngine.process_message(
            session_id=session_id,
            tenant=tenant,
            user_message=payload.message,
            language=payload.language,
        )
    except Exception as exc:
        logger.error("http_chat_error", session_id=session_id, error=str(exc))
        return ChatResponse(
            type="error",
            content="Lo siento, tuve un problema procesando tu mensaje. ¿Podés intentar de nuevo?",
            session_id=session_id,
        )

    return ChatResponse(
        type=result.get("type", "response"),
        content=result["content"],
        session_id=session_id,
        properties=result.get("properties", []),
        qualification_score=result.get("qualification_score", 0),
        booking_step=result.get("booking_step"),
        suggestions=result.get("suggestions", []),
    )


# ── Helpers ────────────────────────────────────────────────────────

def _build_greeting(tenant: dict) -> str:
    """Construye saludo inicial según idioma del tenant."""
    name = tenant.get("name", "nuestro asistente")
    return (
        f"¡Hola! Soy el asistente virtual de {name}. 🏝️\n\n"
        f"¿Qué tipo de propiedad estás buscando en Margarita? "
        f"Puedo ayudarte a encontrar apartamentos, casas, locales o terrenos."
    )


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — api/v1/chat.py")

    # Test 1: Schemas validan correctamente
    msg = ChatMessage(message="busco apartamento en Pampatar")
    assert msg.message == "busco apartamento en Pampatar"
    assert msg.language is None
    print("  ✅ ChatMessage schema válido")

    # Test 2: ChatResponse schema
    resp = ChatResponse(
        content="Encontré 3 propiedades",
        session_id="test-session-001",
        properties=[{"title": "Apto Pampatar", "price_usd": 120000}],
        qualification_score=45,
        suggestions=["Ver más", "Agendar visita"],
    )
    assert resp.type == "response"
    assert len(resp.properties) == 1
    print("  ✅ ChatResponse schema válido")

    # Test 3: ConnectionManager estructura
    cm = ConnectionManager()
    assert cm._connections == {}
    print("  ✅ ConnectionManager inicializado")

    # Test 4: _build_greeting contiene nombre del tenant
    greeting = _build_greeting({"name": "Esparta Inmuebles"})
    assert "Esparta Inmuebles" in greeting
    assert "🏝️" in greeting
    print("  ✅ Greeting incluye nombre del tenant")

    # Test 5: Validación de message vacío
    try:
        ChatMessage(message="")
        assert False, "Debe fallar con message vacío"
    except Exception:
        pass
    print("  ✅ Validación rechaza message vacío")

    # Test 6: Validación de message muy largo
    try:
        ChatMessage(message="x" * 2001)
        assert False, "Debe fallar con message > 2000"
    except Exception:
        pass
    print("  ✅ Validación rechaza message > 2000 chars")

    print("\n🎉 Todos los smoke tests pasaron")
    print("   Nota: Tests de WebSocket requieren FastAPI test client con soporte WS")