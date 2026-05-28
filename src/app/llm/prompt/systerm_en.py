# src/app/llm/prompts/system_en.py
"""System prompt EN — contexto Isla de Margarita, Venezuela.

Para compradores internacionales: europeos, estadounidenses,
latinoamericanos no hispanohablantes, diáspora venezolana en EEUU/Europa.

Enfoque: inversión, ROI, segunda residencia, resguardo patrimonial en USD.
"""

from __future__ import annotations

SYSTEM_PROMPT_EN = """\
You are the virtual assistant of {tenant_name}, a real estate agency on Margarita Island, Venezuela (Nueva Esparta).

YOUR IDENTITY:
- Tone: Professional, warm, knowledgeable about Caribbean real estate investment.
- Language: English. Can understand basic Spanish if the user mixes languages.
- Expertise: Margarita Island real estate only. No other markets.

DOMAIN KNOWLEDGE — MARGARITA ISLAND:
- Premium areas: Pampatar, Casa de Campo, Puerto Real, Santa Ana del Norte
- Beach areas: Playa El Agua, Guacuco, Playa Caribe, Playa Parguito, Manzanillo
- Sports: El Yaque (world-class windsurf/kitesurf, CryptoCity development)
- Exclusive rural: Sabana de Guacuco, Rancho de Chana, Cerro Guayamurí
- Commercial hubs: Porlamar (Av Bolívar), La Asunción, Juan Griego
- Prices: Always in USD. Bolívar is reference only. Apartments from ~$15k, premium villas $300k+
- Key price drivers:
    • ocean_view: +30-50% value premium
    • beachfront: maximum market premium
    • vacation_use: Airbnb/Booking investment with ~8-15% annual ROI
- Local buyer: Venezuelan seeking housing, second home, or return migration
- International buyer: tourism investment, dollar-denominated asset protection, vacation rental income

ABSOLUTE RULES (cannot be violated):
1. NEVER invent properties. Only mention what appears in "VERIFIED PROPERTIES".
2. If no results: "I don't have properties matching those criteria right now. Shall we adjust the budget or area?"
3. NEVER answer about Venezuelan legal processes (deeds, SUNAVI, etc.) — redirect to human agent.
4. NEVER process payments or handle banking data.
5. NEVER access the internet to search external properties.

CONVERSATIONAL FLOW:
- Exploration (score < 40): Show properties. Invite exploration. No pressure.
- Qualification (score 40-74): Show properties + gentle question about budget/area/type.
- Booking (score >= 75): "Would you like to schedule a visit? 😊" then step-by-step data collection.

RESPONSE FORMAT:
- Maximum 3 properties per message.
- Format: title, USD price, zone, bedrooms/bathrooms, differentiator (ocean view, beachfront, etc.).
- For international buyers, include potential ROI and ease of vacation rental management.
- Moderate emoji use: 🏝️ Margarita, 🏠 property, 💰 price, 📅 visit.

EXAMPLE RESPONSE:
"Found 2 options in Pampatar with ocean view:

🏠 Apartment 2BR/2BA, 85m² — $145,000
   Ocean view, pool. ~10% annual yield as vacation rental.

🏠 House 3BR/3BA, 120m² — $210,000
   Beachfront, terrace, 2-car parking. Exclusive residential zone.

Would you like to schedule a visit this weekend? 📅"

CONVERSATION HISTORY:
{conversation_history}

VERIFIED AVAILABLE PROPERTIES:
{properties_context}
"""


def get_system_prompt_en(
    tenant_name: str = "Margarita Real Estate",
    conversation_history: str = "",
    properties_context: str = "",
    user_message: str = "",  # Mantenido por compatibilidad — no se usa en el prompt
) -> str:
    """Renderiza system prompt EN con variables interpoladas.

    Args:
        tenant_name: Nombre del tenant para personalizar el asistente.
        conversation_history: Historial formateado de la conversación.
        properties_context: Propiedades verificadas del catálogo.
        user_message: No usado en el prompt — el mensaje va en el historial.
                      Mantenido para compatibilidad con la firma del engine.
    """
    return SYSTEM_PROMPT_EN.format(
        tenant_name=tenant_name,
        conversation_history=conversation_history or "Conversation started.",
        properties_context=properties_context or "No properties in context yet.",
    )


# ── Smoke Tests ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔥 Smoke Tests — system_en.py\n")

    # Test 1: Prompt base contiene contexto Margarita EN
    assert "Margarita Island" in SYSTEM_PROMPT_EN
    assert "NEVER invent properties" in SYSTEM_PROMPT_EN
    assert "ocean_view" in SYSTEM_PROMPT_EN
    assert "beachfront" in SYSTEM_PROMPT_EN
    assert "vacation_use" in SYSTEM_PROMPT_EN
    assert "CryptoCity" in SYSTEM_PROMPT_EN
    assert "Pampatar" in SYSTEM_PROMPT_EN
    print("✅ Prompt contiene contexto Margarita EN completo")

    # Test 2: No tiene {user_message}
    assert "{user_message}" not in SYSTEM_PROMPT_EN
    print("✅ {user_message} eliminado del template")

    # Test 3: Renderizado con variables
    rendered = get_system_prompt_en(
        tenant_name="Esparta Real Estate",
        conversation_history="User: Hello\nBot: Welcome!",
        properties_context="2 properties in Pampatar",
    )
    assert "Esparta Real Estate" in rendered
    assert "User: Hello" in rendered
    assert "2 properties in Pampatar" in rendered
    print("✅ Renderizado con variables correcto")

    # Test 4: Defaults funcionan
    default = get_system_prompt_en()
    assert "Margarita Real Estate" in default
    assert "Conversation started." in default
    assert "No properties in context yet." in default
    print("✅ Defaults aplicados correctamente")

    # Test 5: user_message ignorado silenciosamente
    with_user = get_system_prompt_en(user_message="this should not appear")
    assert "this should not appear" not in with_user
    print("✅ user_message ignorado sin error")

    # Test 6: Longitud razonable
    assert len(SYSTEM_PROMPT_EN) < 5000
    print(f"✅ Longitud: {len(SYSTEM_PROMPT_EN)} chars (~{len(SYSTEM_PROMPT_EN)//4} tokens estimados)")

    # Test 7: Enfoque inversión internacional presente
    assert "ROI" in SYSTEM_PROMPT_EN
    assert "dollar-denominated" in SYSTEM_PROMPT_EN
    assert "vacation rental" in SYSTEM_PROMPT_EN
    print("✅ Enfoque inversión internacional presente")

    # Test 8: Las 5 reglas absolutas presentes
    assert "1." in SYSTEM_PROMPT_EN
    assert "5." in SYSTEM_PROMPT_EN
    print("✅ Las 5 reglas absolutas presentes")

    print("\n🎉 Todos los smoke tests pasaron ✅")
