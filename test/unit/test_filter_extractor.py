# tests/unit/test_filter_extractor.py
"""Tests unitarios del extractor de filtros regex."""

from __future__ import annotations

import pytest


def test_extract_price_max_usd():
    """Extrae precio máximo en USD."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("busco apto hasta $200,000")
    assert f.max_price_usd == 200000.0


def test_extract_price_range():
    """Extrae rango de precio entre X y Y."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("entre $100k y $200k")
    assert f.min_price_usd == 100000.0
    assert f.max_price_usd == 200000.0


def test_extract_price_min():
    """Extrae precio mínimo."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("desde $80,000")
    assert f.min_price_usd == 80000.0


def test_extract_venezuelan_price_format():
    """Formato venezolano: punto como separador de miles."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("hasta $150.000")
    assert f.max_price_usd == 150000.0


def test_extract_zone_pampatar():
    """Detecta zona Pampatar."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("apartamento en Pampatar")
    assert f.zone is not None
    assert "pampatar" in f.zone.lower()


def test_extract_zone_el_yaque():
    """Detecta zona El Yaque."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("quiero algo en El Yaque para windsurf")
    assert f.zone is not None
    assert "yaque" in f.zone.lower()


def test_extract_zone_la_asuncion():
    """Detecta La Asunción."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("busco casa en La Asunción")
    assert f.zone is not None
    assert "asunci" in f.zone.lower()


def test_extract_property_type_apartamento():
    """Detecta tipo apartamento."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("busco apartamento de 3 habitaciones")
    assert f.property_type is not None
    assert "apartamento" in f.property_type


def test_extract_property_type_synonym_apto():
    """Detecta sinónimo 'apto' → 'apartamento'."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("apto 2H en Porlamar")
    assert f.property_type is not None
    assert "apartamento" in f.property_type


def test_extract_bedrooms():
    """Extrae número de habitaciones."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("apartamento 3 habitaciones 2 baños")
    assert f.bedrooms_min == 3
    assert f.bathrooms_min == 2


def test_extract_bedrooms_shorthand():
    """Extrae habitaciones con formato '3H/2B'."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("apto 3H/2B Pampatar")
    assert f.bedrooms_min == 3


def test_extract_vista_al_mar_true():
    """Detecta flag vista_al_mar=True."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("quiero algo con vista al mar")
    assert f.vista_al_mar is True


def test_extract_frente_playa_true():
    """Detecta flag frente_playa=True."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("busco casa frente a la playa")
    assert f.frente_playa is True


def test_extract_uso_vacacional_true():
    """Detecta uso vacacional / inversión Airbnb."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("quiero invertir en airbnb")
    assert f.uso_vacacional is True


def test_extract_vista_al_mar_negation():
    """Detecta negación 'sin vista al mar' → False."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("sin vista al mar, no importa")
    assert f.vista_al_mar is False


def test_empty_query_is_empty():
    """Query vacío retorna FilterQuery con is_empty=True."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("hola como estas")
    assert f.is_empty is True


def test_full_query_all_filters():
    """Query completo con múltiples filtros."""
    from app.search.filter_extractor import extract_filters
    q = "apto 3 habitaciones en Pampatar hasta $200k con vista al mar"
    f = extract_filters(q)
    assert f.zone is not None
    assert "pampatar" in f.zone.lower()
    assert f.bedrooms_min == 3
    assert f.max_price_usd == 200000.0
    assert f.vista_al_mar is True
    assert f.is_empty is False


def test_english_query_ocean_view():
    """Query en inglés detecta ocean view."""
    from app.search.filter_extractor import extract_filters
    f = extract_filters("looking for beachfront house with ocean view")
    assert f.frente_playa is True or f.vista_al_mar is True
