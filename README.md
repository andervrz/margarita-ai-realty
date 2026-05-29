```markdown
# 🏝️ Margarita AI Realty

Chatbot conversacional embebible para inmobiliarias en la **Isla de Margarita, Venezuela**.

El LLM razona en lenguaje natural. SQLite tiene la verdad estructural.
Python valida todo. El LLM nunca inventa propiedades.

---

## Stack

| Capa | Tecnología |
|------|-----------|
| API | FastAPI + Uvicorn (async) |
| Base de datos | SQLite WAL mode + sqlite-vec (vector search) |
| Embeddings | sentence-transformers `paraphrase-multilingual-MiniLM-L12-v2` |
| LLM | Groq LLaMA 3.3-70b (primary) → Gemini (fallback) via LiteLLM |
| Notificaciones | WhatsApp Meta Cloud API + Email SMTP (aiosmtplib) |
| Agendamiento | Google Calendar API v3 |
| Package manager | uv |

---

## Prerequisitos

```bash
# Python 3.11+
python --version

# uv (instalador oficial)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Verificar
uv --version
```

Cuentas requeridas (todas tienen tier gratuito):
- [Groq Console](https://console.groq.com) → `GROQ_API_KEY`
- [Google AI Studio](https://aistudio.google.com) → `GEMINI_API_KEY` (fallback)
- Gmail con App Password habilitado → SMTP

---

## Setup Local

### 1. Clonar e instalar dependencias

```bash
git clone https://github.com/andervrz/margarita-ai-realty.git
cd margarita-ai-realty

# Instala dependencias + paquete editable (src/app → importable como `app`)
uv sync
```

### 2. Variables de entorno

```bash
cp .env.example .env
```

Editar `.env` con los valores reales. Mínimo para funcionar en desarrollo:

```bash
APP_ENV=development
GROQ_API_KEY=gsk_...        # console.groq.com
SECRET_KEY=$(openssl rand -hex 32)
```

### 3. Fix de imports (una sola vez)

```bash
find src/ -name "*.py" -exec sed -i 's/from src\.app\./from app./g' {} +
```

Verificar que quedó limpio:

```bash
grep -r "from src\.app\." src/ --include="*.py"
# Debe retornar vacío
```

### 4. Crear tablas

```bash
uv run alembic upgrade head
# INFO [alembic] Running upgrade -> 001, Initial schema
```

### 5. Arrancar el servidor

```bash
uv run uvicorn app.main:app --reload --port 8000
```

Verificar en: [http://localhost:8000/docs](http://localhost:8000/docs)

### 6. Demo del chatbot

Abrir `demo/index.html` directamente en el browser.
El widget conecta a `ws://localhost:8000/api/v1/ws/chat/{session_id}`.

---

## Configuración por Fases

En desarrollo (`APP_ENV=development`) el sistema usa un tenant hardcodeado
sin validación de API key — sin fricción para las fases 0-8.

### Fase 1: Subir propiedades al catálogo

```bash
# Via API (servidor corriendo)
curl -X POST http://localhost:8000/api/v1/ingestion \
  -F "file=@propiedades.csv"

# La respuesta incluye estadísticas de ingestion
# { "inserted_rows": 50, "updated_rows": 0, "skipped_rows": 0, "status": "success" }
```

Formato CSV esperado (ver `.env.example` para columnas completas):

```csv
external_id,title,property_type,status,price_usd,location_zone,bedrooms,bathrooms,area_m2,vista_al_mar,frente_playa,uso_vacacional,description_es
PROP001,Apartamento Vista al Mar,venta,disponible,150000,Pampatar,3,2,85,true,false,true,Hermoso apto con vista panorámica
```

### Fase 9+: Multi-tenant completo

En producción (`APP_ENV=production`), cada request requiere `X-API-Key` header.
Ver [Configuración Multi-Tenant](#multi-tenant).

---

## Tests

```bash
# Suite completa
uv run pytest tests/

# Por nivel
uv run pytest tests/unit/           # Rápidos — sin I/O externo
uv run pytest tests/integration/    # Con SQLite en memoria
uv run pytest tests/e2e/            # FastAPI AsyncClient completo

# Con output verbose
uv run pytest tests/ -v

# Un archivo específico
uv run pytest tests/unit/test_filter_extractor.py -v

# Con cobertura (opcional)
uv run pytest tests/ --cov=app --cov-report=term-missing
```

Los tests usan `asyncio_mode = "auto"` — no se necesita el decorador
`@pytest.mark.asyncio` en funciones async.

---

## Estructura del Proyecto

```
margarita-ai-realty/
│
├── pyproject.toml              # Dependencias uv + pytest config
├── alembic.ini                 # Configuración de migraciones
├── .env.example                # Template de variables de entorno
├── demo/
│   └── index.html              # Widget demo (abrir en browser)
│
├── alembic/
│   ├── env.py                  # Config async de Alembic
│   └── versions/
│       └── 001_initial_schema.py
│
├── src/
│   └── app/
│       ├── main.py             # App factory + lifespan + middleware
│       ├── dependencies.py     # FastAPI Depends: DB session, tenant, plan
│       ├── exceptions.py       # DomainError + exception handlers
│       │
│       ├── core/
│       │   ├── config.py       # Settings desde .env (pydantic-settings)
│       │   ├── constants.py    # Enums: Plan, LeadStatus, SearchSource, etc.
│       │   ├── logging.py      # structlog setup
│       │   └── security.py     # hash_api_key, verify_api_key
│       │
│       ├── db/
│       │   ├── engine.py       # AsyncSessionLocal + WAL mode
│       │   ├── base.py         # DeclarativeBase
│       │   └── models/         # ORM: Tenant, Property, Session, Message, Lead, IngestionLog
│       │
│       ├── schemas/            # Pydantic v2: chat, property, lead, search, ingestion
│       │
│       ├── ingestion/          # CSV → SQLite + sqlite-vec
│       │   ├── parser.py       # CSV/Excel → PropertyCSVRow
│       │   ├── hasher.py       # SHA-256 checksum + property hash
│       │   ├── embedder.py     # sentence-transformers → sqlite-vec
│       │   └── pipeline.py     # Orquesta: parse → upsert → embed → log
│       │
│       ├── search/             # Hybrid search: regex → LLM fallback → SQL → vec
│       │   ├── filter_extractor.py  # Capa 1: regex + keywords (costo cero)
│       │   ├── filter_llm.py        # Capa 1b: LiteLLM fallback (solo si vacío)
│       │   ├── sql_search.py        # Capa 2: SQLAlchemy async
│       │   ├── vec_search.py        # Capa 3: sqlite-vec + post-filter
│       │   └── hybrid.py            # Orquestador de 4 capas
│       │
│       ├── chat/               # Motor conversacional
│       │   ├── engine.py       # process_message: end-to-end
│       │   ├── memory.py       # RAM session store + TTL + DB restore
│       │   └── language.py     # Detección ES/EN (heurística, sin LLM)
│       │
│       ├── llm/
│       │   ├── client.py       # LiteLLM wrapper + retry + fallback
│       │   ├── router.py       # Modelo por plan + fallback chain
│       │   └── prompts/        # system_es.py, system_en.py, booking.py
│       │
│       ├── qualification/      # Lead scoring rule-based
│       │   ├── signals.py      # Definición de señales y pesos
│       │   ├── extractor.py    # Historial → señales encontradas
│       │   └── scorer.py       # Score → QualificationResult → stage
│       │
│       ├── leads/
│       │   ├── service.py      # create_lead, update_lead_status
│       │   └── validator.py    # Validaciones: email, fecha futura, teléfono
│       │
│       ├── notifications/
│       │   ├── whatsapp.py     # Meta Cloud API (httpx async)
│       │   ├── email.py        # aiosmtplib + template HTML
│       │   └── dispatcher.py   # asyncio.gather: WhatsApp + Email paralelo
│       │
│       ├── calendar/
│       │   └── service.py      # Google Calendar API + asyncio.to_thread
│       │
│       └── api/
│           ├── middleware.py   # TenantMiddleware + RateLimitMiddleware
│           └── v1/
│               ├── chat.py         # WebSocket + POST fallback
│               ├── ingestion.py    # CSV upload
│               ├── properties.py   # Admin: listar propiedades
│               ├── leads.py        # Admin: gestionar leads
│               └── router.py       # Aggregador v1
│
└── tests/
    ├── conftest.py             # Fixtures: engine, session, tenant, client
    ├── unit/                   # Tests sin I/O (8 archivos)
    ├── integration/            # Tests con DB en memoria (6 archivos)
    └── e2e/                    # Tests HTTP/WebSocket completos (3 archivos)
```

---

## API Reference

Documentación interactiva (solo en `APP_ENV=development`):
- Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
- ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)

### Endpoints principales

| Método | Ruta | Descripción |
|--------|------|-------------|
| `WS` | `/api/v1/ws/chat/{session_id}` | Chat en tiempo real |
| `POST` | `/api/v1` | Chat POST fallback (sin WebSocket) |
| `POST` | `/api/v1/ingestion` | Subir CSV de propiedades |
| `GET` | `/api/v1/ingestion` | Listar ingestions del tenant |
| `GET` | `/api/v1/ingestion/{id}` | Detalle de ingestion |
| `GET` | `/api/v1/properties` | Listar propiedades (admin) |
| `GET` | `/api/v1/properties/search` | Búsqueda híbrida |
| `GET` | `/api/v1/properties/{id}` | Detalle de propiedad |
| `GET` | `/api/v1/leads` | Listar leads del tenant |
| `GET` | `/api/v1/leads/{id}` | Detalle de lead |
| `PATCH` | `/api/v1/leads/{id}/status` | Actualizar estado del lead |
| `POST` | `/api/v1/leads/{id}/notify` | Reenviar notificaciones |
| `GET` | `/api/v1/health` | Health check |
| `GET` | `/health` | Health check (raíz) |

### Flujo de conversación

```
Cliente → WebSocket /api/v1/ws/chat/{session_id}
  Envía: { "message": "busco apartamento en Pampatar" }
  Recibe: {
    "type": "response",
    "content": "Encontré 2 opciones...",
    "qualification_score": 35,
    "qualification_stage": "explore",
    "is_booking_active": false,
    "language": "es"
  }

# Heartbeat automático cada 30s:
  Server → { "type": "ping" }
  Client → { "type": "pong" }
```

---

## Multi-Tenant

En producción, cada cliente inmobiliario tiene su propio API key:

```bash
# Header requerido en todos los requests
X-API-Key: pk_live_xxxxxxxxxxxxxxxx
```

El middleware resuelve el tenant y aplica aislamiento completo:
- Solo ve sus propias propiedades y leads
- Su umbral de calificación es configurable
- Sus credenciales de WhatsApp/Calendar son propias

### Crear un tenant (desde la DB directamente en V1)

```python
# Script temporal hasta que el admin panel esté listo (V2)
from app.core.security import generate_api_key, hash_api_key
from app.db.models.tenant import Tenant

api_key = generate_api_key()
tenant = Tenant(
    id=str(uuid4()),
    name="Inmobiliaria XYZ",
    slug="inmobiliaria-xyz",
    plan="pro",
    api_key_hash=hash_api_key(api_key),
    agent_email="agente@xyz.com",
    agent_whatsapp="+584120000001",
    allowed_origins='["https://xyz.com"]',
    is_active=True,
    created_at=datetime.now(timezone.utc).isoformat(),
    updated_at=datetime.now(timezone.utc).isoformat(),
)
# Compartir api_key con el cliente (se muestra solo una vez)
print(f"API Key: {api_key}")
```

---

## Migraciones

```bash
# Crear nueva migración
uv run alembic revision --autogenerate -m "add_column_X"

# Aplicar migraciones pendientes
uv run alembic upgrade head

# Revertir última migración
uv run alembic downgrade -1

# Ver versión actual
uv run alembic current

# Ver historial
uv run alembic history
```

---

## Variables de Entorno

Ver `.env.example` para la lista completa documentada.

Variables mínimas para desarrollo:

```bash
APP_ENV=development
GROQ_API_KEY=gsk_...
SECRET_KEY=<openssl rand -hex 32>
DATABASE_URL=sqlite+aiosqlite:///./chatbot.db
```

Variables adicionales para producción:

```bash
APP_ENV=production
GEMINI_API_KEY=...          # Fallback LLM
SMTP_USER=tu@gmail.com
SMTP_PASSWORD=...           # App password Gmail
WHATSAPP_TOKEN=...          # Meta Business token
GOOGLE_CALENDAR_CREDENTIALS_PATH=./credentials.json
```

---

## Flujo de Búsqueda Híbrida

```
Query del usuario
  │
  ├── Capa 1: Regex + keywords (costo $0)
  │     → Extrae: zona, precio, habitaciones, flags booleanos
  │     → Si encuentra ≥1 filtro → ir a Capa 2
  │
  ├── Capa 1b: LLM fallback (solo si Capa 1 vacía)
  │     → LiteLLM Structured Output → FilterQuery
  │     → Circuit breaker: máx 3 llamadas/sesión/5min
  │
  ├── Capa 2: SQL (SQLAlchemy async)
  │     → Filtros exactos: precio, zona, habitaciones, flags
  │     → Si retorna resultados → FIN (verdad estructural)
  │
  └── Capa 3: sqlite-vec (solo si SQL vacío)
        → Embedding del query → KNN search
        → Post-filtering en Python (precio, flags)
        → Máx 3 propiedades en respuesta

REGLA: El LLM NUNCA inventa propiedades.
       Solo recibe la lista verificada y genera lenguaje natural.
```

---

## Lead Qualification

El bot califica automáticamente el nivel de compromiso del usuario:

| Stage | Score | Comportamiento |
|-------|-------|---------------|
| `explore` | 0–39 | Muestra propiedades, no presiona |
| `qualify` | 40–74 | Muestra + pregunta suave de presupuesto/zona |
| `book` | 75–100 | Activa flujo de agendamiento |

Señales detectadas (rule-based, sin LLM):

| Señal | Puntos |
|-------|--------|
| Presupuesto mencionado | +20 |
| Propiedad específica consultada | +20 |
| Zona especificada | +15 |
| Forma de pago preguntada | +15 |
| Urgencia temporal expresada | +15 |
| Tipo de propiedad claro | +10 |
| Señal comprador internacional | +10 |
| Conversación > 5 mensajes | +5 |

---

## Decisiones Arquitecturales Clave

**Rechazado en V1** (con documentación de por qué):

| Tecnología | Razón del rechazo |
|-----------|-------------------|
| LangChain / LlamaIndex | Abstracción innecesaria. RAG custom es superior para catálogos discretos. |
| ChromaDB | Conflicto de proceso con SQLite. sqlite-vec unifica el stack. |
| Ollama | Servidor externo. sentence-transformers es Python puro sin servidor. |
| Docker | Frena iteración en dev. Se añade en Fase 11 después de tests. |
| Redis | Sin evidencia de necesidad en V1. session_store en RAM suficiente. |
| LangGraph / CrewAI | Flujo conversacional es determinista. No requiere frameworks agenticos. |
| MCP (Model Context Protocol) | Overengineering. Flujo determinista no necesita descubrimiento dinámico. |

---

## Roadmap

### V1 (actual)
- ✅ Web widget embebible (WebSocket + POST fallback)
- ✅ Multi-tenant (aislamiento completo)
- ✅ Catálogo CSV → SQLite + sqlite-vec
- ✅ Búsqueda híbrida (regex → LLM fallback → SQL → vec)
- ✅ Lead Qualifier rule-based
- ✅ Google Calendar agendamiento
- ✅ WhatsApp + Email notificación al agente
- ✅ Bilingüe ES/EN
- ⏳ Docker (Fase 11)

### V2
- ⬜ Canal WhatsApp para usuarios finales
- ⬜ Lead Qualifier v2: LLM-as-Judge
- ⬜ Panel admin web
- ⬜ Conocimiento proceso inmobiliario venezolano (RAG)
- ⬜ Mistral como tercer LLM provider
- ⬜ Rate limiting por plan en settings

---

## Licencia

MIT — ver `LICENSE`

---

*Proyecto desarrollado por [@andervrz](https://github.com/andervrz)*
*Stack: FastAPI · SQLite · sqlite-vec · sentence-transformers · Groq · LiteLLM*
```
