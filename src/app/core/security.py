# src/app/core/security.py
"""Seguridad: hashing de API keys, generación, verificación.

API keys se hashean con SHA-256 para lookup rápido en DB.
Para storage de secrets sensibles se usa bcrypt via passlib.
"""

import hashlib
import secrets
from passlib.context import CryptContext

# Contexto bcrypt para hashing de passwords/secrets
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_api_key(api_key: str, secret_key: str) -> str:
    return hmac.new(
        secret_key.encode(),
        api_key.encode(),
        hashlib.sha256
    ).hexdigest()

def hash_api_key(api_key: str) -> str:
    """Hash de API key con SHA-256 para lookup en base de datos.
    
    Nota: SHA-256 es para lookup rápido, no para storage de secrets.
    El API key en sí nunca se almacena en texto plano.
    """
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def verify_api_key(plain_api_key: str, hashed_api_key: str) -> bool:
    return secrets.compare_digest(
        hash_api_key(plain_api_key),
        hashed_api_key
    )


def generate_api_key() -> str:
    """Genera un API key seguro y aleatorio.
    
    Formato: pk_live_<token_urlsafe> para distinguir de test keys.
    """
    token = secrets.token_urlsafe(32)
    return f"pk_live_{token}"


def hash_password(password: str) -> str:
    """Hash de password con bcrypt (para credenciales de admin si aplica)."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica un password contra su hash bcrypt."""
    return pwd_context.verify(plain_password, hashed_password)


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — security.py")
    
    # Test generate_api_key
    key1 = generate_api_key()
    key2 = generate_api_key()
    assert key1.startswith("pk_live_")
    assert key1 != key2, "Keys deben ser únicas"
    assert len(key1) > 40
    print(f"  ✅ generate_api_key: {key1[:20]}...")
    
    # Test hash_api_key
    hashed = hash_api_key(key1)
    assert len(hashed) == 64  # SHA-256 hex = 64 chars
    assert hashed == hash_api_key(key1)  # Determinístico
    print(f"  ✅ hash_api_key: determinístico, 64 chars")
    
    # Test verify_api_key
    assert verify_api_key(key1, hashed) is True
    assert verify_api_key("key-falsa", hashed) is False
    print("  ✅ verify_api_key: match y no-match correctos")
    
    # Test hash_password / verify_password
    password = "mi-password-secreto-123"
    hashed_pw = hash_password(password)
    assert verify_password(password, hashed_pw) is True
    assert verify_password("wrong", hashed_pw) is False
    print("  ✅ bcrypt password: hash y verify correctos")
    
    print("\n🎉 Todos los smoke tests pasaron")