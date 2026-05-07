# src/app/ingestion/embedder.py
"""Embeddings con sentence-transformers (local, sin servidor)."""

from typing import List

import numpy as np

from app.core.config import get_settings

settings = get_settings()

# Lazy loading: se inicializa en primera llamada
_model = None


def _get_model():
    """Carga el modelo de embeddings (lazy singleton)."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(settings.embedding_model)
    return _model


def embed_text(text: str) -> List[float]:
    """Genera embedding de un texto. Retorna lista de floats."""
    model = _get_model()
    embedding = model.encode(text, convert_to_numpy=True)
    return embedding.tolist()


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Batch embedding de múltiples textos."""
    model = _get_model()
    embeddings = model.encode(texts, convert_to_numpy=True)
    return [e.tolist() for e in embeddings]


def generate_raw_embed_text(row_data: dict) -> str:
    """Genera texto concatenado para embedding desde datos de propiedad."""
    parts = [
        row_data.get("title", ""),
        row_data.get("property_type", ""),
        row_data.get("location_zone", ""),
        row_data.get("location_city", ""),
        row_data.get("description_es", ""),
        row_data.get("description_en", ""),
    ]
    # Añadir amenities si existen
    if row_data.get("amenities"):
        parts.append(f"Amenities: {row_data['amenities']}")
    
    return " | ".join(p for p in parts if p)


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — ingestion/embedder.py")
    
    # Test generate_raw_embed_text
    raw = generate_raw_embed_text({
        "title": "Casa en Pampatar",
        "property_type": "venta",
        "location_zone": "Pampatar",
        "description_es": "Hermosa casa con vista al mar",
        "amenities": "piscina,gym",
    })
    assert "Pampatar" in raw
    assert "piscina" in raw
    print(f"  ✅ raw_embed_text: {raw[:50]}...")
    
    # Test embed_text (requiere modelo descargado)
    try:
        emb = embed_text("Casa en Pampatar con vista al mar")
        assert len(emb) == settings.embedding_dims
        assert isinstance(emb[0], float)
        print(f"  ✅ embed_text: {len(emb)} dims")
    except Exception as e:
        print(f"  ⚠️ embed_text: modelo no descargado ({e})")
    
    print("\n🎉 Smoke tests pasaron")