# PLAN.md — Real Estate Chatbot Pro
## Documento Maestro de Arquitectura, Scope y Metodología

> **Estado:** Scope cerrado. V1 = Plan Pro completo.
> **Última actualización:** Mayo 2026
> **Versión del documento:** 1.2.0
> **Mercado objetivo:** Isla de Margarita, Venezuela 🏝️

### Historial de Versiones

| Versión | Cambios |
|---------|---------|
| 1.0.0 | Documento inicial. Scope completo definido. |
| 1.1.0 | Correcciones Qwen3.6: WAL, observabilidad, rate limiting, idempotencia, TTL, contexto. |
| 1.2.0 | **Cambios mayores:** (1) ChromaDB eliminado → reemplazado por `sqlite-vec` (vector search nativo en SQLite, stack unificado). (2) Ollama eliminado → reemplazado por `sentence-transformers` (embeddings locales, sin servidor externo). (3) Correcciones adicionales: session cleanup background task, Fase 0.5 HTML demo widget, tenant default en dev, `visit_duration_minutes` en leads, multi-LLM simplificado a Groq + Gemini, verificación de model strings LiteLLM. Secciones colapsadas en v1.1.0 restauradas íntegramente. |

---

## Tabla de Contenidos

1. [Resumen Ejecutivo](#1-resumen-ejecutivo)
2. [Contexto de Mercado — Isla de Margarita](#2-contexto-de-mercado--isla-de-margarita)
3. [Decisiones de Scope — Registro Completo](#3-decisiones-de-scope--registro-completo)
4. [Stack Tecnológico Justificado](#4-stack-tecnológico-justificado)
5. [Arquitectura del Sistema](#5-arquitectura-del-sistema)
6. [Esquemas de Base de Datos](#6-esquemas-de-base-de-datos)
7. [Multi-Tenant Design](#7-multi-tenant-design)
8. [Multi-LLM Orchestration](#8-multi-llm-orchestration)
9. [Planes de Pago](#9-planes-de-pago)
10. [Lead Qualification Engine](#10-lead-qualification-engine)
11. [Flujo Conversacional](#11-flujo-conversacional)
12. [Hybrid Search Engine](#12-hybrid-search-engine)
13. [Sistema de Memoria y Contexto](#13-sistema-de-memoria-y-contexto)
14. [Notificaciones](#14-notificaciones)
15. [Google Calendar Integration](#15-google-calendar-integration)
16. [Observabilidad](#16-observabilidad)
17. [Prerequisitos de Desarrollo](#17-prerequisitos-de-desarrollo)
18. [Estructura de Carpetas](#18-estructura-de-carpetas)
19. [Metodología de Desarrollo](#19-metodología-de-desarrollo)
20. [Orden de Construcción — Build Phases](#20-orden-de-construcción--build-phases)
21. [Convenciones de Código](#21-convenciones-de-código)
22. [Variables de Entorno](#22-variables-de-entorno)
23. [Testing Strategy](#23-testing-strategy)
24. [Docker Strategy](#24-docker-strategy)
25. [Roadmap de Versiones](#25-roadmap-de-versiones)
26. [Principios Arquitecturales](#26-principios-arquitecturales)
27. [Decisiones Pendientes para V2+](#27-decisiones-pendientes-para-v2)

---

## 1. Resumen Ejecutivo

### ¿Qué es este proyecto?

Un chatbot conversacional embebible en sitios web inmobiliarios que permite a usuarios
interesados explorar un catálogo de propiedades en lenguaje natural, recibir
recomendaciones filtradas, calificar su intención de compra/arriendo, y agendar visitas
automáticamente — notificando al agente por WhatsApp y email.

### Propuesta de Valor Técnica

- **El LLM nunca inventa propiedades** — solo razona sobre lo que SQLite confirma
- **Stack unificado de una sola DB** — SQLite + sqlite-vec: búsqueda relacional y
  vectorial en el mismo archivo, sin procesos externos en conflicto
- **Embeddings locales sin servidor** — sentence-transformers: pura librería Python,
  sin Ollama, sin dependencias de infraestructura
- **Multi-tenant nativo** — un backend sirve múltiples clientes inmobiliarios
- **Lead qualification automático** — el bot sabe cuándo el usuario está listo para agendar
- **AsyncIO 100%** — arquitectura no bloqueante de extremo a extremo

### Oportunidad de Mercado

En Margarita, todas las agencias operan con WhatsApp + Instagram + sitio web estático.
**Ninguna tiene chatbot, búsqueda semántica ni calificación automática de leads.**
La ventana tecnológica está completamente abierta.

### Target de Negocio

- **Usuarios finales:** Personas buscando propiedades (compra/arriendo/inversión)
- **Clientes directos:** Agencias y agentes inmobiliarios en Isla de Margarita
- **Canal v1:** Web widget embebible (JS snippet / iframe)
- **Canal v2:** WhatsApp para usuarios finales
- **Mercado inicial:** Isla de Margarita — bilingüe ES/EN
- **Monetización:** Tres planes (Básico / Estándar / Pro)

---

## 2. Contexto de Mercado — Isla de Margarita

### Estado del Mercado (Mayo 2026)

El mercado inmobiliario de Margarita está en expansión acelerada. Los precios subieron
entre 20% y 100% entre septiembre 2025 y enero 2026. El metro cuadrado pasó de $1,064
a $1,282 USD. Las unidades más económicas parten desde $150,000 USD.

La demanda supera la oferta. Los agentes están desbordados de consultas.
**Calificar leads eficientemente es el problema número uno del sector.**

### Perfil del Comprador

| Tipo | Idioma | Canal | Motivación |
|------|--------|-------|------------|
| Local venezolano | ES | WhatsApp | Vivienda / segunda residencia |
| Venezolano en exterior | ES/EN | Web | Inversión / retorno |
| Inversionista latinoamericano | ES | Web | ROI, turismo, dolarización |
| Inversionista europeo | EN/FR | Web | Precio/m2 atractivo, Caribe |
| Comprador EEUU | EN | Web | Inversión turística |

### Zonas del Mercado (Keywords para Filter Extractor)

```python
MARGARITA_ZONES = {
    "premium":          ["pampatar", "paraíso", "paraiso", "casa de campo",
                         "country club", "puerto real", "santa ana del norte"],
    "beach":            ["playa el agua", "el agua", "guacuco", "playa caribe",
                         "playa parguito", "manzanillo"],
    "sports":           ["el yaque", "yaque"],
    "exclusive_rural":  ["sabana de guacuco", "rancho de chana", "cerro guayamuri",
                         "las hernández", "chana"],
    "commercial":       ["porlamar", "av bolívar", "av 4 de mayo",
                         "la asunción", "juan griego"],
    "general":          ["margarita", "nueva esparta", "isla", "perla del caribe"],
}
```

### Tipos de Propiedad Activos en Margarita

- Apartamentos residenciales (venta y arriendo)
- Casas y villas (con/sin vista al mar)
- Propiedades vacacionales (alquiler por temporada)
- Posadas (hospedaje turístico pequeño)
- Hoteles (inversión turística)
- Locales comerciales
- Terrenos para desarrollo
- Proyectos en planos

### Brecha Tecnológica Actual

```
Lo que tienen las inmobiliarias de Margarita hoy:
  ✅ Sitio web con catálogo estático
  ✅ Instagram con fotos
  ✅ Botón de WhatsApp al agente (canal principal)

Lo que NO tiene ninguna:
  ❌ Chatbot conversacional
  ❌ Búsqueda semántica / lenguaje natural
  ❌ Calificación automática de leads
  ❌ Agendamiento automático de visitas
  ❌ Notificación automática al agente
  ❌ Memoria de conversación entre sesiones
```

### Implicaciones para el Sistema

1. **WhatsApp al agente es prioridad 1** — los agentes viven en WhatsApp
2. **`vista_al_mar`** es el factor de precio más crítico — campo obligatorio en schema
3. **`uso_vacacional`** diferencia inversión turística de residencia permanente
4. **Compradores internacionales** generan señales detectables en el qualifier
5. **Precio siempre en USD** — el Bs es referencial, tasa del día por tenant

---

## 3. Decisiones de Scope — Registro Completo

| # | Dimensión | Decisión | Justificación |
|---|-----------|----------|---------------|
| 1 | Canal principal v1 | Web widget embebible | JS snippet o iframe puro. Sin Gradio ni Streamlit |
| 2 | Canal secundario | WhatsApp usuarios finales (v2) | Meta Cloud API + FastAPI webhook |
| 3 | Modelo de tenant | Multi-tenant desde v1 | Construir el techo. Desactivar por plan es config, no código |
| 4 | Tipos de propiedad | Venta, Arriendo, Vacacional, Locales, Hoteles/Posadas, Planos, Terrenos | Mercado completo de Margarita |
| 5 | Fuente de datos | CSV/Excel del cliente | Ingestion pipeline valida y carga |
| 6 | Idioma | Bilingüe ES/EN desde v1 | Mercado venezolano + compradores internacionales |
| 7 | Flujo conversacional | Híbrido | Bot sugiere, usuario escribe libre |
| 8 | Tipo de búsqueda | Híbrida: regex+keywords → LLM fallback → sqlite-vec | Regex cubre 80% sin costo. LLM fallback para ambigüedad. sqlite-vec sin proceso externo |
| 9 | Memoria | Sesión activa (RAM + TTL/LRU + background cleanup) + historial persistente (DB) | TTL + cleanup task garantiza que RAM no crece indefinidamente |
| 10 | Lead capture | Al momento de agendar visita | Nombre, email, teléfono, fecha, hora, duración, notas |
| 11 | Agendamiento | Google Calendar API | Oficial, confiable. Duración de visita configurable por tenant |
| 12 | Notificación agente | WhatsApp (prioridad 1) + Email SMTP (prioridad 2) | Agentes de Margarita viven en WhatsApp |
| 13 | MCP para Calendar/Email | ❌ Descartado | Overengineering. Flujo determinista. No necesita descubrimiento dinámico de tools |
| 14 | Lead Qualifier v1 | Rule-based scoring | Simple, predecible, auditable, cero costo LLM |
| 15 | Multi-LLM | LiteLLM: Groq (primary) → Gemini (fallback) | Dos providers son suficientes para V1. Mistral en V2 |
| 16 | Planes de pago | Básico / Estándar / Pro | Pro es la base de V1 |
| 17 | V1 target | Plan Pro completo | Se construye el techo. Los pisos se configuran después |
| 18 | AsyncIO | 100% sin excepciones | FastAPI async-first. Todo I/O no bloqueante |
| 19 | ORM | SQLAlchemy 2.0 async + aiosqlite + Alembic | Driver async nativo. Migraciones versionadas incluso para solo dev |
| 20 | Contenido catálogo | Solo propiedades v1 | Proceso inmobiliario venezolano → V2 con experto legal |
| 21 | Docker | Fase 11 — al final | No cambia código. Frena iteración en dev |
| 22 | Package manager | uv | Consistencia con proyectos del autor |
| 23 | DB producción | SQLite (dev) → PostgreSQL + pgvector (prod) | Mismo ORM. Cambio de DATABASE_URL |
| 24 | SQLite modo WAL | ✅ Habilitado desde Fase 0 | Lecturas concurrentes sin bloquear escrituras |
| 25 | Vector store | **sqlite-vec** en lugar de ChromaDB | Elimina el conflicto de dos instancias SQLite en el mismo proceso. Stack unificado: una sola DB para relacional + vectorial. Aislamiento por tenant via tablas separadas. Sin proceso externo |
| 26 | Embeddings | **sentence-transformers** en lugar de Ollama | Librería Python pura, sin servidor externo. Modelo `paraphrase-multilingual-MiniLM-L12-v2` (384 dims, ES/EN nativo). Primera ejecución descarga ~120MB, luego cache local |
| 27 | Filter extractor | Híbrido: regex+keywords → LLM fallback | Regex cubre 80% sin costo. LLM solo cuando filtros están vacíos |
| 28 | Sesiones RAM | TTL/LRU + background cleanup task (asyncio) | Sin cleanup la RAM crece indefinidamente. Task periódica limpia sesiones expiradas |
| 29 | Observabilidad | structlog desde Fase 0 | Estándar async moderno. OpenTelemetry → V2 |
| 30 | WebSocket resiliencia | Heartbeat ping/pong cada 30s | Firewalls cortan conexiones idle |
| 31 | Rate limiting | slowapi por tenant/IP | Previene abuso de LLM y costos descontrolados |
| 32 | Ingestión idempotente | Checksum de archivo + hash por propiedad | Evita duplicados en re-subidas de CSV |
| 33 | Gestión de contexto | Truncado de payloads pesados en historial | Evita desborde de tokens y controla costos |
| 34 | CORS | CORSMiddleware con `allowed_origins` por tenant | Widget vive en dominio del cliente. Sin CORS el browser bloquea todo |
| 35 | Widget V1 | HTML demo page (Fase 0.5) | Sin widget el producto no se puede demostrar. Demo mínima demuestra el producto funcionando |
| 36 | Tenant dev | Tenant default hardcodeado en desarrollo | Elimina fricción del middleware en Fases 0-8. Multi-tenant completo se activa en Fase 9 |
| 37 | Duración de visita | `visit_duration_minutes` en leads (default: 60 min) | Google Calendar requiere start ≠ end. Visitas inmobiliarias duran 45-90 min |
| 38 | Model strings LiteLLM | Verificar en LiteLLM registry antes de implementar router | `gemini-2.0-pro` puede no ser el string exacto. Validar contra docs de LiteLLM |
| 39 | Campos Margarita | `vista_al_mar`, `uso_vacacional`, `frente_playa`, `tipo_especial`, `capacidad_huespedes` | Factores de precio críticos en el mercado de Margarita |
| 40 | Señal qualifier internacional | `international_buyer_signal` (+10 pts) | Compradores del exterior generan señales detectables |
| 41 | LangChain / LlamaIndex | ❌ Descartados | Abstracción innecesaria. RAG custom es superior para propiedades discretas |
| 42 | SQLite Long Context | ❌ Descartado | 500 props × 200 tokens = 100k tokens/request. Inviable |
| 43 | Frameworks agenticos | ❌ Fuera de scope | LangGraph, CrewAI, MCP, A2A → decisión futura cuando sea requerida |

---

## 4. Stack Tecnológico Justificado

### Backend Core

| Librería | Rol | Justificación |
|----------|-----|---------------|
| `fastapi` | Framework HTTP + WebSocket | Async-first nativo, tipado, docs automáticas |
| `uvicorn` | ASGI server | Standard FastAPI en producción |
| `pydantic` v2 | Validación de datos | 10x más rápido que v1. Estricto en todo el borde |
| `pydantic-settings` | Config desde .env | Settings tipados que integran con Pydantic v2 |
| `structlog` | Logging estructurado | Estándar para apps async modernas. JSON en prod |
| `slowapi` | Rate limiting | Control de abuso por tenant/IP |

### Base de Datos — Stack Unificado

| Librería | Rol | Justificación |
|----------|-----|---------------|
| `sqlalchemy[asyncio]` | ORM async | v2.0 con `create_async_engine` + `AsyncSession` |
| `aiosqlite` | Driver async SQLite | `await` en todas las operaciones SQLite |
| `sqlite-vec` | Vector search en SQLite | **Elimina ChromaDB.** Extensión nativa de SQLite para búsqueda vectorial. Una sola DB para relacional + vectorial. Sin conflicto de dos instancias SQLite |
| `alembic` | Migraciones | Schema versionado. Valioso incluso para solo dev cuando hay cambios de schema frecuentes |
| `asyncpg` | Driver async PostgreSQL | Para producción con PostgreSQL + pgvector |

### LLM & AI

| Librería | Rol | Justificación |
|----------|-----|---------------|
| `litellm` | Multi-LLM gateway | Una sola API para Groq + Gemini con formato OpenAI. Fallback automático |
| `sentence-transformers` | Embeddings locales | **Elimina Ollama.** Librería Python pura, sin servidor. Modelo `paraphrase-multilingual-MiniLM-L12-v2`: 384 dims, ES/EN nativo, ~120MB descarga única |
| `pandas` | CSV/Excel parsing | Lectura robusta de catálogos del cliente |

### Comunicaciones Async

| Librería | Rol | Justificación |
|----------|-----|---------------|
| `aiosmtplib` | Email async | SMTP nativo para asyncio. No bloquea event loop |
| `httpx` | HTTP async client | WhatsApp Cloud API y HTTP externos |
| `google-api-python-client` | Google Calendar | SDK oficial. Envuelto en `asyncio.to_thread()` |

### Testing

| Librería | Rol |
|----------|-----|
| `pytest` | Framework base |
| `pytest-asyncio` | Soporte async en tests |
| `httpx` | `AsyncClient` para tests de FastAPI |

### Modelos LLM

| Modelo | Provider | Rol |
|--------|----------|-----|
| `llama-3.3-70b-versatile` | Groq | Default — sub-segundo de latencia, costo mínimo |
| `gemini-2.5-pro` *(verificar string en LiteLLM registry)* | Google | Fallback V1 |
| `paraphrase-multilingual-MiniLM-L12-v2` | Local (sentence-transformers) | Embeddings ES/EN — sin costo de API, sin servidor |

### Stack Rechazado (con justificación)

| Librería | Decisión | Razón |
|----------|----------|-------|
| ChromaDB | ❌ Eliminado | Conflicto de dos instancias SQLite en el mismo proceso. sqlite-vec es superior para este caso |
| Ollama | ❌ Eliminado | Dependencia de infraestructura (servidor externo). sentence-transformers es pura Python |
| LangChain / LlamaIndex | ❌ Eliminados | Abstracción innecesaria sobre código que ya controlamos |
| OpenTelemetry | ❌ Diferido a V2 | Overhead para single-worker. structlog es suficiente |
| Mistral | ❌ Diferido a V2 | Groq + Gemini cubren V1. Complejidad de 3 providers prematura |
| MCP | ❌ Descartado V1 | Flujo determinista. No necesita descubrimiento dinámico |

---

## 5. Arquitectura del Sistema

```
┌──────────────────────────────────────────────────────────────────────┐
│                         CLIENTE (Browser)                             │
│          Web Widget — JS snippet embebido en sitio del agente         │
│       WebSocket /ws/chat ←→ REST /api/v1/* para operaciones admin    │
└─────────────────────────────┬────────────────────────────────────────┘
                              │  X-API-Key header (tenant auth)
                              │  Origin header (CORS validation)
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        FastAPI Application                            │
│                                                                       │
│  Middleware Stack (orden de ejecución):                               │
│  1. CORSMiddleware      → allow_origins por tenant                   │
│  2. TenantMiddleware    → X-API-Key hash → tenant_id + config        │
│  3. RateLimitMiddleware → slowapi por tenant/IP                       │
│                                                                       │
│  ┌──────────────────┐  ┌─────────────────────┐  ┌────────────────┐  │
│  │  POST /api/v1/   │  │  WebSocket          │  │ Admin Endpoints│  │
│  │       chat       │  │  /ws/chat/{sid}     │  │ /ingest        │  │
│  │                  │  │  + heartbeat 30s    │  │ /properties    │  │
│  │                  │  │    ping/pong        │  │ /leads         │  │
│  └────────┬─────────┘  └──────────┬──────────┘  └────────────────┘  │
│           └──────────────────────┘                                    │
│                        │                                              │
│                        ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                       Chat Engine                               │ │
│  │                                                                 │ │
│  │  1. Language Detection (ES/EN)                                 │ │
│  │  2. Memory Load (RAM session + TTL check + cleanup task)       │ │
│  │  3. Filter Extraction (regex → LLM fallback si vacío)         │ │
│  │  4. Hybrid Search (SQL + sqlite-vec con metadata filters)      │ │
│  │  5. Context Build (truncado inteligente de historial)          │ │
│  │  6. LiteLLM Call (Groq → Gemini fallback)                     │ │
│  │  7. Lead Qualifier (rule-based score)                          │ │
│  │  8. Response Assembly (explore / qualify / book)               │ │
│  │  9. Memory Save (RAM + DB async)                               │ │
│  └──────┬────────────┬────────────┬────────────┬──────────────────┘ │
│         │            │            │            │                      │
│         ▼            ▼            ▼            ▼                      │
│  ┌──────────┐  ┌──────────┐  ┌───────┐  ┌────────────────────────┐  │
│  │ LiteLLM  │  │  Hybrid  │  │Memory │  │   Lead Qualifier        │  │
│  │ Router   │  │  Search  │  │  RAM  │  │   Rule-based Scorer     │  │
│  │ Groq /   │  │  SQL +   │  │  TTL/ │  │   score >= 75 → book   │  │
│  │ Gemini   │  │sqlite-vec│  │  LRU  │  │   score 40-74 → qualify│  │
│  └──────────┘  └────┬─────┘  └───────┘  └────────────────────────┘  │
│                     │                                                 │
└─────────────────────┼───────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  Data Layer — Stack Unificado                         │
│                                                                       │
│  SQLite (WAL mode)                                                   │
│  ├── Tablas relacionales: tenants, properties, sessions,             │
│  │                        messages, leads, ingestion_logs            │
│  └── Tablas vectoriales (sqlite-vec):                                │
│       property_embeddings_{tenant_id}  ← por tenant, aisladas       │
│                                                                       │
│  sentence-transformers (local, sin servidor)                         │
│  └── paraphrase-multilingual-MiniLM-L12-v2 (384 dims, ES/EN)       │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│                       External Services                               │
│                                                                       │
│  Google Calendar API  → asyncio.to_thread()                         │
│  aiosmtplib           → Email SMTP                                   │
│  httpx                → Meta WhatsApp Cloud API (prioridad 1)        │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

### Flujo Completo de un Mensaje

```
1.  Browser → WebSocket con session_id
2.  TenantMiddleware → resuelve tenant desde X-API-Key (SHA-256 en DB)
    [En desarrollo: tenant default hardcodeado, sin middleware]
3.  RateLimiter → verifica límite por tenant/IP
4.  Language Detection → ES o EN
5.  Memory Load → busca session en RAM (si existe y no expiró TTL)
               → si expiró o no existe: carga N mensajes desde SQLite
6.  Filter Extraction:
      PASO 1: regex + keywords → FilterQuery (costo cero)
      PASO 2: si FilterQuery vacío → LiteLLM Structured Output → Pydantic v2
7.  Hybrid Search:
      SQL query con filtros duros (tenant_id, status, precio, zona, habitaciones)
      Si SQL retorna resultados → usar (verdad estructural)
      Si SQL vacío → sqlite-vec similarity search + metadata filtering
      Merge & rank → máx 3 propiedades
8.  Context Build → trunca historial, referencias compactas en turnos anteriores
9.  LiteLLM Call → system prompt + context + propiedades verificadas
10. Lead Qualifier → escanea historial → calcula score
11. Si score >= 75 → activa booking flow step-by-step
    Si score 40-74 → añade pregunta de calificación al final
    Si score < 40  → responde con propiedades, invita a explorar
12. Guarda mensajes user + assistant en SQLite (async)
13. Actualiza session RAM (score, timestamp, booking_step)
14. Retorna respuesta via WebSocket
```

---

## 6. Esquemas de Base de Datos

### `tenants`
```sql
CREATE TABLE tenants (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,          -- "Esparta Inmuebles"
    slug                    TEXT UNIQUE NOT NULL,   -- "esparta-inmuebles"
    plan                    TEXT NOT NULL DEFAULT 'pro',
    api_key_hash            TEXT UNIQUE NOT NULL,   -- SHA-256 del API key
    llm_model               TEXT,                   -- override del modelo
    llm_fallback_1          TEXT,
    qualification_threshold INTEGER DEFAULT 75,
    session_ttl_minutes     INTEGER DEFAULT 30,
    visit_duration_minutes  INTEGER DEFAULT 60,     -- duración default de visita
    calendar_enabled        INTEGER DEFAULT 1,
    email_enabled           INTEGER DEFAULT 1,
    whatsapp_enabled        INTEGER DEFAULT 1,
    agent_email             TEXT,
    agent_whatsapp          TEXT,
    whatsapp_phone_id       TEXT,
    allowed_origins         TEXT,                   -- JSON array de dominios permitidos
    is_active               INTEGER DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
```

### `properties`
```sql
CREATE TABLE properties (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL,
    external_id         TEXT,                       -- ID en CSV del cliente (idempotencia)
    property_hash       TEXT,                       -- SHA-256 del contenido (upsert)
    title               TEXT NOT NULL,
    property_type       TEXT NOT NULL,              -- venta|arriendo|vacacional|local|posada|hotel|planos|terreno
    status              TEXT NOT NULL DEFAULT 'disponible',
    price_usd           REAL,
    price_bs            REAL,
    location_city       TEXT DEFAULT 'Porlamar',
    location_zone       TEXT,                       -- "Pampatar", "El Yaque"
    location_address    TEXT,
    area_m2             REAL,
    bedrooms            INTEGER,
    bathrooms           INTEGER,
    parking_spots       INTEGER,
    -- Campos específicos mercado Margarita
    vista_al_mar        INTEGER DEFAULT 0,          -- BOOLEAN (factor de precio crítico)
    frente_playa        INTEGER DEFAULT 0,          -- BOOLEAN (premium máximo)
    uso_vacacional      INTEGER DEFAULT 0,          -- BOOLEAN (inversión turística vs residencia)
    tipo_especial       TEXT,                       -- posada|hotel|villa|galpon|finca
    capacidad_huespedes INTEGER,                    -- para posadas, villas y hoteles
    -- Contenido
    amenities           TEXT,                       -- JSON array: ["piscina","gym"]
    photos              TEXT,                       -- JSON array de URLs
    description_es      TEXT,
    description_en      TEXT,
    raw_embed_text      TEXT,                       -- texto para embedding (generado en ingestion)
    -- Metadata
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);
```

### `property_embeddings_{tenant_id}` (sqlite-vec)
```sql
-- Una tabla virtual por tenant — aislamiento estricto
-- Se crea dinámicamente en ingestion/pipeline.py al procesar el primer CSV del tenant

CREATE VIRTUAL TABLE property_embeddings_{tenant_id}
USING vec0(
    property_id TEXT PRIMARY KEY,
    embedding   FLOAT[384]          -- dimensiones de paraphrase-multilingual-MiniLM-L12-v2
);
```

### `sessions`
```sql
CREATE TABLE sessions (
    id                  TEXT PRIMARY KEY,           -- UUID (cookie del usuario)
    tenant_id           TEXT NOT NULL,
    language            TEXT DEFAULT 'es',
    qualification_score INTEGER DEFAULT 0,
    is_booking_active   INTEGER DEFAULT 0,
    booking_step        TEXT,                       -- nombre|email|phone|date|time|duration|notes|confirm
    created_at          TEXT NOT NULL,
    last_active_at      TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);
```

### `messages`
```sql
CREATE TABLE messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    tenant_id   TEXT NOT NULL,                      -- denormalizado para queries eficientes
    role        TEXT NOT NULL,                      -- user|assistant
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
```

### `leads`
```sql
CREATE TABLE leads (
    id                      TEXT PRIMARY KEY,
    session_id              TEXT NOT NULL,
    tenant_id               TEXT NOT NULL,
    property_id             TEXT,                   -- nullable
    name                    TEXT NOT NULL,
    email                   TEXT NOT NULL,
    phone                   TEXT NOT NULL,
    preferred_date          TEXT NOT NULL,          -- ISO date "2026-06-15"
    preferred_time          TEXT NOT NULL,          -- "10:00"
    visit_duration_minutes  INTEGER DEFAULT 60,     -- duración de la visita
    notes                   TEXT,
    qualification_score     INTEGER,
    is_international        INTEGER DEFAULT 0,      -- BOOLEAN
    status                  TEXT DEFAULT 'pendiente',
    calendar_event_id       TEXT,
    whatsapp_sent           INTEGER DEFAULT 0,
    email_sent              INTEGER DEFAULT 0,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (property_id) REFERENCES properties(id)
);
```

### `ingestion_logs`
```sql
CREATE TABLE ingestion_logs (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    filename        TEXT NOT NULL,
    file_checksum   TEXT NOT NULL,
    total_rows      INTEGER,
    valid_rows      INTEGER,
    inserted_rows   INTEGER,
    updated_rows    INTEGER,
    skipped_rows    INTEGER,
    failed_rows     INTEGER,
    errors          TEXT,                           -- JSON array de errores
    status          TEXT,                           -- success|partial|failed
    created_at      TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);
```

### Índices Críticos
```sql
CREATE INDEX idx_properties_tenant_status  ON properties(tenant_id, status);
CREATE INDEX idx_properties_tenant_type    ON properties(tenant_id, property_type);
CREATE INDEX idx_properties_external_id    ON properties(tenant_id, external_id);
CREATE INDEX idx_properties_hash           ON properties(tenant_id, property_hash);
CREATE INDEX idx_messages_session          ON messages(session_id);
CREATE INDEX idx_messages_tenant_date      ON messages(tenant_id, created_at);
CREATE INDEX idx_leads_tenant_date         ON leads(tenant_id, created_at);
CREATE INDEX idx_sessions_tenant_active    ON sessions(tenant_id, last_active_at);
```

### Configuración WAL + sqlite-vec (Fase 0)
```python
# app/db/engine.py
import sqlite_vec
from sqlalchemy import event

@event.listens_for(engine.sync_engine, "connect")
def configure_sqlite(dbapi_connection, connection_record):
    # WAL mode: lecturas concurrentes sin bloquear escrituras
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
    # Cargar extensión sqlite-vec para búsqueda vectorial
    dbapi_connection.enable_load_extension(True)
    sqlite_vec.load(dbapi_connection)
    dbapi_connection.enable_load_extension(False)
```

---

## 7. Multi-Tenant Design

### Principio de Aislamiento

Cada tenant (cliente inmobiliario) está completamente aislado:
- `tenant_id` en cada query — propiedades, conversaciones y leads son privados
- `property_embeddings_{tenant_id}` — tabla vectorial separada por tenant
- Modelo LLM, threshold de calificación y TTL configurables por tenant
- Credenciales de WhatsApp/Calendar son propias de cada tenant

### Tenant Default en Desarrollo

```python
# app/dependencies.py — en APP_ENV=development

# Durante Fases 0-8, el tenant se hardcodea para eliminar fricción del middleware
# y permite ver el chatbot funcionar antes de implementar multi-tenant completo

DEV_TENANT = Tenant(
    id="dev-tenant-001",
    name="Dev Inmobiliaria Margarita",
    plan="pro",
    api_key_hash="dev",
    qualification_threshold=75,
    session_ttl_minutes=30,
    visit_duration_minutes=60,
    calendar_enabled=True,
    email_enabled=True,
    whatsapp_enabled=True,
)

async def get_current_tenant(request: Request) -> Tenant:
    if settings.APP_ENV == "development":
        return DEV_TENANT
    # En producción: TenantMiddleware resuelve desde X-API-Key
    return request.state.tenant
```

### Autenticación del Widget (Producción)

```
1. Cliente embebe el widget:
   <script src="https://api.chatbot.com/widget.js"
           data-api-key="pk_live_xxxxxx">
   </script>

2. Widget envía en cada request:
   Header: X-API-Key: pk_live_xxxxxx
   Header: Origin: https://espartainmuebles.com

3. TenantMiddleware:
   a. SHA-256 del API Key → busca en tenants WHERE api_key_hash = ?
   b. Verifica Origin en allowed_origins del tenant
   c. Si inactivo → 403 Forbidden
   d. Inyecta tenant en request.state
```

### Isolation en sqlite-vec

```python
# Cada tenant tiene su propia tabla vectorial — creada en primera ingestión

def get_vector_table_name(tenant_id: str) -> str:
    return f"property_embeddings_{tenant_id.replace('-', '_')}"

# Query vectorial siempre filtra por tabla del tenant — sin cross-tenant leaks
```

---

## 8. Multi-LLM Orchestration

### LiteLLM como Gateway Unificado (V1: Groq + Gemini)

```python
# app/llm/client.py
import litellm

async def chat_completion(
    messages: list[dict],
    model: str,
    timeout: int = settings.LLM_TIMEOUT,
    **kwargs,
) -> str:
    try:
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=0.7,
            max_tokens=1000,
            timeout=timeout,
            **kwargs,
        )
        return response.choices[0].message.content
    except litellm.RateLimitError:
        raise LLMRateLimitError(model=model)
    except litellm.Timeout:
        raise LLMTimeoutError(model=model, timeout=timeout)
    except Exception as e:
        raise LLMError(model=model, detail=str(e)) from e
```

### Model Router

```python
# app/llm/router.py
# ⚠️ ACCIÓN REQUERIDA ANTES DE IMPLEMENTAR:
# Verificar strings exactos en: https://docs.litellm.ai/docs/providers
# El string "gemini/gemini-2.5-pro" debe confirmarse en LiteLLM registry

PLAN_MODELS = {
    "pro": {
        "primary":    "groq/llama-3.3-70b-versatile",
        "fallback_1": "gemini/gemini-2.5-pro",  # verificar string exacto
    },
    "standard": {
        "primary":    "groq/llama-3.3-70b-versatile",
        "fallback_1": "gemini/gemini-2.0-flash",  # verificar string exacto
    },
    "basic": {
        "primary":    "groq/llama-3.3-70b-versatile",
        "fallback_1": None,
    },
}
```

### Roles de Modelos

| Rol | Modelo | Justificación |
|-----|--------|---------------|
| Chat conversacional | Groq LLaMA 3.3-70b | Sub-segundo de latencia. UX fluida |
| Filter extraction fallback | Groq LLaMA 3.3-70b | Mismo modelo. Solo si regex falla |
| Fallback V1 | Gemini 2.5 Pro | Mayor razonamiento cuando Groq falla |
| Embeddings | paraphrase-multilingual-MiniLM-L12-v2 | Local. Sin costo de API. Sin servidor. ES/EN nativo |

---

## 9. Planes de Pago

### V1: Todo el Sistema es Plan Pro

El código no tiene feature flags en V1. Pro es la base completa. Los planes inferiores
se configuran post-V1 en la tabla `tenants` desactivando flags booleanos.

```
Plan Pro      → calendar_enabled=1, email_enabled=1, whatsapp_enabled=1
Plan Standard → calendar_enabled=1, email_enabled=1, whatsapp_enabled=0
Plan Basic    → calendar_enabled=0, email_enabled=0, whatsapp_enabled=0
```

| Capacidad | Básico | Estándar | Pro |
|-----------|:------:|:--------:|:---:|
| Chatbot catálogo | ✅ | ✅ | ✅ |
| Búsqueda híbrida SQL + sqlite-vec | ✅ | ✅ | ✅ |
| Bilingüe ES/EN | ✅ | ✅ | ✅ |
| Memoria sesión + persistente | ✅ | ✅ | ✅ |
| Lead capture | ✅ | ✅ | ✅ |
| Lead Qualifier rule-based | ✅ | ✅ | ✅ |
| Google Calendar agendamiento | ❌ | ✅ | ✅ |
| Email al agente | ❌ | ✅ | ✅ |
| WhatsApp al agente | ❌ | ❌ | ✅ |
| LLM principal | Groq | Groq | Groq |
| LLM fallback | — | Gemini Flash | Gemini Pro |

---

## 10. Lead Qualification Engine

### Filosofía

El bot no presiona desde el primer mensaje. Observa señales de compromiso real
durante la conversación. En Margarita, donde la demanda supera a la oferta,
el tiempo del agente es escaso — solo los leads calificados deben llegar a él.

### Señales y Pesos (Rule-Based V1)

```python
# app/qualification/signals.py

SIGNALS = {
    "budget_mentioned": {
        "points": 20,
        "patterns_es": [
            r"\$[\d,\.]+",
            r"[\d,\.]+\s*(dólares|usd|bs)",
            r"(hasta|máximo|mínimo|entre)\s+[\d,\.]+",
            r"(precio|presupuesto|costo)\s+(de|máximo|mínimo)",
            r"(cuánto|cuanto)\s+(cuesta|vale|sale)",
        ],
        "patterns_en": [
            r"\$[\d,\.]+",
            r"[\d,\.]+\s*(dollars|usd)",
            r"(up to|max|between|around)\s+[\d,\.]+",
            r"(price|budget|cost)\s+(of|range|limit)",
        ],
    },
    "zone_specified": {
        "points": 15,
        "margarita_keywords": [
            "pampatar", "porlamar", "el agua", "guacuco", "el yaque",
            "playa caribe", "manzanillo", "casa de campo", "paraíso",
            "rancho de chana", "juan griego", "la asunción",
        ],
        "patterns_es": [r"(en|cerca de|por la zona de|sector)\s+[A-Za-záéíóúñ]+"],
        "patterns_en": [r"(in|near|around|close to)\s+[A-Za-z]+"],
    },
    "property_type_clear": {
        "points": 10,
        "keywords_es": ["apartamento", "apto", "casa", "villa", "local",
                        "planos", "arriendo", "alquiler", "venta", "comprar",
                        "alquilar", "posada", "hotel", "terreno", "vacacional"],
        "keywords_en": ["apartment", "house", "villa", "office", "commercial",
                        "rent", "buy", "purchase", "lease", "hostel", "hotel", "land"],
    },
    "specific_property_queried": {
        "points": 20,
        "logic": "Activado en chat engine cuando usuario hace follow-up de un resultado específico",
    },
    "payment_method_asked": {
        "points": 15,
        "keywords_es": ["crédito", "hipotecario", "financiamiento", "contado",
                        "cuotas", "enganche", "inicial", "banco", "efectivo",
                        "transferencia", "zelle", "criptomoneda", "cripto"],
        "keywords_en": ["mortgage", "financing", "credit", "cash", "installments",
                        "down payment", "bank", "wire transfer", "crypto", "zelle"],
    },
    "time_urgency_expressed": {
        "points": 15,
        "keywords_es": ["urgente", "pronto", "este mes", "inmediato", "ya",
                        "cuanto antes", "disponible", "mudarse", "mudanza"],
        "keywords_en": ["urgent", "soon", "this month", "immediately", "asap",
                        "available", "move in", "moving", "right away"],
    },
    "engagement_depth": {
        "points": 5,
        "logic": "len([m for m in messages if m.role == 'user']) > 5",
    },
    "international_buyer_signal": {
        "points": 10,
        "keywords_es": ["inversión", "roi", "retorno", "desde el exterior",
                        "viviendo fuera", "diaspora", "invertir", "dólares"],
        "keywords_en": ["investment", "roi", "return", "from abroad",
                        "living outside", "invest", "portfolio", "rental income"],
    },
}

THRESHOLDS = {
    "book":    75,   # >= 75: activa booking flow
    "qualify": 40,   # 40-74: preguntas de calificación
    "explore":  0,   # < 40: exploración libre
}
```

### Preguntas de Calificación por Señal Faltante

```python
QUALIFICATION_QUESTIONS = {
    "budget_missing": {
        "es": "¿Tienes un presupuesto aproximado en mente? Esto me ayuda a mostrarte las mejores opciones.",
        "en": "Do you have an approximate budget in mind? This helps me show you the best options.",
    },
    "zone_missing": {
        "es": "¿Hay alguna zona de la isla que prefieras? Por ejemplo Pampatar, El Agua o El Yaque.",
        "en": "Is there an area of the island you prefer? For example Pampatar, El Agua or El Yaque.",
    },
    "type_missing": {
        "es": "¿Buscas para vivir, para invertir o como propiedad vacacional?",
        "en": "Are you looking for a residence, an investment, or a vacation property?",
    },
}
```

---

## 11. Flujo Conversacional

### Diagrama Completo

```
Usuario abre widget en sitio del cliente
       │
       ▼
Widget envía API Key → FastAPI resuelve tenant → crea session_id
       │
       ▼
Bot saluda en idioma detectado:
  ES: "¡Hola! Soy el asistente de [Inmobiliaria]. 🏝️
       ¿Qué tipo de propiedad estás buscando en Margarita?"
  [Comprar] [Arrendar] [Vacacional] [Invertir]
       │
       ▼
Chat Engine: language → memory → filters → search → LLM → qualifier → response
       │
       ├── EXPLORE (score < 40)
       │   → Muestra propiedades. Invita a explorar. No presiona.
       │
       ├── QUALIFY (score 40-74)
       │   → Muestra propiedades + pregunta de calificación al final
       │
       └── BOOK (score >= 75)
           → "¡Perfecto! ¿Te gustaría coordinar una visita? 😊"
                │
                ▼
           Step-by-step (Python valida cada campo con Pydantic v2):
           nombre → email → teléfono → fecha → hora → notas (opcional)
                │
                ▼
           asyncio.gather():
             ├── SQLite: crea lead
             ├── Google Calendar: evento con duración configurable
             ├── WhatsApp al agente (PRIORIDAD 1)
             └── Email al agente (PRIORIDAD 2)
                │
                ▼
           "✅ ¡Listo! Tu visita está confirmada para el [fecha] a las [hora].
            El agente te contactará pronto. ¡Hasta pronto! 🏝️"
```

### Lo que el Chatbot NO Hace en V1

```
❌ No inventa propiedades — solo muestra lo que está en SQLite
❌ No responde sobre procesos legales venezolanos (V2)
❌ No maneja pagos
❌ No procesa imágenes del usuario
❌ No accede a internet para buscar propiedades externas
```

---

## 12. Hybrid Search Engine

### Arquitectura de Capas

```
Input: "busco apto de 3 habitaciones con vista al mar en Pampatar hasta $200k"
       │
       ▼
CAPA 1: Filter Extractor — Regex + Keywords (costo CERO)
    → property_type: "venta"
    → zone: "pampatar"
    → max_price_usd: 200000
    → bedrooms_min: 3
    → vista_al_mar: True
    ¿Al menos 1 filtro encontrado? → CAPA 2
    ¿Todos vacíos? → CAPA 1b

CAPA 1b: LLM Fallback (solo si CAPA 1 retorna FilterQuery vacío)
    → LiteLLM Structured Output → FilterQuery (Pydantic v2)
    → Costo: 1 llamada LLM extra. No en cada mensaje.
    → FilterQuery.extracted_by = "llm_fallback"
       │
       ▼
CAPA 2: SQL Query (SQLAlchemy async) — Verdad Estructural
    SELECT * FROM properties
    WHERE tenant_id = :tenant_id
      AND status = 'disponible'
      AND bedrooms >= 3
      AND price_usd <= 200000
      AND vista_al_mar = 1
      AND location_zone LIKE '%pampatar%'
    ¿Resultados SQL? → CAPA 4 (saltamos sqlite-vec)
    ¿Sin resultados? → CAPA 3
       │
       ▼
CAPA 3: sqlite-vec Similarity Search (solo si SQL está vacío)
    → Genera embedding del query (sentence-transformers, local)
    → Query en property_embeddings_{tenant_id}
    → Metadata filtering estricto aplicado en post-query:
      Python filtra resultados por price_usd, status, bedrooms (desde SQLite)
      Los filtros duros nunca se omiten
    → Top-5 propiedades semánticamente similares
       │
       ▼
CAPA 4: Merge & Rank (Python)
    → SQL results tienen prioridad
    → Complementa con sqlite-vec sin duplicados
    → Límite: máx 3 propiedades por respuesta
    → Lista verificada → LLM la presenta en lenguaje natural
```

### FilterQuery Schema

```python
# app/schemas/search.py
class FilterQuery(BaseModel):
    property_type:  list[str] | None = None
    zone:           str | None = None
    min_price_usd:  float | None = None
    max_price_usd:  float | None = None
    bedrooms_min:   int | None = None
    bathrooms_min:  int | None = None
    area_min_m2:    float | None = None
    vista_al_mar:   bool | None = None
    frente_playa:   bool | None = None
    uso_vacacional: bool | None = None
    tipo_especial:  str | None = None
    raw_query:      str                  # query original siempre presente
    extracted_by:   str = "regex"        # "regex" | "llm_fallback"

    @property
    def is_empty(self) -> bool:
        """True si no hay ningún filtro estructural extraído."""
        return all(
            v is None for v in [
                self.property_type, self.zone, self.min_price_usd,
                self.max_price_usd, self.bedrooms_min, self.bathrooms_min,
                self.vista_al_mar, self.frente_playa, self.uso_vacacional,
            ]
        )
```

### Reglas de Oro

```
REGLA 1: Si SQL tiene resultados → úsalos. sqlite-vec no se invoca.
REGLA 2: Si SQL está vacío → sqlite-vec con post-filtering estricto en Python.
REGLA 3: Si ninguno → bot informa honestamente y sugiere ajustar criterios.
REGLA 4: El LLM NUNCA inventa propiedades.
REGLA 5: Los filtros duros (precio, estado) se aplican siempre en Python después del vector search.
REGLA 6: El filter extractor NO llama al LLM si encuentra al menos 1 filtro estructural.
```

---

## 13. Sistema de Memoria y Contexto

### Memoria de Sesión (RAM) con TTL y Background Cleanup

```python
# app/chat/memory.py

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import asyncio

@dataclass
class SessionMemory:
    session_id:         str
    tenant_id:          str
    language:           str
    messages:           list[dict] = field(default_factory=list)
    qualification_score: int = 0
    is_booking_active:  bool = False
    booking_step:       str | None = None
    last_active:        datetime = field(default_factory=datetime.utcnow)

# Dict global — single-worker en V1
session_store: dict[str, SessionMemory] = {}


async def cleanup_expired_sessions(ttl_minutes: int = 30) -> None:
    """
    Background task que corre cada 5 minutos.
    Elimina sesiones inactivas que superaron el TTL.
    Previene memory leak en producción.
    """
    while True:
        await asyncio.sleep(300)  # cada 5 minutos
        now = datetime.utcnow()
        expired = [
            sid for sid, session in session_store.items()
            if (now - session.last_active) > timedelta(minutes=ttl_minutes)
        ]
        for sid in expired:
            del session_store[sid]
        if expired:
            logger.info("session_cleanup",
                expired_count=len(expired),
                active_count=len(session_store))


# Se registra en lifespan del app:
# app/main.py
# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     asyncio.create_task(cleanup_expired_sessions(settings.SESSION_TTL_MINUTES))
#     yield
```

### Memoria Persistente (SQLite)

- Todos los mensajes se persisten en tabla `messages`
- Cuando un usuario regresa con el mismo `session_id`:
  1. Se detecta que la sesión expiró de RAM (TTL)
  2. Se cargan los últimos `MAX_MESSAGES_IN_CONTEXT` mensajes desde SQLite
  3. Se restaura el score de calificación desde tabla `sessions`
- Retención configurable por tenant (default: 90 días)

### Context Window Management — Truncado Inteligente

```python
# app/chat/memory.py

MAX_MESSAGES_IN_CONTEXT = 20  # configurable por .env

def build_context_messages(
    session: SessionMemory,
    current_properties: list[dict],
) -> list[dict]:
    """
    Solo el turno actual lleva datos completos de propiedades.
    Turnos anteriores usan referencias compactas.
    Evita desborde de tokens y reduce costo de inferencia.
    """
    context = []
    for msg in session.messages[-MAX_MESSAGES_IN_CONTEXT:]:
        if msg.get("has_properties") and msg["role"] == "assistant":
            # Reemplaza payload completo de propiedades por referencia compacta
            content = f"[Presenté {msg['property_count']} propiedades en turno anterior]"
        else:
            content = msg["content"]
        context.append({"role": msg["role"], "content": content})
    return context
```

---

## 14. Notificaciones

### Dispatcher Async con Timeouts y Logging

```python
# app/notifications/dispatcher.py

async def dispatch_booking_notifications(
    lead: Lead,
    tenant: Tenant,
    property: Property | None,
) -> NotificationResult:
    """
    WhatsApp (prioridad 1) y Email (prioridad 2) en paralelo.
    Fallo en uno no detiene el otro (return_exceptions=True).
    Timeouts estrictos. Logging de éxito/fallo por canal.
    """
    tasks = []
    channels = []

    if tenant.whatsapp_enabled and tenant.agent_whatsapp:
        tasks.append(asyncio.wait_for(
            send_booking_whatsapp(lead, tenant, property),
            timeout=settings.EXTERNAL_API_TIMEOUT
        ))
        channels.append("whatsapp")

    if tenant.email_enabled and tenant.agent_email:
        tasks.append(asyncio.wait_for(
            send_booking_email(lead, tenant, property),
            timeout=settings.EXTERNAL_API_TIMEOUT
        ))
        channels.append("email")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for channel, result in zip(channels, results):
        if isinstance(result, Exception):
            logger.error("notification_failed",
                channel=channel, lead_id=str(lead.id),
                tenant_id=str(tenant.id), error=str(result))
        else:
            logger.info("notification_sent",
                channel=channel, lead_id=str(lead.id),
                tenant_id=str(tenant.id))

    return NotificationResult(channels=channels, results=results)
```

### WhatsApp (httpx + Meta Cloud API)

```python
# app/notifications/whatsapp.py

def build_whatsapp_message(lead: Lead, property: Property | None) -> str:
    prop_info = f"🏠 *{property.title}*\n" if property else ""
    intl_flag = "\n🌍 *Comprador internacional*" if lead.is_international else ""
    return (
        f"🔔 *Nueva solicitud de visita*\n\n"
        f"{prop_info}"
        f"👤 *Nombre:* {lead.name}\n"
        f"📧 *Email:* {lead.email}\n"
        f"📱 *Teléfono:* {lead.phone}\n"
        f"📅 *Fecha:* {lead.preferred_date}\n"
        f"🕐 *Hora:* {lead.preferred_time}\n"
        f"⏱️ *Duración:* {lead.visit_duration_minutes} min\n"
        f"📝 *Notas:* {lead.notes or 'Sin notas'}"
        f"{intl_flag}"
    )
```

---

## 15. Google Calendar Integration

### asyncio.to_thread() para SDK Síncrono

```python
# app/calendar/service.py

async def create_calendar_event(
    lead: Lead,
    tenant: Tenant,
    property: Property | None,
) -> str:
    """Crea evento en Google Calendar. Retorna event_id."""

    def _create_event_sync() -> str:
        from datetime import datetime, timedelta

        creds = Credentials.from_service_account_file(
            settings.GOOGLE_CALENDAR_CREDENTIALS_PATH,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        service = build("calendar", "v3", credentials=creds)

        start_dt = datetime.fromisoformat(
            f"{lead.preferred_date}T{lead.preferred_time}:00"
        )
        end_dt = start_dt + timedelta(minutes=lead.visit_duration_minutes)

        event = {
            "summary": f"Visita: {lead.name}"
                       + (f" — {property.title}" if property else ""),
            "description": (
                f"Lead: {lead.name}\n"
                f"Email: {lead.email}\n"
                f"Teléfono: {lead.phone}\n"
                f"Score: {lead.qualification_score}\n"
                f"Internacional: {'Sí' if lead.is_international else 'No'}\n"
                f"Notas: {lead.notes or 'Sin notas'}"
            ),
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": settings.GOOGLE_CALENDAR_TIMEZONE,
            },
            "end": {
                "dateTime": end_dt.isoformat(),         # ← start + duración (no igual a start)
                "timeZone": settings.GOOGLE_CALENDAR_TIMEZONE,
            },
            "attendees": [{"email": lead.email}],
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email", "minutes": 1440},  # 24h antes
                    {"method": "popup", "minutes": 60},    # 1h antes
                ],
            },
        }

        result = service.events().insert(
            calendarId="primary",
            body=event,
            sendUpdates="all",
        ).execute()
        return result["id"]

    event_id = await asyncio.to_thread(_create_event_sync)
    return event_id
```

---

## 16. Observabilidad

### structlog desde Fase 0

```python
# app/core/logging.py

import structlog
import logging

def setup_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if settings.APP_ENV == "development"
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.LOG_LEVEL)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )

logger = structlog.get_logger()
```

### Eventos de Log Estandarizados

```python
# Todos los logs siguen este patrón: evento + contexto estructurado

logger.info("chat_request",        tenant_id=..., session_id=..., language=...)
logger.info("filter_extracted",    extracted_by="regex"|"llm_fallback", filters=...)
logger.info("hybrid_search",       sql_results=N, vec_results=N, duration_ms=...)
logger.info("llm_call",            model=..., tokens=..., duration_ms=..., fallback=False)
logger.info("lead_created",        lead_id=..., score=..., is_international=...)
logger.info("notification_sent",   channel="whatsapp"|"email", lead_id=...)
logger.error("notification_failed", channel=..., error=..., lead_id=...)
logger.info("session_cleanup",     expired_count=N, active_count=N)
logger.info("ingestion_complete",  inserted=N, updated=N, skipped=N, failed=N)
```

OpenTelemetry diferido a V2 — structlog en JSON es suficiente para single-worker.

---

## 17. Prerequisitos de Desarrollo

### Lo que necesitas antes de la Fase 0

```
✅ Python 3.12+
✅ uv (pip install uv)
✅ Git
✅ API Keys: GROQ_API_KEY, GEMINI_API_KEY
✅ Credenciales Google Calendar (credentials.json con Service Account)
✅ Cuenta Meta Business (para WhatsApp en fases avanzadas)
✅ Cuenta Gmail con App Password habilitado (para SMTP)

❌ Ollama — NO requerido (reemplazado por sentence-transformers)
❌ Docker — NO requerido durante desarrollo (se añade en Fase 11)
❌ Redis — NO requerido en V1 (single-worker)
❌ ChromaDB — NO requerido (reemplazado por sqlite-vec)
```

### Primera ejecución de sentence-transformers

```python
# La primera vez que se ejecute ingestion, sentence-transformers descarga el modelo
# Tamaño: ~120MB — se cachea localmente en ~/.cache/huggingface/

from sentence_transformers import SentenceTransformer

# En app/ingestion/embedder.py — se inicializa una vez al startup
model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
# → 384 dimensiones, soporte nativo ES/EN, sin servidor externo
```

---

## 18. Estructura de Carpetas

```
real-estate-chatbot/
│
├── pyproject.toml                  ← uv + dependencias + pytest config
├── uv.lock                         ← versiones pinned (generado por uv)
├── .env                            ← Variables reales (NO en git)
├── .env.example                    ← Template documentado (SÍ en git)
├── .gitignore
├── alembic.ini
├── PLAN.md                         ← Este documento (source of truth)
├── README.md                       ← Setup + prerequisitos + comandos
│
├── demo/
│   └── index.html                  ← HTML demo del widget (Fase 0.5)
│                                      Conecta via WebSocket al backend
│                                      Demuestra el chatbot funcionando
│
├── alembic/
│   ├── env.py                      ← Config async para Alembic
│   ├── script.py.mako
│   └── versions/
│       └── 001_initial_schema.py
│
├── src/
│   └── app/
│       │
│       ├── main.py                 ← FastAPI app factory + lifespan
│       │                              (cleanup task + logging setup)
│       ├── dependencies.py         ← DI: AsyncSession, Tenant (dev default)
│       ├── exceptions.py           ← Excepciones custom del dominio
│       │
│       ├── core/
│       │   ├── config.py           ← Settings (pydantic-settings)
│       │   ├── security.py         ← hash_api_key, generate_api_key
│       │   ├── logging.py          ← setup_logging() con structlog
│       │   └── constants.py        ← Enums: Plan, Language, PropertyType,
│       │                               LeadStatus, QualificationStage, BookingStep
│       │
│       ├── db/
│       │   ├── engine.py           ← create_async_engine + WAL + sqlite-vec load
│       │   ├── base.py             ← DeclarativeBase
│       │   └── models/
│       │       ├── tenant.py
│       │       ├── property.py
│       │       ├── session.py
│       │       ├── message.py
│       │       ├── lead.py
│       │       └── ingestion_log.py
│       │
│       ├── schemas/
│       │   ├── tenant.py
│       │   ├── property.py
│       │   ├── chat.py             ← ChatRequest, ChatResponse, SessionState
│       │   ├── lead.py             ← LeadCreate, LeadResponse, BookingData
│       │   ├── search.py           ← FilterQuery, SearchResult
│       │   └── ingestion.py        ← PropertyCSVRow
│       │
│       ├── ingestion/
│       │   ├── parser.py           ← CSV/Excel → PropertyCSVRow validados
│       │   ├── hasher.py           ← file_checksum + property_hash
│       │   ├── embedder.py         ← raw_embed_text → sqlite-vec
│       │   │                          (sentence-transformers local)
│       │   └── pipeline.py         ← parse → hash → upsert SQLite → embed sqlite-vec
│       │
│       ├── llm/
│       │   ├── client.py           ← LiteLLM async wrapper + retry + timeout
│       │   ├── router.py           ← modelo por tenant/plan + fallback
│       │   └── prompts/
│       │       ├── system_es.py    ← System prompt ES (contexto Margarita)
│       │       ├── system_en.py    ← System prompt EN
│       │       └── booking.py      ← Prompts para recopilación de lead
│       │
│       ├── chat/
│       │   ├── engine.py           ← Motor conversacional (orquestador)
│       │   ├── memory.py           ← RAM session + TTL + cleanup task
│       │   └── language.py         ← Detección ES/EN
│       │
│       ├── search/
│       │   ├── filter_extractor.py ← regex + keywords → FilterQuery
│       │   ├── filter_llm.py       ← LLM Structured Output (fallback)
│       │   ├── sql_search.py       ← SQLAlchemy async query
│       │   ├── vec_search.py       ← sqlite-vec similarity + post-filter Python
│       │   └── hybrid.py           ← Orquesta 4 capas + merge & rank
│       │
│       ├── qualification/
│       │   ├── signals.py          ← SIGNALS, pesos, patrones, zonas Margarita
│       │   ├── extractor.py        ← historial → señales encontradas
│       │   └── scorer.py           ← score → QualificationResult → stage
│       │
│       ├── leads/
│       │   ├── service.py          ← CRUD async de leads
│       │   └── validator.py        ← nombre, email, teléfono, fecha futura
│       │
│       ├── notifications/
│       │   ├── whatsapp.py         ← httpx + Meta Cloud API
│       │   ├── email.py            ← aiosmtplib + template HTML
│       │   └── dispatcher.py       ← asyncio.gather + timeouts + logging
│       │
│       ├── calendar/
│       │   └── service.py          ← Google Calendar + asyncio.to_thread
│       │
│       └── api/
│           ├── middleware.py       ← TenantMiddleware + RateLimitMiddleware
│           └── v1/
│               ├── router.py
│               ├── chat.py         ← WebSocket + heartbeat + POST fallback
│               ├── properties.py
│               ├── leads.py
│               └── ingestion.py
│
└── tests/
    ├── conftest.py
    ├── unit/
    │   ├── test_config.py
    │   ├── test_security.py
    │   ├── test_language.py
    │   ├── test_filter_extractor.py    ← regex + zonas Margarita + campos especiales
    │   ├── test_filter_llm.py          ← mock LiteLLM Structured Output
    │   ├── test_scorer.py              ← señales → scores → stages
    │   ├── test_memory.py              ← TTL, cleanup, restauración
    │   └── test_hasher.py              ← checksums
    │
    ├── integration/
    │   ├── test_ingestion.py           ← CSV → SQLite + sqlite-vec (idempotente)
    │   ├── test_chat_engine.py         ← multi-turno (mock LiteLLM)
    │   ├── test_qualification.py       ← historiales completos → scores
    │   ├── test_leads.py               ← captura, validación, persistencia
    │   ├── test_notifications.py       ← mock httpx + mock aiosmtplib
    │   └── test_calendar.py            ← mock Google API + duración visita
    │
    └── e2e/
        ├── test_chat_flow.py           ← WebSocket: saludo → búsqueda → booking
        ├── test_ingestion_api.py
        └── test_leads_api.py
```

---

## 19. Metodología de Desarrollo

### Filosofía (11 Principios)

```
1.  Async-first
    Todo I/O usa async/await. asyncio.to_thread() solo para libs sync obligatorias.

2.  Validate early, validate hard
    Pydantic v2 en el borde: CSV input, API requests, LLM outputs.
    Nada sucio entra al core.

3.  Fail loudly
    Excepciones custom con contexto. Sin bare except. Sin swallow silencioso.

4.  LLM reasons, Python validates, SQLite is truth
    LLM genera lenguaje. Python extrae señales, calcula scores, decide thresholds.
    SQLite tiene verdad estructural. sqlite-vec tiene verdad semántica.

5.  One responsibility per module
    Cada archivo hace UNA cosa. Sin god objects.

6.  Test before moving to next phase
    Tests de la fase N pasan antes de comenzar la fase N+1.

7.  Config over code
    Comportamiento variable en .env o tenant config.

8.  Context efficiency
    Truncado inteligente. Referencias compactas. Solo turno actual lleva datos completos.

9.  Observability native
    structlog desde Fase 0. Todo evento tiene tenant_id + session_id + duration_ms.

10. Idempotent operations
    Checksums en ingestión. Upserts seguros. Re-subir el mismo CSV no genera duplicados.

11. Resilient by design
    Heartbeat WebSocket. Timeouts estrictos. Rate limiting. Session cleanup task.
    Fallo en WhatsApp no detiene el email y viceversa.
```

### Regla de Commit

```
Antes de cualquier commit:
  ✅ uv run pytest tests/unit/        → verde
  ✅ uv run pytest tests/integration/ → verde (cuando aplique en la fase)
  ✅ Sin imports no usados
  ✅ Sin print() de debug
  ✅ Sin TODO sin número de issue asociado
  ✅ Logs son structlog, no print() ni logging.info()
```

---

## 20. Orden de Construcción — Build Phases

### FASE 0 — Fundación

**Objetivo:** Esqueleto del proyecto. DB con WAL y sqlite-vec. Logging configurado.

```
Tareas:
  ✦ pyproject.toml con todas las dependencias y config de pytest
  ✦ .env.example documentado
  ✦ .gitignore (*.db, .env, __pycache__, .venv, credentials.json,
                 ~/.cache/huggingface/ — modelo de embeddings)
  ✦ core/config.py → Settings pydantic-settings
  ✦ core/logging.py → setup_logging() structlog
  ✦ core/constants.py → Enums completos (BookingStep incluido)
  ✦ core/security.py → hash_api_key, generate_api_key
  ✦ db/base.py → DeclarativeBase
  ✦ db/models/ → todos los modelos ORM (campos Margarita incluidos)
  ✦ db/engine.py → create_async_engine + WAL pragma + sqlite-vec load
  ✦ alembic/ setup async
  ✦ 001_initial_schema.py → primera migración completa

Tests Fase 0:
  ✦ test_config.py → Settings carga desde .env.example
  ✦ test_security.py → hash_api_key es consistente y correcto
  ✦ Verificación manual: alembic upgrade head → tablas creadas + WAL activo + sqlite-vec cargado
```

### FASE 0.5 — HTML Demo Widget

**Objetivo:** Una página HTML que demuestra el chatbot funcionando.
Sin esto el producto no se puede demostrar a un cliente.

```
Tareas:
  ✦ demo/index.html:
      → Abre WebSocket a ws://localhost:8000/ws/chat/{session_id}
      → Input de texto + botón enviar
      → Muestra respuestas del bot en tiempo real
      → Heartbeat ping/pong para mantener conexión activa
      → Sin frameworks JS — HTML/CSS/JS vanilla

Criterio de completitud:
  ✦ Se puede abrir en el browser, escribir un mensaje y ver la respuesta del bot
  ✦ La conexión se mantiene activa > 60 segundos sin desconectarse
```

### FASE 1 — Ingestion Pipeline

**Objetivo:** Un CSV del cliente se convierte en propiedades en SQLite + sqlite-vec.

```
Tareas:
  ✦ schemas/ingestion.py → PropertyCSVRow (Pydantic v2 con campos Margarita)
  ✦ schemas/property.py → PropertyCreate, PropertyResponse
  ✦ ingestion/hasher.py → file_checksum(file) + property_hash(row)
  ✦ ingestion/parser.py:
      → CSV/Excel → PropertyCSVRow validados
      → Precios venezolanos (150.000,00) → float correcto
      → Columnas opcionales faltantes → None correcto
  ✦ ingestion/embedder.py:
      → SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
      → raw_embed_text → vector [384 dims]
      → INSERT/UPDATE en property_embeddings_{tenant_id} (sqlite-vec)
      → Crea tabla virtual si no existe
  ✦ ingestion/pipeline.py:
      1. Checksum del archivo → si procesado sin cambios → skip
      2. Por fila: property_hash
         → igual → skip (skipped_rows++)
         → diferente → upsert SQLite + update sqlite-vec
         → nueva → insert SQLite + insert sqlite-vec
      3. Guardar ingestion_log con diff completo

Tests Fase 1:
  ✦ test_hasher.py → checksum y hash consistentes
  ✦ test_parser.py → CSV válido, inválido, encoding venezolano
  ✦ test_ingestion.py (integration):
      → Primera subida → N insertadas
      → Segunda subida igual → 0 insertadas, N skipped
      → Subida con 1 cambio → 1 updated, N-1 skipped
      → sqlite-vec tiene el count correcto por tenant
```

### FASE 2 — Hybrid Search

**Objetivo:** Dado texto del usuario, retorna propiedades verificadas.

```
Tareas:
  ✦ schemas/search.py → FilterQuery (con is_empty property), SearchResult
  ✦ search/filter_extractor.py → regex + keywords + zonas Margarita
  ✦ search/filter_llm.py → LiteLLM Structured Output (solo si is_empty)
  ✦ search/sql_search.py → SQLAlchemy async query
  ✦ search/vec_search.py:
      → embedding del query (sentence-transformers)
      → sqlite-vec similarity search en property_embeddings_{tenant_id}
      → post-filtering en Python (precio, estado, habitaciones)
  ✦ search/hybrid.py → orquesta 4 capas + merge & rank (máx 3)

Tests Fase 2:
  ✦ test_filter_extractor.py:
      → "apto 3H en pampatar hasta $200k" → filtros correctos
      → "vista al mar" → vista_al_mar=True
      → "algo en el yaque" → zone="el yaque"
      → texto ambiguo → is_empty=True (trigger LLM fallback)
  ✦ test_hybrid_search.py (integration):
      → SQL tiene resultados → sqlite-vec no invocado
      → SQL vacío → sqlite-vec con post-filter estricto
      → Propiedades de otro tenant no aparecen
      → Máx 3 propiedades en output
```

### FASE 3 — LLM Layer

**Objetivo:** Llamadas async a Groq/Gemini con fallback y timeout.

```
Tareas:
  ✦ llm/client.py → LiteLLM wrapper + retry + timeout
  ✦ llm/router.py:
      → ⚠️ ACCIÓN: verificar strings exactos en docs.litellm.ai antes de implementar
      → PLAN_MODELS con Groq + Gemini (sin Mistral en V1)
  ✦ llm/prompts/system_es.py → system prompt con contexto Margarita
  ✦ llm/prompts/system_en.py
  ✦ llm/prompts/booking.py

Tests Fase 3:
  ✦ test_llm_router.py → modelo correcto por plan + fallback chain
  ✦ test_llm_client.py (mock LiteLLM) → success, timeout, rate limit
```

### FASE 4 — Chat Engine

**Objetivo:** Motor conversacional end-to-end. Primera conversación funcional.

```
Tareas:
  ✦ chat/language.py → detección ES/EN
  ✦ chat/memory.py:
      → RAM session store + TTL/LRU
      → cleanup_expired_sessions() background task
      → build_context_messages() con truncado inteligente
      → carga historial desde SQLite si sesión expirada
  ✦ chat/engine.py:
      → language → memory → filter_extractor → hybrid_search
      → context_build → llm_call → qualifier_check
      → response_assembly → memory_save

Tests Fase 4:
  ✦ test_language.py → ES/EN correctos
  ✦ test_memory.py:
      → TTL expirado → recarga desde DB
      → cleanup_task → elimina sesiones expiradas
      → build_context_messages → truncado activo
  ✦ test_chat_engine.py (integration, mock LiteLLM):
      → Primer mensaje → saludo correcto
      → Búsqueda → invoca hybrid_search
      → Respuesta contiene solo propiedades verificadas
```

### FASE 5 — Lead Qualification Engine

**Objetivo:** El sistema evalúa el compromiso del usuario en cada turno.

```
Tareas:
  ✦ qualification/signals.py → SIGNALS + zonas Margarita + señal internacional
  ✦ qualification/extractor.py → historial → señales encontradas
  ✦ qualification/scorer.py → score → QualificationResult → stage

Tests Fase 5:
  ✦ test_scorer.py:
      → score=0 (vacío), score=20 (solo budget), score=45 (3 señales)
      → score=75 → stage="book"
      → international_buyer_signal detectado correctamente
      → zonas de Margarita reconocidas como zone_specified
```

### FASE 6 — Lead Capture & Storage

**Objetivo:** Captura, validación y persistencia correcta de leads.

```
Tareas:
  ✦ schemas/lead.py → LeadCreate, LeadResponse, BookingData
  ✦ leads/validator.py:
      → nombre: min 2 chars
      → email: EmailStr válido
      → teléfono: formato venezolano/internacional
      → fecha: en el futuro
      → hora: formato HH:MM válido
      → visit_duration_minutes: > 0
  ✦ leads/service.py → create_lead, update_lead_status

Tests Fase 6:
  ✦ test_leads.py (integration):
      → Lead válido → guardado con todos los campos
      → Email inválido → ValidationError antes de DB
      → Fecha pasada → ValidationError
      → is_international detectado
      → visit_duration_minutes persiste correctamente
```

### FASE 7 — Notificaciones

**Objetivo:** WhatsApp y Email async y en paralelo al confirmar lead.

```
Tareas:
  ✦ notifications/whatsapp.py → httpx + Meta Cloud API + timeout
  ✦ notifications/email.py → aiosmtplib + template HTML + timeout
  ✦ notifications/dispatcher.py → asyncio.gather + return_exceptions + logging

Tests Fase 7:
  ✦ test_notifications.py (mock httpx + mock aiosmtplib):
      → WhatsApp payload correcto para Meta API
      → Email con asunto y destinatario correctos
      → Fallo WhatsApp → Email continúa
      → Timeout loggeado, no propaga
      → visit_duration_minutes aparece en mensaje WhatsApp
```

### FASE 8 — Google Calendar

**Objetivo:** Evento creado con duración correcta al confirmar visita.

```
Tareas:
  ✦ calendar/service.py → create_calendar_event + asyncio.to_thread
      → end_dt = start_dt + timedelta(minutes=lead.visit_duration_minutes)
      → Timezone "America/Caracas"

Tests Fase 8:
  ✦ test_calendar.py (mock Google API):
      → start != end (duración > 0)
      → Timezone correcto
      → Notas incluyen score e is_international
      → event_id retornado como string no vacío
```

### FASE 9 — Multi-Tenant + Security + Rate Limiting

**Objetivo:** Aislamiento completo. Multi-tenant activado en producción.

```
Tareas:
  ✦ api/middleware.py:
      → TenantMiddleware: X-API-Key SHA-256 → tenant
      → Origin validation: verifica allowed_origins
      → RateLimitMiddleware: slowapi por tenant/IP
  ✦ dependencies.py → get_current_tenant:
      → APP_ENV=development → DEV_TENANT (sin middleware)
      → APP_ENV=production → request.state.tenant

Tests Fase 9:
  ✦ test_security.py:
      → API key válida → tenant resuelto
      → API key inválida → 401
      → Sin API key → 401
      → Tenant inactivo → 403
      → Origin no permitido → 403
      → Rate limit excedido → 429
      → Tenant A no ve datos de Tenant B
```

### FASE 10 — API Layer

**Objetivo:** Endpoints completos. End-to-end funcional.

```
Tareas:
  ✦ main.py:
      → CORSMiddleware + TenantMiddleware + RateLimitMiddleware
      → lifespan: setup_logging() + DB init + cleanup_task
  ✦ api/v1/chat.py:
      → WebSocket /ws/chat/{session_id} + heartbeat ping/pong 30s
      → POST /api/v1/chat (fallback HTTP)
  ✦ api/v1/ingestion.py → POST multipart CSV upload
  ✦ api/v1/properties.py → GET admin
  ✦ api/v1/leads.py → GET admin

Tests Fase 10 (e2e):
  ✦ test_chat_flow.py → WebSocket: saludo → búsqueda → booking completo
  ✦ test_ingestion_api.py → CSV upload → propiedades en DB + sqlite-vec
  ✦ test_leads_api.py → leads del tenant correcto, sin cross-tenant leak
```

### FASE 11 — Docker & Containerización

**Objetivo:** Proyecto containerizado listo para deploy.

```
Tareas:
  ✦ Dockerfile → Python 3.12-slim + uv + sentence-transformers model cache
  ✦ docker-compose.yml → app + volúmenes SQLite (WAL) + model cache
  ✦ .dockerignore → __pycache__, .env, *.db local, .venv
  ✦ README.md → prerequisitos + setup local + Docker completo

Notas Docker:
  → sentence-transformers descarga el modelo en el build (no en runtime)
  → El modelo se monta como volumen para no re-descargarlo en cada build
  → Sin servicio ChromaDB (eliminado), sin servicio Ollama (eliminado)
  → Sin servicio Redis (V2)

Sin cambios en código de aplicación.
```

---

## 21. Convenciones de Código

```python
# Naming
snake_case          # variables, funciones, archivos, tablas DB
PascalCase          # clases, schemas Pydantic, modelos ORM, Enums
SCREAMING_SNAKE     # constantes
kebab-case          # endpoints URL

# Async — SIEMPRE en I/O
async def get_property(session: AsyncSession, property_id: str) -> Property | None:
    result = await session.execute(
        select(Property).where(Property.id == property_id)
    )
    return result.scalar_one_or_none()

# Para SDK sync obligatorio
result = await asyncio.to_thread(sync_function, *args)

# Type hints — SIEMPRE en funciones públicas
async def create_lead(
    session: AsyncSession,
    lead_data: LeadCreate,
    tenant: Tenant,
) -> Lead: ...

# Pydantic v2
class PropertyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    vista_al_mar: bool = False
    visit_duration_minutes: int = 60

# SQLAlchemy async
async with session.begin():           # writes: auto commit/rollback
    session.add(new_lead)

result = await session.execute(       # reads
    select(Property)
    .where(Property.tenant_id == tenant_id)
    .where(Property.status == "disponible")
)

# Error handling
try:
    ...
except SpecificError as e:
    logger.error("context", error=str(e), tenant_id=str(tenant_id))
    raise DomainError(detail=str(e)) from e

# Logging — NUNCA print() o logging.info()
logger.info("event_name", tenant_id=..., session_id=..., duration_ms=...)
```

---

## 22. Variables de Entorno

```bash
# ==============================================================
# .env.example — Real Estate Chatbot Pro v1.2.0
# Isla de Margarita, Venezuela 🏝️
# Copia a .env y rellena los valores reales.
# NUNCA commitees .env al repositorio.
# ==============================================================

# --- App ---
APP_ENV=development              # development | production
APP_NAME="Real Estate Chatbot Margarita"
SECRET_KEY=                      # openssl rand -hex 32
LOG_LEVEL=INFO                   # DEBUG | INFO | WARNING | ERROR

# --- Database (stack unificado SQLite + sqlite-vec) ---
DATABASE_URL=sqlite+aiosqlite:///./chatbot.db
# Producción: DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname

# --- Embeddings (sentence-transformers — sin servidor externo) ---
EMBEDDING_MODEL=paraphrase-multilingual-MiniLM-L12-v2
EMBEDDING_DIMS=384

# --- LLM (LiteLLM los lee automáticamente con estos nombres) ---
GROQ_API_KEY=                    # console.groq.com
GEMINI_API_KEY=                  # aistudio.google.com
# MISTRAL_API_KEY=               # diferido a V2

# --- Email (SMTP) ---
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=                   # App password de Gmail
SMTP_FROM_NAME="Chatbot Inmobiliario Margarita"

# --- WhatsApp Meta Cloud API ---
WHATSAPP_TOKEN=                  # Token permanente de Meta Business
WHATSAPP_API_VERSION=v18.0

# --- Google Calendar ---
GOOGLE_CALENDAR_CREDENTIALS_PATH=./credentials.json
GOOGLE_CALENDAR_TIMEZONE=America/Caracas

# --- Timeouts (segundos) ---
LLM_TIMEOUT=30
EXTERNAL_API_TIMEOUT=15
WEBSOCKET_HEARTBEAT_INTERVAL=30

# --- Memory & Context ---
SESSION_TTL_MINUTES=30
SESSION_CLEANUP_INTERVAL_SECONDS=300   # cada 5 minutos
MAX_MESSAGES_IN_CONTEXT=20
MAX_PROPERTIES_PER_RESPONSE=3

# --- Lead Defaults ---
DEFAULT_VISIT_DURATION_MINUTES=60

# --- Lead Qualifier ---
QUALIFIER_BOOK_THRESHOLD=75
QUALIFIER_QUALIFY_THRESHOLD=40

# --- Rate Limiting ---
RATE_LIMIT_PER_TENANT=60/minute
RATE_LIMIT_PER_IP=120/minute
```

---

## 23. Testing Strategy

### Pirámide de Tests

```
         /\
        /E2E\          ← Pocos, lentos, flujo completo WebSocket
       /──────\
      /  INTEG  \      ← Medianos, DB SQLite en memoria + sqlite-vec
     /────────────\
    /    UNIT       \  ← Muchos, rápidos, componentes aislados
   /──────────────────\
```

### conftest.py — Fixtures Principales

```python
@pytest_asyncio.fixture
async def async_engine():
    """SQLite en memoria con WAL + sqlite-vec cargado."""
    import sqlite_vec
    from sqlalchemy import event

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def configure(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        dbapi_conn.enable_load_extension(True)
        sqlite_vec.load(dbapi_conn)
        dbapi_conn.enable_load_extension(False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_tenant(session) -> Tenant:
    tenant = Tenant(
        id=str(uuid4()),
        name="Test Inmobiliaria Margarita",
        slug="test-margarita",
        plan="pro",
        api_key_hash=hash_api_key("test-api-key-12345"),
        qualification_threshold=75,
        session_ttl_minutes=30,
        visit_duration_minutes=60,
        calendar_enabled=True,
        email_enabled=True,
        whatsapp_enabled=True,
        agent_email="agente@test.com",
        agent_whatsapp="+584120000000",
        allowed_origins='["https://test-margarita.com"]',
        is_active=True,
        created_at=datetime.utcnow().isoformat(),
        updated_at=datetime.utcnow().isoformat(),
    )
    session.add(tenant)
    await session.commit()
    return tenant


@pytest_asyncio.fixture
async def http_client(test_tenant) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        app=app,
        base_url="http://test",
        headers={
            "X-API-Key": "test-api-key-12345",
            "Origin": "https://test-margarita.com",
        },
    ) as client:
        yield client
```

### Mock de LiteLLM

```python
@pytest.mark.asyncio
async def test_chat_returns_only_verified_properties(session, test_tenant, monkeypatch):
    async def mock_acompletion(*args, **kwargs):
        return MockLiteLLMResponse(content="Encontré propiedades verificadas...")

    monkeypatch.setattr(litellm, "acompletion", mock_acompletion)
    # ...
```

---

## 24. Docker Strategy

### Decisión

Docker se añade en **Fase 11**, después de que todo el código está testeado.
No durante desarrollo porque SQLite + sqlite-vec locales no necesitan orquestación.

### Dockerfile

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY demo/ ./demo/

# Pre-descargar modelo de embeddings en build time (no en runtime)
RUN /app/.venv/bin/python -c \
    "from sentence_transformers import SentenceTransformer; \
     SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

RUN mkdir -p /app/data
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### docker-compose.yml

```yaml
version: "3.9"

services:
  chatbot:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data                      # SQLite DB (WAL + sqlite-vec)
      - ./credentials.json:/app/credentials.json:ro
      - huggingface_cache:/root/.cache/huggingface  # modelo de embeddings
    env_file:
      - .env
    environment:
      - DATABASE_URL=sqlite+aiosqlite:///./data/chatbot.db
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  huggingface_cache:     # persiste el modelo entre rebuilds
```

---

## 25. Roadmap de Versiones

### V1 — MVP Pro (Scope Actual)

```
✅ Web widget embebible
✅ HTML demo page (Fase 0.5)
✅ Multi-tenant con aislamiento completo
✅ Stack unificado: SQLite (WAL) + sqlite-vec (sin ChromaDB, sin Ollama)
✅ Embeddings locales: sentence-transformers (ES/EN nativo)
✅ Ingestión CSV idempotente (checksum + upsert + diff log)
✅ Hybrid Search 4 capas (regex → LLM fallback → SQL → sqlite-vec)
✅ Memoria: RAM con TTL/LRU + background cleanup + historial SQLite
✅ Truncado inteligente de contexto
✅ Bilingüe ES/EN
✅ Lead Qualifier Rule-based (8 señales + contexto Margarita)
✅ visit_duration_minutes en leads y Calendar
✅ Google Calendar: evento con duración correcta
✅ WhatsApp al agente (prioridad 1)
✅ Email al agente (prioridad 2)
✅ Multi-LLM: Groq → Gemini (LiteLLM)
✅ AsyncIO 100% + timeouts estrictos
✅ CORS por tenant
✅ Rate limiting (slowapi)
✅ structlog desde Fase 0
✅ Campos específicos Margarita: vista_al_mar, frente_playa, uso_vacacional
✅ Docker al final (Fase 11)
```

### V2 — Expansión

```
⬜ Canal WhatsApp para usuarios finales
⬜ Lead Qualifier v2: LLM-as-Judge (calibrado con data real de V1)
⬜ Proceso inmobiliario venezolano (RAG, experto legal Nueva Esparta)
⬜ Idioma francés (mercado europeo activo en Margarita)
⬜ Panel admin web (CSV upload, ver leads, ver conversaciones)
⬜ Redis para sesiones (multi-worker, escalado horizontal)
⬜ OpenTelemetry completo (tracing distribuido)
⬜ Mistral como tercer fallback LLM
⬜ Configuración planes Básico y Estándar
⬜ Analytics básicos (leads/semana, tasa de conversión)
```

### V3 — Escala

```
⬜ PostgreSQL + pgvector (misma lógica, sqlite-vec → pgvector)
⬜ Multi-tenant panel de administración robusto
⬜ CRM externo via API (Salesforce, HubSpot, Zoho)
⬜ Webhook para actualizaciones de propiedades (sin re-subir CSV)
⬜ Analytics avanzado por tenant
⬜ A/B testing de system prompts
⬜ Expansión: Caracas, Valencia, Maracaibo
```

---

## 26. Principios Arquitecturales

```
┌──────────────────────────────────────────────────────────────────┐
│                    10 PRINCIPIOS DEL PROYECTO                     │
│                                                                   │
│  1. El LLM razona y genera lenguaje.                             │
│     Nunca decide, nunca inventa datos, nunca valida.              │
│                                                                   │
│  2. SQLite tiene la verdad estructural.                          │
│     Precios, m2, disponibilidad, habitaciones, vista_al_mar.     │
│     Si no está en SQLite, no existe.                             │
│                                                                   │
│  3. sqlite-vec tiene la verdad semántica.                        │
│     Descripciones, características cualitativas.                  │
│     Complementa, nunca reemplaza, la verdad estructural.         │
│     Los filtros duros se aplican siempre en Python post-query.   │
│                                                                   │
│  4. Python valida todo.                                          │
│     Scores, thresholds, datos de lead, outputs del LLM.          │
│     La lógica de negocio vive en Python, no en el LLM.          │
│                                                                   │
│  5. Async-first, sin excepción.                                  │
│     Todo I/O usa await. Timeouts estrictos en externos.          │
│     Un proceso maneja miles de conversaciones concurrentes.       │
│                                                                   │
│  6. Simple primero, complejo después.                            │
│     Regex antes que LLM para filtros.                            │
│     Rule-based antes que LLM-as-Judge.                           │
│     SQLite antes que PostgreSQL.                                  │
│     Una sola DB (sqlite-vec) antes que dos sistemas (ChromaDB).  │
│                                                                   │
│  7. Tests antes de avanzar.                                      │
│     La fase N no comienza hasta que N-1 está en verde.           │
│     Sin excepciones a esta regla.                                │
│                                                                   │
│  8. Contexto eficiente.                                          │
│     Truncado inteligente. Referencias compactas en historial.    │
│     Solo el turno actual lleva datos estructurales completos.    │
│                                                                   │
│  9. Observabilidad nativa.                                       │
│     structlog desde día 1. Contexto completo en cada log.        │
│     Sin telemetría no hay optimización.                          │
│                                                                   │
│  10. Resiliencia operativa.                                      │
│      Session cleanup task. Heartbeat WebSocket. Fallback LLM.   │
│      Rate limiting. Fallo en un canal no detiene el otro.        │
└──────────────────────────────────────────────────────────────────┘
```

---

## 27. Decisiones Pendientes para V2+

| Decisión | Razón del diferimiento | Cuándo decidir |
|----------|------------------------|----------------|
| PostgreSQL + pgvector | SQLite + sqlite-vec suficiente para V1. Cambio es DATABASE_URL + schema de vectores | Antes de primer deploy con carga > 100 usuarios concurrentes |
| Redis para sesiones | Sin evidencia de necesidad sin datos de uso reales | V2 cuando haya multi-worker deployment |
| Lead Qualifier LLM-as-Judge | Necesita data real para calibrar (ground truth) | V2 con al menos 100 leads reales etiquetados |
| Proceso inmobiliario venezolano | Requiere experto legal en Nueva Esparta y fuentes verificadas | V2 con consultor especializado |
| WhatsApp canal usuarios | Canal adicional, no MVP. Widget primero | V2 cuando widget esté validado con usuarios reales |
| Panel admin web | Script CLI suficiente para V1 | V2 cuando haya > 3 tenants activos |
| Idioma francés | Mercado europeo activo pero no prioridad MVP | V2 con primer cliente de ese segmento |
| Mistral tercer LLM | Groq + Gemini cubren V1 con overhead mínimo | V2 cuando haya SLA que justifique tercer provider |
| OpenTelemetry | structlog suficiente para single-worker | V2 con multi-servicio o multi-worker |
| Fine-tuning de modelos | Sin data de conversaciones reales | V3 con 10,000+ conversaciones etiquetadas |
| Frameworks agenticos (LangGraph, CrewAI, MCP, A2A) | Fuera de scope hasta necesidad explícita | Decisión futura cuando el negocio lo requiera |

---

*Fin del documento. Versión 1.2.0 — Scope cerrado para V1.*
*Mercado objetivo: Isla de Margarita, Venezuela 🏝️ — "La Perla del Caribe"*
*Cualquier cambio de scope debe actualizarse aquí antes de tocar código.*
*Principios: KISS · YAGNI · Async-first · LLMOps · Una sola DB · Simple y limpio*
