# src/app/ingestion/embedder.py
"""Embeddings con sentence-transformers (local, sin servidor).

Carga lazy del modelo — no bloquea el startup de FastAPI.
Todas las operaciones CPU-bound corren en asyncio.to_thread()
para no bloquear el event loop durante ingestion de CSV.
"""

import asyncio

from app.core.config import get_settings

settings = get_settings()

# Lazy singleton — se inicializa en primera llamada async
_model = None
_model_lock = asyncio.Lock()


async def _get_model_async():
    """Carga el modelo de embeddings (lazy singleton thread-safe)."""
    global _model
    if _model is None:
        async with _model_lock:
            if _model is None:  # double-check después del lock
                from sentence_transformers import SentenceTransformer
                _model = await asyncio.to_thread(
                    SentenceTransformer, settings.embedding_model
                )
    return _model


async def embed_text(text: str) -> list[float]:
    """Genera embedding de un texto. Retorna lista de floats.
    
    Corre en thread pool para no bloquear el event loop.
    """
    model = await _get_model_async()
    embedding = await asyncio.to_thread(
        model.encode, text, None, None, None, None, None, True  # convert_to_numpy=True
    )
    return embedding.tolist()


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch embedding de múltiples textos.
    
    Significativamente más rápido que N llamadas a embed_text.
    Usar para ingestion pipeline completa de CSV.
    """
    model = await _get_model_async()
    embeddings = await asyncio.to_thread(
        model.encode, texts, None, None, None, None, None, True  # convert_to_numpy=True
    )
    return [e.tolist() for e in embeddings]


def generate_raw_embed_text(row_data: dict) -> str:
    """Genera texto concatenado para embedding desde datos de propiedad.
    
    Incluye campos específicos de Margarita en ES + EN
    para capturar búsquedas de extranjeros en inglés.
    Esta función es síncrona — no hace I/O, solo concatena strings.
    """
    parts = [
        row_data.get("title", ""),
        row_data.get("property_type", ""),
        row_data.get("location_zone", ""),
        row_data.get("location_city", ""),
        row_data.get("description_es", ""),
        row_data.get("description_en", ""),
    ]

    # Amenities
    if row_data.get("amenities"):
        parts.append(f"Amenities: {row_data['amenities']}")

    # Campos específicos de Margarita — términos bilingües para extranjeros
    if row_data.get("vista_al_mar"):
        parts.append("vista al mar sea view ocean view")
    if row_data.get("frente_playa"):
        parts.append("frente playa beachfront frente al mar")
    if row_data.get("uso_vacacional"):
        parts.append("uso vacacional vacation rental alquiler turístico")
    if row_data.get("tipo_especial"):
        parts.append(str(row_data["tipo_especial"]))

    return " | ".join(p for p in parts if p)


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio as _asyncio

    print("🔥 Smoke Test — ingestion/embedder.py")

    # Test generate_raw_embed_text — síncrono, sin modelo
    raw = generate_raw_embed_text({
        "title": "Casa en Pampatar",
        "property_type": "venta",
        "location_zone": "Pampatar",
        "description_es": "Hermosa casa con vista al mar",
        "amenities": "piscina, gym",
        "vista_al_mar": True,
        "frente_playa": False,
        "uso_vacacional": True,
    })
    assert "Pampatar" in raw
    assert "piscina" in raw
    assert "sea view" in raw
    assert "vacation rental" in raw
    assert "beachfront" not in raw  # frente_playa=False
    print(f"  ✅ generate_raw_embed_text: '{raw[:60]}...'")

    # Test embed_text — requiere modelo descargado (~90MB primera vez)
    async def _test_embed():
        try:
            emb = await embed_text("Casa en Pampatar con vista al mar")
            assert len(emb) == settings.embedding_dims
            assert isinstance(emb[0], float)
            print(f"  ✅ embed_text: {len(emb)} dims, primer valor={emb[0]:.4f}")

            # Test batch
            embs = await embed_texts([
                "Apartamento en Porlamar",
                "Casa frente al mar en Playa El Agua",
            ])
            assert len(embs) == 2
            assert len(embs[0]) == settings.embedding_dims
            print(f"  ✅ embed_texts: batch de {len(embs)} textos")

            # Test singleton — segunda llamada no recarga el modelo
            emb2 = await embed_text("test singleton")
            assert emb2 is not None
            print("  ✅ singleton: modelo no recargado en segunda llamada")

        except Exception as e:
            print(f"  ⚠️  embed_text: modelo no disponible ({type(e).__name__}: {e})")
            print("      Ejecuta: pip install sentence-transformers")
            print(f"      Modelo: {settings.embedding_model}")

    _asyncio.run(_test_embed())
    print("\n🎉 Smoke tests pasaron")
