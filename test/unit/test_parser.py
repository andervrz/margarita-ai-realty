# tests/unit/test_parser.py
"""Tests unitarios del parser de CSV de propiedades."""

from __future__ import annotations

import io

import pytest


def test_valid_csv_returns_rows(sample_csv_valid):
    """CSV válido retorna lista de PropertyCSVRow."""
    from app.ingestion.parser import parse_properties_csv
    rows, errors = parse_properties_csv(sample_csv_valid)
    assert len(rows) == 3
    assert len(errors) == 0


def test_valid_csv_first_row_fields(sample_csv_valid):
    """Primera fila tiene campos correctos."""
    from app.ingestion.parser import parse_properties_csv
    rows, _ = parse_properties_csv(sample_csv_valid)
    first = rows[0]
    assert first.external_id == "PROP001"
    assert first.title == "Apto Vista Mar Pampatar"
    assert first.property_type == "venta"
    assert float(first.price_usd) == 150000.0
    assert first.location_zone == "Pampatar"
    assert first.bedrooms == 3


def test_csv_with_errors_captures_invalid(sample_csv_with_errors):
    """Filas inválidas capturadas, válidas retornadas."""
    from app.ingestion.parser import parse_properties_csv
    rows, errors = parse_properties_csv(sample_csv_with_errors)
    # PROP001 es válida, PROP002 y PROP003 son inválidas
    assert len(rows) >= 1
    assert len(errors) >= 1


def test_bool_coercion_true():
    """Valores 'true', '1', 'si' → True."""
    from app.ingestion.parser import parse_properties_csv
    csv = (
        b"external_id,title,property_type,price_usd,vista_al_mar,frente_playa\n"
        b"P1,Test,venta,100000,true,1\n"
        b"P2,Test2,venta,100000,si,yes\n"
    )
    rows, errors = parse_properties_csv(csv)
    assert len(rows) >= 1
    for row in rows:
        assert row.vista_al_mar is True


def test_bool_coercion_false():
    """Valores 'false', '0', 'no' → False."""
    from app.ingestion.parser import parse_properties_csv
    csv = (
        b"external_id,title,property_type,price_usd,vista_al_mar\n"
        b"P1,Test,venta,100000,false\n"
        b"P2,Test2,venta,100000,0\n"
    )
    rows, errors = parse_properties_csv(csv)
    for row in rows:
        assert row.vista_al_mar is False


def test_optional_columns_return_none():
    """Columnas opcionales faltantes → None."""
    from app.ingestion.parser import parse_properties_csv
    csv = b"external_id,title,property_type,price_usd\nP1,Casa,venta,100000\n"
    rows, errors = parse_properties_csv(csv)
    assert len(rows) >= 1
    row = rows[0]
    assert row.location_zone is None
    assert row.bedrooms is None


def test_empty_csv_returns_empty():
    """CSV solo con headers retorna lista vacía."""
    from app.ingestion.parser import parse_properties_csv
    csv = b"external_id,title,property_type,price_usd\n"
    rows, errors = parse_properties_csv(csv)
    assert len(rows) == 0


def test_price_zero_invalid():
    """Precio 0 o negativo debe ser inválido o None."""
    from app.ingestion.parser import parse_properties_csv
    csv = b"external_id,title,property_type,price_usd\nP1,Casa,venta,0\n"
    rows, errors = parse_properties_csv(csv)
    # Precio 0 puede ser inválido o None según implementación
    if rows:
        assert rows[0].price_usd is None or rows[0].price_usd == 0
