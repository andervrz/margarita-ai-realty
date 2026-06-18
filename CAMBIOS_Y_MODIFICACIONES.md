# Margarita AI Realty — Cambios y Modificaciones para Replit

**Proyecto:** Margarita AI Realty  
**Descripción:** Chatbot conversacional con IA para inmobiliarias en Isla de Margarita, Venezuela  
**Stack:** FastAPI + Python 3.11 + SQLAlchemy (async) + PostgreSQL + structlog  
**Fecha de adaptación:** Junio 2026  

---

## Resumen ejecutivo

El proyecto fue importado desde GitHub y adaptado para correr en el entorno de Replit, que usa PostgreSQL (en vez de SQLite+sqlite-vec original), gestiona variables de entorno vía Secrets, y requiere instalación de dependencias vía pip (no uv).

---

## 1. Instalación de dependencias

### Paquetes instalados vía pip

El proyecto originalmente usaba `uv` como gestor de paquetes. En Replit se instalaron todas las dependencias con `pip`:

```bash
pip install fastapi uvicorn sqlalchemy aiosqlite sqlite-vec litellm \
            sentence-transformers asyncpg psycopg2-binary \
            passlib bcrypt structlog pydantic pydantic-settings \
            alembic python-multipart aiofiles
```

### Paquetes adicionales descubiertos durante la ejecución

| Paquete | Motivo |
|---------|--------|
| `passlib` | Requerido por `src/app/core/security.py` para hashing bcrypt |
| `bcrypt` | Dependencia de passlib para hashing de passwords |

### Instalación del paquete en modo editable

```bash
pip install -e . --no-deps
```

Necesario para que los imports `from src.app.*` funcionen correctamente en el entorno.

---

## 2. Variables de entorno configuradas

Configuradas vía Replit Secrets (no en archivo `.env`):

| Variable | Valor / Descripción |
|----------|---------------------|
| `APP_ENV` | `development` |
| `SECRET_KEY` | `c8d02dda3572c7536f5ccd5fd30d178a08f33788935d6198514821165aef53b2` |
| `APP_NAME` | `Margarita AI Realty` |
| `DATABASE_URL` | Provisto automáticamente por Replit (PostgreSQL) |

> **Nota:** `GROQ_API_KEY` y `GEMINI_API_KEY` no están configuradas. El servidor inicia correctamente pero las llamadas al LLM fallarán hasta que se agreguen.

---

## 3. Archivos nuevos creados

### `src/app/llm/system_en.py`

El archivo original se llamaba `systerm_en.py` (typo). Se creó una copia correcta:

```
src/app/llm/systerm_en.py  →  (existía con typo)
src/app/llm/system_en.py   →  (creado como copia correcta)
```

### Archivos `__init__.py` faltantes

Se crearon los siguientes archivos vacíos para completar la estructura de paquetes Python:

```
src/__init__.py
src/app/__init__.py
src/app/api/__init__.py
src/app/api/v1/__init__.py
src/app/chat/__init__.py
src/app/core/__init__.py
src/app/db/__init__.py
src/app/db/models/__init__.py
src/app/ingestion/__init__.py
src/app/llm/__init__.py
src/app/qualification/__init__.py
src/app/schemas/__init__.py
src/app/search/__init__.py
```

---

## 4. Archivos modificados

### `src/app/db/engine.py`

**Problema:** El motor original era solo para SQLite. Replit provee PostgreSQL.

**Cambio:** Auto-detección del driver según `DATABASE_URL`:
- Si la URL empieza con `postgresql://` → usa `asyncpg` como driver
- Si empieza con `sqlite://` → usa `aiosqlite` como driver
- Conversión automática: `postgresql://` → `postgresql+asyncpg://`
- Eliminación del parámetro `?sslmode=disable` que asyncpg no acepta

```python
# Antes (solo SQLite)
DATABASE_URL = "sqlite+aiosqlite:///./data/margarita.db"
engine = create_async_engine(DATABASE_URL, ...)

# Después (auto-detección)
_raw = os.environ.get("DATABASE_URL", settings.database_url)
if _raw.startswith("postgresql://"):
    _db_url = _raw.replace("postgresql://", "postgresql+asyncpg://")
    _db_url = _db_url.split("?sslmode=")[0]  # strip unsupported param
else:
    _db_url = _raw  # SQLite sin cambios
engine = create_async_engine(_db_url, ...)
```

---

### `alembic/env.py`

**Problema 1:** Los imports usaban `from app.*` (sin `src.`), pero los modelos del proyecto usan `from src.app.*`. Esto creaba dos módulos Python distintos, y los modelos no registraban sus tablas en el `Base` que Alembic usaba → autogenerate producía migraciones vacías.

**Problema 2:** El motor no soportaba PostgreSQL.

**Cambios:**
- Todos los imports cambiados a `from src.app.*` para coincidir con el resto del proyecto
- Importación explícita de todos los modelos para que registren sus tablas en `Base.metadata`
- Lógica de conversión de URL PostgreSQL → asyncpg (igual que `engine.py`)
- Soporte `render_as_batch=True` para SQLite, `False` para PostgreSQL

```python
# Cambio clave en imports
from src.app.db.base import Base          # antes: from app.db.base
from src.app.db.models.tenant import Tenant  # etc.

# Importar todos los modelos explícitamente
from src.app.db.models.ingestion_log import IngestionLog
from src.app.db.models.lead import Lead
from src.app.db.models.message import Message
from src.app.db.models.property import Property
from src.app.db.models.session import Session
from src.app.db.models.tenant import Tenant
```

---

### `src/app/core/constants.py`

**Problema:** Faltaba el enum `SearchSource` que `src/app/schemas/search.py` importaba.

**Cambio:** Se agregó el enum:

```python
class SearchSource(str, Enum):
    """Fuente del resultado de búsqueda."""
    SQL = "sql"
    VEC = "vec"
    HYBRID = "hybrid"
    FALLBACK = "fallback"
```

---

### `src/app/core/logging.py`

**Problema 1:** `structlog.processors.PositionalArgumentsFormatter()` fue eliminado en versiones recientes de structlog.

**Problema 2:** `structlog.stdlib.add_logger_name` requiere un logger de stdlib de Python, pero el proyecto usa `PrintLoggerFactory()` → `AttributeError: 'PrintLogger' object has no attribute 'name'`.

**Cambio:** Se removieron ambos processors incompatibles:

```python
# Antes
shared_processors = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,          # ← removido
    structlog.processors.PositionalArgumentsFormatter(),  # ← removido
    structlog.processors.StackInfoRenderer(),
    structlog.processors.TimeStamper(fmt="iso"),
]

# Después
shared_processors = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.processors.StackInfoRenderer(),
    structlog.processors.TimeStamper(fmt="iso"),
]
```

---

### `src/app/main.py`

**Problema:** El health check de inicio usaba una query de SQLite (`sqlite_master`) que no existe en PostgreSQL → `UndefinedTableError` en cada startup.

**Cambio:** Query reemplazada por `information_schema.tables` (compatible con ambos motores):

```python
# Antes (SQLite-only)
result = await conn.execute(
    text("SELECT name FROM sqlite_master WHERE type='table' AND name='tenants'")
)

# Después (PostgreSQL + SQLite compatible)
result = await conn.execute(
    text("SELECT table_name FROM information_schema.tables "
         "WHERE table_schema='public' AND table_name='tenants'")
)
```

---

### `src/app/api/v1/chat.py`

**Problema:** El router de chat se declaraba sin prefix (`APIRouter(tags=["chat"])`), pero tenía una ruta POST con path vacío `""`. FastAPI no permite incluir un router con prefix vacío y path vacío simultáneamente → `FastAPIError: Prefix and path cannot be both empty`.

**Cambio:**

```python
# Antes
router = APIRouter(tags=["chat"])

# Después
router = APIRouter(prefix="/chat", tags=["chat"])
```

El endpoint POST queda en `/api/v1/chat` (antes era inválido).

---

### `src/app/qualification/signals.py`

**Problemas múltiples:**
1. Error de indentación en la línea 37 (`   name:` con 3 espacios en vez de 4)
2. Función `get_stage_from_score` definida dos veces (la segunda referenciaba `THRESHOLDS` que no existía)
3. Los campos de `SignalConfig` (frozen dataclass) usaban `list` en vez de `tuple` (los dataclasses frozen no admiten listas mutables como default)

**Cambio:** Archivo reescrito con:
- Indentación corregida
- Función duplicada eliminada (se mantiene una sola versión con parámetros opcionales)
- Todos los defaults de `SignalConfig` convertidos a `tuple`
- Import innecesario de `field` y `Any` removido

---

### `src/app/chat/engine.py`

**Problema:** Imports incorrectos:

```python
# Antes (módulos inexistentes)
from src.app.llm.prompts import ...    # debe ser: llm.prompt
from src.app.qualification.scorer import ...  # debe ser: qualification.score
```

**Cambio:**

```python
from src.app.llm.prompt import ...
from src.app.qualification.score import ...
```

---

### `src/app/qualification/score.py`

**Problema:** Error de sintaxis — coma doble en una llamada de función (`,, `).

**Cambio:** Eliminada la coma duplicada.

---

## 5. Migración de base de datos

### Generación de la migración inicial

```bash
python3 -m alembic revision --autogenerate -m "initial_schema"
python3 -m alembic upgrade head
```

### Tablas creadas en PostgreSQL

| Tabla | Descripción |
|-------|-------------|
| `tenants` | Agencias inmobiliarias que usan el chatbot |
| `properties` | Propiedades inmuebles del catálogo |
| `sessions` | Sesiones de conversación activas |
| `messages` | Mensajes individuales por sesión |
| `leads` | Leads calificados generados por el chatbot |
| `ingestion_logs` | Registro de importaciones CSV de propiedades |
| `alembic_version` | Control de versión de migraciones |

### Índices creados

| Índice | Tabla | Columnas |
|--------|-------|----------|
| `idx_properties_external_id` | properties | (tenant_id, external_id) |
| `idx_properties_hash` | properties | (tenant_id, property_hash) |
| `idx_properties_tenant_status` | properties | (tenant_id, status) |
| `idx_properties_tenant_type` | properties | (tenant_id, property_type) |
| `idx_sessions_tenant_active` | sessions | (tenant_id, last_active_at) |
| `idx_leads_tenant_date` | leads | (tenant_id, created_at) |
| `idx_messages_session` | messages | (session_id,) |
| `idx_messages_tenant_date` | messages | (tenant_id, created_at) |

---

## 6. Workflow de Replit

### Configuración

```
Nombre: Start application
Comando: python3 -m uvicorn src.app.main:app --host 0.0.0.0 --port 5000 --reload
Puerto: 5000
Tipo: webview
```

---

## 7. Limitaciones conocidas

### SQLite-vec (búsqueda vectorial)

El archivo `src/app/search/vec_search.py` usa tablas virtuales de `sqlite-vec`, que son específicas de SQLite y no funcionan en PostgreSQL. El sistema de búsqueda híbrida detecta esto y cae back al modo SQL puro automáticamente — la búsqueda funciona, pero sin embeddings vectoriales.

Para activar búsqueda vectorial en producción se requeriría migrar a `pgvector` (extensión de PostgreSQL para vectores).

### LLM (Groq / Gemini)

Sin las API keys configuradas, el chatbot responde pero los modelos de lenguaje no están disponibles. Para activar:

1. Ir a **Secrets** en Replit
2. Agregar `GROQ_API_KEY` con la clave de [console.groq.com](https://console.groq.com)
3. Agregar `GEMINI_API_KEY` con la clave de [aistudio.google.com](https://aistudio.google.com)

### Modo desarrollo

En `APP_ENV=development`:
- La API key de tenant es omitida (se usa un tenant de desarrollo hardcodeado)
- CORS permite todos los orígenes (`*`)
- Documentación disponible en `/docs` y `/redoc`
- Rate limiting desactivado

---

## 8. Endpoints disponibles

| Método | Path | Descripción |
|--------|------|-------------|
| GET | `/` | Estado del servicio |
| GET | `/health` | Health check |
| GET | `/docs` | Swagger UI (solo development) |
| GET | `/redoc` | ReDoc (solo development) |
| POST | `/api/v1/chat` | Chat POST fallback |
| WS | `/api/v1/chat/ws/chat/{session_id}` | WebSocket conversacional |
| POST | `/api/v1/ingestion` | Upload CSV de propiedades |
| GET | `/api/v1/ingestion` | Listar ingestiones |
| GET | `/api/v1/ingestion/{id}` | Detalle de ingestión |
| GET | `/api/v1/properties` | Listar propiedades |
| GET | `/api/v1/properties/search` | Buscar propiedades |
| GET | `/api/v1/properties/{id}` | Detalle de propiedad |
| GET | `/api/v1/leads` | Listar leads |
| GET | `/api/v1/leads/{id}` | Detalle de lead |
| PATCH | `/api/v1/leads/{id}/status` | Actualizar estado de lead |
| POST | `/api/v1/leads/{id}/notify` | Notificar al agente |
| GET | `/api/v1/health` | Health check v1 |
