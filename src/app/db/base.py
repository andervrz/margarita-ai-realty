# src/app/db/base.py
"""Base declarativa para todos los modelos ORM."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base declarativa de SQLAlchemy 2.0."""
    pass


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — db/base.py")
    assert Base is not None
    assert hasattr(Base, "metadata")
    print("  ✅ DeclarativeBase instanciado correctamente")
    print("\n🎉 Smoke test pasó")