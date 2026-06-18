# Inventario de código: vivo vs. adorno

> Auditoría de `src/app/` para separar **lo que de verdad corre** de lo que es
> código muerto, duplicado o desconectado. El objetivo es tener un mapa antes de
> entrar a limpieza — **este documento no implica que nada se haya borrado todavía.**
>
> **Estado (rama `cleanup/dead-code-and-bugs`):** ya aplicado el borrado seguro
> (archivos y símbolos huérfanos sin tests) y los bugs **1, 3, 4, 5, 6, 8**;
> **bug 2** conectado (las excepciones locales heredan de `DomainError`) y
> **bug 7** corregido (columnas `has_properties`/`property_count` en `messages` +
> migración). Pendiente: las "decisiones de capas canónicas" (sección 1) y
> `calendar/` (sección 8). Los símbolos con test propio se **conservaron**.
>
> Convenciones de acción:
> - 🟢 **VIVO** — se ejecuta y aporta; no tocar.
> - 🗑️ **BORRAR SEGURO** — cero consumidores; eliminar es bajo riesgo.
> - 🔌 **CONECTAR** — funcionalidad construida pero desenchufada; aporta si se cablea.
> - 🐛 **BUG** — error real que afecta o afectará el runtime.
> - 🏗️ **DEUDA** — no es adorno; falta cobertura/robustez.
> - ❓ **DECISIÓN DE PRODUCTO** — feature ausente, no solo código sobrante.

---

## 1. Resumen ejecutivo

El código que **de verdad corre** es un subconjunto coherente y funcional:
`api/middleware` + endpoints v1 → `chat/engine` → `search/hybrid` (regex/LLM/SQL/pgvector)
→ `llm/client` → `qualification` (score) → `leads` (booking) → `notification` (dispatcher).
Más `ingestion` (parser/pipeline) para cargar catálogo y `core` (config/logging/observability).

El resto del proyecto sigue **dos patrones de "ruido"** muy marcados:

1. **Capas canónicas ignoradas.** Hay módulos "oficiales" construidos con cuidado
   que los consumidores reales **no usan**, reimplementando lo mismo local o inline:

   | Capa canónica | Lo que el código usa en su lugar |
   |---|---|
   | `exceptions.py` (DomainError + 18 subclases) | Excepciones locales por módulo, no heredan de DomainError |
   | `dependencies.py` (DI "oficial") | `get_current_tenant` de middleware + lógica inline |
   | `schemas/` (chat, tenant, lead, property) | Schemas locales en cada endpoint |
   | `leads/services.py` (CRUD) | `select(Lead)` inline en endpoints |
   | `constants.py` (enums) | Strings crudos (`"book"`, `"whatsapp"`) |
   | `llm/router.py` (PLAN_MODELS) | `llm/client.py` decide el modelo por su cuenta |

2. **Cadáveres del flujo de booking de 7 pasos.** Una versión anterior del booking
   (nombre→email→teléfono→fecha→**hora→notas**→confirmar, con validación de fecha
   futura/horario comercial y Google Calendar) fue reemplazada por el flujo simplificado
   de 5 pasos. Sus restos quedaron esparcidos en 7+ módulos.

---

## 2. Bugs reales (🐛)

| # | Ubicación | Problema | Impacto |
|---|---|---|---|
| 1 | `api/v1/leads.py:254` | `from app.notifications.dispatcher` (con "s") — el paquete es `notification` | `ModuleNotFoundError` al llamar `POST /leads/{id}/notify` |
| 2 | `exceptions.py` (global) | Las excepciones reales no heredan de `DomainError`; el handler registrado nunca se dispara | Todo error de dominio sale como **500 genérico** (se pierden 429/504/502/400/403) |
| 3 | `search/filter_llm.py:298` | `_build_fallback_filter_query` no separa `dwelling_type` (inconsistente con `to_filter_query`) | Si el LLM cae por la rama de fallback, `"apartamento"` va a `property_type` y no matchea |
| 4 | `search/filter_llm.py:322` | `_select_model` usa `gemini-2.5-pro`; `client.py` usa `gemini-2.5-flash`; `router` usa `2.5-pro`/`2.0-flash` | 3 versiones de Gemini distintas; el extractor de filtros usa otra que el chat |
| 5 | `ingestion/pipeline.py:171` | El embedding se genera **antes** del skip-check por hash | Re-subir CSV con 1 fila cambiada regenera embeddings de las 34 sin cambios |
| 6 | `chat/engine.py` (228, 346) | Usa `memory.messages.append()` directo, no `memory.add_message()` | El cap de RAM nunca se aplica → `messages` crece sin límite en sesiones largas |
| 7 | `chat/engine.py` `_persist_messages` | Guarda solo role+content+created_at; pierde `has_properties`/`property_count` | Tras restaurar sesión desde DB, la compactación de contexto deja de funcionar |
| 8 | `chat/engine.py` `_persist_messages` | user y assistant comparten el mismo `now` (String) sin id secuencial | Orden de mensajes del mismo turno no garantizado al restaurar |

---

## 3. Archivos / módulos completos muertos

| Archivo | Líneas | Estado | Acción |
|---|---|---|---|
| `dependencies.py` | 298 | Cero imports. Duplica `_DEV_TENANT`/`get_current_tenant` de middleware + helpers nunca usados (`get_db_session`, `require_plan`, `get_chat_session_id`, `get_db_and_tenant`) | 🗑️ BORRAR SEGURO |
| `schemas/chat.py` | 64 | `ChatRequest`/`ChatResponse`/`SessionState` — cero imports; endpoints usan versiones locales | 🗑️ BORRAR SEGURO |
| `schemas/tenant.py` | 53 | `TenantConfig` — cero imports; el tenant se maneja como dict | 🗑️ BORRAR SEGURO |
| `calendar/services.py` | 312 | Implementación completa de Google Calendar, cero imports. Asume el flujo de 7 pasos (parsea fecha/hora) | ❓ DECISIÓN DE PRODUCTO |
| `exceptions.py` | 434 | DomainError + 18 subclases; solo `main.py` importa la base + handler, que nunca se dispara | 🔌 CONECTAR (no borrar) |

---

## 4. Inventario por módulo

### `core/`
- 🟢 `config.py` (`get_settings`, lru_cache, validate_production) — núcleo, 18 importadores.
- 🟢 `logging.py` (bridge structlog→Logfire) — 26 importadores, el más usado.
- 🟢 `observability.py` (instrumentación token-opcional) — vivo (main).
- 🟢 `security.hash_api_key` — central (middleware + WS auth).
- 🟡 `security.generate_api_key` — solo `scripts/provision_tenant.py` (herramienta, no runtime).
- 🗑️ `security.verify_api_key`, `hash_password`, `verify_password`, `pwd_context` — bcrypt entero muerto. Reevaluar dependencia `passlib[bcrypt]`.
- 🗑️ `constants.NotificationChannel` (0 usos), `QualificationStage`/`BookingStep`/`Plan` (huérfanos: el código usa strings).
- 🗑️ `config.rate_limit_per_tenant`/`rate_limit_per_ip` (el middleware hardcodea 60/120), `default_visit_duration_minutes` (solo en calendar muerto).
- 🏗️ `MARGARITA_ZONES` triplicado: `constants.py` (dict), `qualification/signals.py` (lista), `search/filter_extractor.py` (canonical).
- Nota: comentario "sentence-transformers" en config.py:41 (el stack es fastembed).

### `chat/`
- 🟢 `engine.process_message` — orquestador central.
- 🟢 `language.detect_language` / `should_switch_language` — ES/EN real, buena calidad.
- 🟢 `memory.get_session_memory`, `save_session_memory`, `build_context_messages`, `cleanup_expired_sessions`.
- 🗑️ `memory.add_message` (engine hace append directo), `delete_session_memory`, `get_active_session_count`, `language.get_language_code`.
- 🐛 Bugs 6, 7, 8 (cap RAM, metadata perdida, orden tras restore).

### `search/`
- 🟢 `hybrid.hybrid_search`, `filter_extractor.extract_filters`, `filter_llm.extract_filters_with_llm`, `sql_search`, `vec_search`, `split_property_terms`, circuit breaker.
- 🗑️ `hybrid._enrich_result` (no-op nunca llamado), `_generate_fallback_suggestions`, `get_circuit_breaker_stats`, `reset_circuit_breaker`, `filter_llm.get_cache_stats`, `clear_cache`, `filter_extractor._contains_word`.
- 🐛 Bugs 3, 4 (dwelling_type en fallback, versión Gemini).
- ❓ Capa 3 (pgvector) solo opera en Postgres; intesteable en SQLite/dev.

### `qualification/`
- 🟢 `score.calculate_qualification_score` → `extractor.extract_signals_from_history` → `signals.detect_signal`. El `total_score` alimenta lead + notificaciones; `stage=="book"` participa en booking.
- 🗑️ `score.should_trigger_booking`, `extractor.calculate_raw_score`, `signals.detect_all_signals`, `score.get_qualification_summary`.
- 🟡 Rama `suggested_questions`/`QUALIFICATION_QUESTIONS`/`_generate_questions`: viva pero casi nunca dispara (el LLM ya pregunta vía system prompt).

### `llm/`
- 🟢 `client.chat_completion` — motor real (engine + filter_llm). Retry/timeout/fallback sólidos.
- 🟢 `prompt`: `get_booking_prompt`, `get_system_prompt_es/en` + templates.
- 🔴 `router.py` casi entero inerte en runtime: `get_chat_model` solo devuelve `.primary`; `resolve_model_route`, `ModelRoute`, `fallback_chain`, `PLAN_MODELS`, `validate_plan_models`, `_provider_has_key` solo en tests. El fallback real lo decide `client.py`.
- 🗑️ `prompt.get_booking_summary` (muerto + residuo 7 pasos), param `user_message` fantasma en `get_system_prompt_*`, docstring "7 pasos" en booking.py.

### `ingestion/`
- 🟢 `pipeline.IngestionPipeline.process_csv`, `parser.parse_properties_csv` (robusto), `hasher.file_checksum`/`property_hash`, `embedder.embed_text`/`generate_raw_embed_text`.
- 🗑️ `embedder.embed_texts` (batch nunca usado — su ausencia causa el bug 5), `hasher.chroma_doc_id` (vestigio ChromaDB).
- 🐛 Bug 5 (embedding antes del skip-check).
- Nota: docstring "sentence-transformers" en embedder.py (usa fastembed); embeddings generados en dev/SQLite que nunca se consultan.

### `schemas/`
- 🟢 `property.PropertyChatSummary` (la más usada), `search.FilterQuery`/`SearchResult`, `ingestion.PropertyCSVRow`/`IngestionResult`.
- 🗑️ `chat.py` entero, `tenant.py` entero, `property.PropertyCreate`, `property.PropertyResponse`, `lead.BookingData`, `lead.LeadResponse`.
- 🟡 `lead.LeadCreate` — semi-muerto: solo lo usa `create_lead()`, que no se llama en runtime.

### `notification/`
- 🟢 `dispatcher.dispatch_booking_notifications` (paralelo + timeouts), `whatsapp.send_booking_whatsapp` (Meta API real), `email.send_booking_email` (aiosmtplib real).
- 🗑️ `dispatcher.send_single_notification` (cero usos), smoke tests podridos (afirman `"No channels enabled"` vs `None`; parchean `src.app.notifications` inexistente).
- 🐛 Bug 1 (import roto en leads.py:254).
- Nota: docstrings con path viejo `# src/app/notifications/...`; mensajes muestran `Hora`/`Duración` placeholder del flujo viejo.

### `calendar/`
- ❓ `services.py` entero: implementación completa, cero consumidores. `Lead.calendar_event_id` nunca se escribe; `tenant.calendar_enabled` no dispara nada.

### `api/`
- 🟢 `middleware.py` (TenantMiddleware, RateLimitMiddleware, get_current_tenant, _lookup_tenant, _parse_origins), `router.py`, los 4 endpoints v1 (implementados, sin stubs).
- 🐛 Bug 1 en `leads.py:254`.
- 🏗️ `RateLimitMiddleware._requests` nunca purga keys de IP/tenant viejas → fuga de memoria lenta en prod.
- Nota: schemas locales en endpoints → causa de `schemas/` huérfano.

### `leads/`
- 🟢 `services.create_lead_from_booking`, `services.update_lead_status`, `validator.validate_name`/`validate_email`/`_validate_phone_value`/`sanitize_name`.
- 🗑️ `services`: `create_lead`, `get_lead_by_id`, `get_leads_by_session`, `get_leads_by_tenant`, `cancel_lead`, `get_lead_stats` (6 de 8 — endpoints hacen queries inline).
- 🗑️ `validator`: `LeadValidator`, `validate_phone`, `validate_booking_datetime`, `sanitize_notes`, `format_phone_for_display` (residuos del flujo de 7 pasos).

### `db/`
- 🟢 `engine.py` (`engine`, `AsyncSessionLocal`, `normalize_database_url`), `base.py`, los 6 modelos (tablas/columnas de datos vivas).
- 🗑️ `engine.get_async_session` (provider huérfano).
- 🏗️ `normalize_database_url` sin tests (lo más delicado: conexión a Neon).
- 🏗️ Modelado: timestamps como String ISO (no DateTime); `updated_at` sin `onupdate`; sin `relationship()`; `Session.tenant_id` nullable inconsistente.

### `main.py`
- 🟢 Vivo y bien estructurado (factory, lifespan, middleware, routers, demo, observability).

---

## 5. Cadáveres del flujo de booking de 7 pasos

El flujo viejo (con hora, notas, duración, fecha futura validada y Google Calendar) dejó
restos en estos puntos. Comparten causa raíz → conviene limpiarlos juntos:

- `calendar/services.py` (asume fecha/hora estructuradas)
- `schemas/lead.BookingData` + `LeadCreate` (validan fecha futura/hora)
- `leads/validator.LeadValidator`, `validate_booking_datetime`, `sanitize_notes`
- `llm/prompt/booking.get_booking_summary`
- `core/constants.BookingStep` (incluye TIME/DURATION/NOTES)
- `db/models/lead.preferred_time` (NOT NULL pero siempre `"Por confirmar"`)
- Campos `Hora`/`Duración` placeholder en mensajes de notificación

---

## 6. Configuración por-tenant ignorada (adorno)

El modelo `Tenant` y el dict del middleware exponen estos campos, pero el código usa
globals/hardcoded:

| Campo de `Tenant` | Qué pasa en runtime |
|---|---|
| `llm_model`, `llm_fallback_1/2` | El override nunca llega: `engine` llama `get_chat_model(tenant_plan="pro")` sin override |
| `plan` | Hardcodeado `"pro"` en esa misma llamada |
| `qualification_threshold` | `score.py` usa `settings.qualifier_book_threshold` (global) |
| `visit_duration_minutes` | Hardcodeado `60` en `create_lead_from_booking` |
| `session_ttl_minutes` | El cleanup usa `settings.session_ttl_minutes` (global) |

Sí se respetan: `allowed_origins`, `agent_email`/`agent_whatsapp`/`whatsapp_phone_id`, `api_key_hash`, `is_active`, `name`/`slug`.

---

## 7. Duplicaciones / doble fuente de verdad

- `_DEV_TENANT` — en `api/middleware.py` y `dependencies.py` (pueden divergir).
- `MARGARITA_ZONES` — 3 definiciones (constants dict / signals lista / filter_extractor canonical).
- Excepciones — `exceptions.py` (canónicas) vs locales por módulo.
- Schemas — `schemas/` (canónicos) vs locales en endpoints.
- Fallback LLM — `router.PLAN_MODELS` vs `client._build_provider_chain`.
- Modelo Gemini — `2.5-pro` (router/filter_llm) vs `2.5-flash` (client).

---

## 8. Plan de acción sugerido (orden por riesgo/valor)

1. **Corregir bug 1** (import roto en `leads.py:254`) — 1 línea, alto impacto.
2. **Decidir `exceptions.py`** (bug 2): conectar (que las excepciones hereden de DomainError) o reducir. Es la diferencia entre "todo 500" y manejo HTTP correcto.
3. **Borrar seguro**: `dependencies.py`, `schemas/chat.py`, `schemas/tenant.py`, y las funciones 🗑️ listadas (cero consumidores).
4. **Limpiar los cadáveres del flujo de 7 pasos** en bloque (sección 5).
5. **Decidir capas canónicas** (sección 1): adoptar o eliminar — schemas, leads/service, enums, router.
6. **Decisiones de producto**: `calendar/` (conectar o quitar), config por-tenant (cablear o documentar como global).
7. **Bugs 3-8** y deuda de modelado, según prioridad.

---

*Generado a partir de la auditoría módulo por módulo de `src/app/`. No se ha modificado código.*
