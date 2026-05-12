# src/app/ingestion/hasher.py
"""Hashing para idempotencia: checksum de archivo y hash por propiedad."""

import hashlib
import json
from typing import Any


def file_checksum(file_content: bytes) -> str:
    """SHA-256 del contenido completo del archivo."""
    return hashlib.sha256(file_content).hexdigest()

# Campos que no forman parte de la identidad de una propiedad
_HASH_EXCLUDED_FIELDS = frozenset({
    "id", "created_at", "updated_at",
    "property_hash", "raw_embed_text",
    "chroma_doc_id",  # si aplica en el futuro
})

def property_hash(row_data: dict[str, Any]) -> str:
    """SHA-256 de los datos de una propiedad para detectar cambios.
    
    Usa sorted keys para consistencia independiente del orden.
    """
    # Normalizar: solo campos relevantes, ordenados, sin None
    normalized = {
        k: v for k, v in sorted(row_data.items())
        if v is not None and k not in _HASH_EXCLUDED_FIELDS
    }
    content = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — ingestion/hasher.py")
    
    # Test file_checksum
    content = b"test csv content"
    cs1 = file_checksum(content)
    cs2 = file_checksum(content)
    assert len(cs1) == 64
    assert cs1 == cs2, "Determinístico"
    assert cs1 != file_checksum(b"otro contenido")
    print(f"  ✅ file_checksum: {cs1[:16]}... (determinístico)")
    
    # Test property_hash
    row = {"title": "Casa", "price_usd": 100000, "bedrooms": 3}
    h1 = property_hash(row)
    h2 = property_hash({"bedrooms": 3, "title": "Casa", "price_usd": 100000})
    assert h1 == h2, "Orden no importa"
    assert h1 != property_hash({**row, "price_usd": 200000})
    print(f"  ✅ property_hash: {h1[:16]}... (orden-agnóstico)")
    
    print("\n🎉 Smoke tests pasaron")
