🏝️ margarita-ai-realty

AI-powered conversational platform for real estate in Margarita Island.
Enables intelligent property search, lead qualification, and automated client interaction using LLMs and hybrid search.

🚀 Overview

margarita-ai-realty is a production-oriented AI system designed to modernize real estate operations in emerging markets like Margarita Island (Venezuela).

The platform acts as a conversational assistant that:

Understands user intent via LLMs
Filters properties using structured + semantic search
Qualifies leads automatically
Supports real estate agencies with scalable, multi-tenant infrastructure
🎯 Problem

Real estate workflows in Margarita are:

Fragmented (WhatsApp, Instagram, manual catalogs)
Inefficient (slow response times, repetitive queries)
Hard to scale (manual lead qualification)
💡 Solution

A conversational AI assistant that:

Automates client interaction
Filters properties instantly
Pre-qualifies buyers
Reduces operational friction
🧠 Core Features
🤖 Conversational AI (LLM-powered)
🔍 Hybrid property search (SQL + semantic)
🧾 Structured intent extraction (Pydantic schemas)
🏢 Multi-tenant architecture (per agency)
💬 Session memory (DB-backed, stateless API)
📊 Lead qualification engine
🔌 Async integrations (calendar, notifications, CRM-ready)
⚡ Async-first backend (FastAPI + PostgreSQL)
🏗️ Architecture
High-Level Flow
User Input
   ↓
[Intent Extraction - LLM]
   ↓
[Structured Filters]
   ↓
 ┌───────────────┐
 │   SQL Engine  │ ← strict filtering
 └───────────────┘
         ↓ (fallback / complement)
 ┌───────────────┐
 │ Vector Search │ ← semantic similarity
 └───────────────┘
         ↓
[Context Builder]
         ↓
[LLM Response Generator]
         ↓
User Response
🧩 System Layers
1. Intent Layer
LLM-based structured extraction
Converts natural language → FilterQuery
2. Search Layer
SQL (PostgreSQL): deterministic filtering
Vector DB (Chroma): semantic retrieval
Metadata filtering enforced
3. Orchestration Layer
Combines results
Applies business logic
4. LLM Layer
Response generation
Guardrails (no hallucinated properties)
5. Persistence Layer
PostgreSQL
Sessions, messages, leads, properties
🗄️ Database Design (Simplified)
tenants
 ├── id
 ├── name
 └── plan

properties
 ├── id
 ├── tenant_id
 ├── price
 ├── location
 ├── features

sessions
 ├── id
 ├── tenant_id
 └── created_at

messages
 ├── id
 ├── session_id
 ├── role
 └── content

leads
 ├── id
 ├── session_id
 └── score
⚙️ Tech Stack
Backend: FastAPI (async)
Database: PostgreSQL + asyncpg
LLM Orchestration: LiteLLM
Vector DB: ChromaDB
Validation: Pydantic
Infra-ready: Docker (planned)
Testing: Pytest
🔄 Execution Strategy

The system follows:

Full architecture defined → incremental activation

Modules are categorized as:
🟢 ACTIVE → implemented in current phase
🟡 DORMANT → defined but not activated
🧪 EXPERIMENTAL → under evaluation
🧪 Development Phases
🟢 V0 — Core System
Chat + LLM response
SQL property search
Basic session tracking
Single LLM provider
🟡 V1 — Production Foundation
Multi-tenant support
Hybrid search (SQL + vector)
Lead qualification
Async integrations (mock → real)
LLM fallback system
🔵 V1.1 — Optimization
Prompt tuning
Performance improvements
Query optimization
Observability (logs + metrics)
🟣 V2 — Scale
Multi-agency SaaS
CRM integrations
Analytics dashboard
Advanced personalization
📊 Roadmap
[ V0 ] ──▶ Core Chat + SQL Search
   ↓
[ V1 ] ──▶ Hybrid Search + Leads + Multi-tenant
   ↓
[ V1.1 ] ──▶ Optimization + Observability
   ↓
[ V2 ] ──▶ SaaS Platform + Integrations
🛡️ Design Principles
❗ LLM never invents properties (grounded responses only)
⚡ Async-first architecture
🧩 Separation of concerns (layered system)
🧠 LLM used for reasoning, not business logic
📦 Stateless API (DB-backed memory)
🔒 Multi-tenant isolation
⚠️ Known Trade-offs
Hybrid search adds complexity (controlled via feature flags)
LLM dependency introduces latency/cost
Multi-tenant increases system overhead early
🧠 LLM Strategy
Structured outputs (Pydantic schemas)
Tool-like behavior for filter extraction
Controlled prompts to avoid hallucination
Fallback providers via LiteLLM
🧪 Testing Strategy
Unit tests → core logic
Integration tests → DB + search
E2E tests → conversation flows
🚀 Future Improvements
Voice interface
WhatsApp integration
Recommendation system
Smart pricing insights
Agent-based automation
📌 Status

🚧 Active development — architecture stabilized, implementation in progress.

👨‍💻 Author

Ander V. — AI Engineer
Focused on LLM systems, automation, and applied AI products.
