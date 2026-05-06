# src/app/core/config.py
"""Configuración centralizada — carga exclusiva desde .env externo.

Todas las API keys, secrets y credenciales se leen del archivo .env.
Nunca hardcodeadas. Validación en producción para variables críticas.
"""

import os
import re
from functools import lru_cache
from pydantic import Field, field_validator, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict





class Settings(BaseSettings):
    """Settings tipados alineados con PLAN.md v1.2 — Variables de Entorno."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────
    app_env: str = Field(default="development")
    app_name: str = Field(default="Real Estate Chatbot Margarita")
    secret_key: str = Field(default="")
    log_level: str = Field(default="INFO")

    # ── Database (stack unificado SQLite + sqlite-vec) ────
    database_url: str = Field(default="sqlite+aiosqlite:///./chatbot.db")

    # ── Embeddings (sentence-transformers) ────────────────
    embedding_model: str = Field(default="paraphrase-multilingual-MiniLM-L12-v2")
    embedding_dims: int = Field(default=384)

    # ── LLM API Keys (desde .env únicamente) ──────────────
    groq_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")

    # ── Email (SMTP) ──────────────────────────────────────
    smtp_host: str = Field(default="smtp.gmail.com")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_from_name: str = Field(default="Chatbot Inmobiliario Margarita")

    # ── WhatsApp Meta Cloud API ───────────────────────────
    whatsapp_token: str = Field(default="")
    whatsapp_api_version: str = Field(default="v18.0")

    # ── Google Calendar ───────────────────────────────────
    google_calendar_credentials_path: str = Field(default="./credentials.json")
    google_calendar_timezone: str = Field(default="America/Caracas")

    # ── Timeouts (segundos) ───────────────────────────────
    llm_timeout: int = Field(default=30)
    external_api_timeout: int = Field(default=15)
    websocket_heartbeat_interval: int = Field(default=30)

    # ── Memory & Context ──────────────────────────────────
    session_ttl_minutes: int = Field(default=30)
    session_cleanup_interval_seconds: int = Field(default=300)
    max_messages_in_context: int = Field(default=20)
    max_properties_per_response: int = Field(default=3)

    # ── Lead Defaults ─────────────────────────────────────
    default_visit_duration_minutes: int = Field(default=60)

    # ── Lead Qualifier ──────────────────────────────────
    qualifier_book_threshold: int = Field(default=75)
    qualifier_qualify_threshold: int = Field(default=40)

    # ── Rate Limiting ─────────────────────────────────────
    rate_limit_per_tenant: str = Field(default="60/minute")
    rate_limit_per_ip: str = Field(default="120/minute")

    # ── Validators ────────────────────────────────────────
    @field_validator("rate_limit_per_tenant", "rate_limit_per_ip")
    @classmethod
    def validate_rate_limit_format(cls, v: str) -> str:
        import re
        if not re.match(r"^\d+/(second|minute|hour)$", v):
            raise ValueError(f"Rate limit debe ser formato '60/minute', recibido: {v}")
        return v

    def validate_production(self) -> None:
        """Falla en producción si faltan variables críticas."""
        if self.app_env != "production":
            return
        missing = []
        if not self.groq_api_key:
            missing.append("GROQ_API_KEY")
        if not self.secret_key:
            missing.append("SECRET_KEY")
        if missing:
            raise ValueError(f"Variables de entorno faltantes en producción: {', '.join(missing)}")


@lru_cache
def get_settings() -> Settings:
    """Instancia cacheada. Valida en producción."""
    settings = Settings()
    settings.validate_production()
    return settings