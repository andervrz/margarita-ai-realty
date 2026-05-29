El README que generamos sirve como documentación técnica pero para el repo como **presentación de portfolio** hay que agregarle:

1. **Badges** arriba del todo
2. **Screenshot/GIF del demo** — lo más importante para un cliente o reclutador
3. **Intro más vendedora** — qué problema resuelve, no solo cómo funciona

Versión mejorada para GitHub:

---

```markdown
# 🏝️ Margarita AI Realty

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-WAL+sqlite--vec-003B57?logo=sqlite&logoColor=white)
![LiteLLM](https://img.shields.io/badge/LLM-Groq%20%2B%20Gemini-FF6B35)
![License](https://img.shields.io/badge/License-MIT-green)

**Chatbot conversacional embebible para inmobiliarias en Isla de Margarita, Venezuela.**

Búsqueda híbrida real · Multi-tenant nativo · Lead qualification automático · Bilingüe ES/EN

[Demo en vivo](#demo) · [Documentación API](#api-reference) · [Setup rápido](#setup-local)

</div>

---

## ¿Qué problema resuelve?

La mayoría de chatbots inmobiliarios **inventan propiedades** o dependen de keyword
matching básico. Este sistema diferencia porque:

- **El LLM nunca inventa** — solo razona sobre lo que SQLite confirma
- **Búsqueda híbrida real** — regex (costo $0) → LLM fallback → SQL exacto → sqlite-vec semántico
- **Multi-tenant nativo** — un backend sirve múltiples agencias inmobiliarias
- **Lead qualification automático** — el bot sabe cuándo el usuario está listo para agendar
- **Async 100%** — arquitectura no bloqueante de extremo a extremo

---

## Demo

> Widget HTML vanilla — sin frameworks JS. Abre `demo/index.html` en el browser
> con el servidor corriendo.

```
Usuario: "busco apartamento en Pampatar hasta $200k con vista al mar"

Bot: "Encontré 2 opciones:
     🏠 Apto 3H/2B, 85m² — $185,000 (Pampatar) | 🌊 Vista al mar
     🏠 Apto 2H/2B, 72m² — $160,000 (Pampatar) | 🌊 Vista al mar | 💰 Ideal inversión

     ¿Te gustaría agendar una visita? 📅"
```

*Screenshot o GIF del demo aquí — graba con [LICEcap](https://www.cockos.com/licecap/)
o [Kap](https://getkap.co/) y súbelo como `demo/demo.gif`*

---

## Stack

| Capa | Tecnología | Decisión |
|------|-----------|---------|
| API | FastAPI + Uvicorn | Async-first nativo, WebSocket built-in |
| Base de datos | SQLite WAL + sqlite-vec | Stack unificado: relacional + vectorial en un solo archivo |
| Embeddings | sentence-transformers | Sin servidor externo. `paraphrase-multilingual-MiniLM-L12-v2` (ES/EN nativo) |
| LLM | Groq → Gemini via LiteLLM | Groq: velocidad máxima. Fallback automático si hay outage |
| Notificaciones | WhatsApp Meta API + aiosmtplib | Paralelo con `asyncio.gather` — no bloquea |
| Agendamiento | Google Calendar API v3 | SDK sync envuelto en `asyncio.to_thread` |
| Package manager | uv | Consistencia y velocidad |

**Rechazado intencionalmente:** LangChain, ChromaDB, Ollama, Docker en dev, Redis, LangGraph.
[Ver decisiones documentadas →](#decisiones-arquitecturales-clave)

---

## Arquitectura

```
Browser Widget (JS vanilla)
        │ WebSocket ws://
        ▼
FastAPI ──► TenantMiddleware (X-API-Key → tenant)
        │
        ▼
   Chat Engine
        │
   ┌────┴────┐
   │         │
   ▼         ▼
Hybrid     LiteLLM
Search     (Groq → Gemini)
   │
  ┌┴──────────────────┐
  │ 1. Regex ($0)     │
  │ 2. LLM fallback   │
  │ 3. SQL exacto     │
  │ 4. sqlite-vec     │
  └───────────────────┘
        │
        ▼
   Lead Qualifier
   (rule-based scoring)
        │
   score ≥ 75 → Booking flow
        │
   asyncio.gather(
     Google Calendar,
     WhatsApp,
     Email SMTP
   )
```

---

## Setup Local

### Prerequisitos

```bash
python --version    # 3.11+
uv --version        # instalar: curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Instalación

```bash
git clone https://github.com/andervrz/margarita-ai-realty.git
cd margarita-ai-realty

uv sync                          # instala dependencias + paquete editable

cp .env.example .env             # editar con tus API keys

# Fix de imports (una sola vez)
find src/ -name "*.py" -exec sed -i 's/from src\.app\./from app./g' {} +

uv run alembic upgrade head      # crea tablas SQLite

uv run uvicorn app.main:app --reload --port 8000
```

Abrir [http://localhost:8000/docs](http://localhost:8000/docs) para la API interactiva.

### Variables mínimas para funcionar

```bash
# .env
APP_ENV=development
GROQ_API_KEY=gsk_...          # console.groq.com (gratis)
SECRET_KEY=<openssl rand -hex 32>
DATABASE_URL=sqlite+aiosqlite:///./chatbot.db
```

---

## Tests

```bash
uv run pytest tests/unit/           # rápidos — lógica pura, sin I/O
uv run pytest tests/integration/    # SQLite en memoria + mocks
uv run pytest tests/e2e/            # FastAPI AsyncClient completo
uv run pytest tests/ -v             # suite completa
```

---

## API Reference

Documentación interactiva: [http://localhost:8000/docs](http://localhost:8000/docs)
(solo disponible en `APP_ENV=development`)

### Chat

```bash
# WebSocket (tiempo real)
ws://localhost:8000/api/v1/ws/chat/{session_id}

# POST fallback (compatible con proxies corporativos)
POST /api/v1
{ "message": "busco apartamento en Pampatar" }
```

### Ingestion

```bash
# Subir catálogo de propiedades (CSV/Excel)
POST /api/v1/ingestion
Content-Type: multipart/form-data
file: propiedades.csv

# Idempotente: mismo archivo → devuelve resultado anterior sin re-procesar
```

### Admin

```bash
GET  /api/v1/properties           # catálogo del tenant
GET  /api/v1/properties/search?q= # búsqueda híbrida
GET  /api/v1/leads                 # leads capturados
PATCH /api/v1/leads/{id}/status   # actualizar estado del lead
POST  /api/v1/leads/{id}/notify   # reenviar notificación al agente
```

---

## Flujo de Búsqueda Híbrida

```
Query: "apto 3H en Pampatar hasta $200k vista al mar"
  │
  ├── Capa 1: Regex     → zone=Pampatar, beds=3, max=$200k, vista_al_mar=True  ✓ [costo $0]
  ├── Capa 1b: LLM      → solo si regex no encuentra nada
  ├── Capa 2: SQL       → 4 propiedades verificadas                             ✓
  └── Capa 3: sqlite-vec → solo si SQL vacío

REGLA ABSOLUTA: El LLM nunca inventa propiedades.
                Solo recibe la lista verificada y genera lenguaje natural.
```

---

## Lead Qualification Engine

Sistema rule-based (sin LLM extra) que evalúa compromiso real:

```
Score 0–39  → explore:  muestra propiedades, no presiona
Score 40–74 → qualify:  agrega pregunta suave de presupuesto/zona
Score 75+   → book:     "¿coordinamos una visita? 😊" → flujo paso a paso
```

Señales detectadas automáticamente: presupuesto mencionado (+20),
propiedad específica consultada (+20), zona especificada (+15),
forma de pago preguntada (+15), urgencia temporal (+15),
señal comprador internacional (+10), tipo de propiedad claro (+10).

---

## Multi-Tenant

Un solo backend, múltiples agencias inmobiliarias:

```
X-API-Key: pk_live_xxx  →  Inmobiliaria Pampatar (plan Pro)
X-API-Key: pk_live_yyy  →  Esparta Inmuebles (plan Standard)
```

Cada tenant tiene: propiedades aisladas, leads privados, modelo LLM configurable,
umbrales de calificación propios, credenciales de WhatsApp/Calendar independientes.

---

## Decisiones Arquitecturales Clave

| Rechazado | Por qué |
|-----------|---------|
| LangChain / LlamaIndex | Abstracción innecesaria para catálogos discretos. RAG custom es superior. |
| ChromaDB | Conflicto de proceso con SQLite. sqlite-vec unifica el stack. |
| Ollama | Servidor externo innecesario. sentence-transformers es Python puro. |
| Docker en dev | Frena iteración. Se añade en Fase 11 después de tests. |
| Redis | Sin evidencia de necesidad. session_store en RAM suficiente para V1. |
| LangGraph / CrewAI | Flujo determinista. No requiere frameworks agenticos. |
| MCP | Overengineering. Descubrimiento dinámico innecesario en flujo fijo. |

---

## Roadmap

### V1 ✅ (actual)
Web widget · Multi-tenant · Catálogo CSV · Búsqueda híbrida ·
Lead Qualifier · Google Calendar · WhatsApp + Email · Bilingüe ES/EN

### V2 ⏳
Canal WhatsApp para usuarios finales · Lead Qualifier LLM-as-Judge ·
Panel admin web · RAG proceso inmobiliario venezolano · Mistral como tercer provider

### V3 🔮
PostgreSQL + pgvector en producción · Redis cache · Analytics por tenant ·
Fine-tuning para sector inmobiliario venezolano

---

## Estructura del Proyecto

<details>
<summary>Ver árbol completo</summary>

```
margarita-ai-realty/
├── pyproject.toml          # uv + dependencias + pytest config
├── alembic.ini             # migraciones
├── .env.example            # template de variables
├── demo/index.html         # widget demo (HTML vanilla)
│
├── alembic/versions/
│   └── 001_initial_schema.py
│
└── src/app/
    ├── main.py             # app factory + lifespan + middleware
    ├── core/               # config, constants, logging, security
    ├── db/                 # engine, base, models ORM
    ├── schemas/            # Pydantic v2: chat, property, lead, search
    ├── ingestion/          # CSV → SQLite + sqlite-vec
    ├── search/             # hybrid: regex → LLM → SQL → vec
    ├── chat/               # engine, memory, language detection
    ├── llm/                # client, router, prompts ES/EN/booking
    ├── qualification/      # signals, extractor, scorer
    ├── leads/              # service, validator
    ├── notifications/      # whatsapp, email, dispatcher
    ├── calendar/           # Google Calendar service
    └── api/                # middleware, v1/chat/ingestion/properties/leads
```

</details>

---

## Licencia

MIT — ver [LICENSE](LICENSE)

---

<div align="center">

Desarrollado por [@andervrz](https://github.com/andervrz)

*FastAPI · SQLite · sqlite-vec · sentence-transformers · Groq · LiteLLM*

</div>
```

---
