# src/app/ingestion/parser.py
"""Parser de CSV de propiedades con manejo de formatos venezolanos."""

import csv
import io
import json
from typing import Any

from src.app.schemas.ingestion import PropertyCSVRow


class CSVParseError(Exception):
    """Error fatal en parsing de CSV."""
    pass


def parse_properties_csv(
    file_content: bytes,
    filename: str = "upload.csv",
) -> tuple[list[PropertyCSVRow], list[str]]:
    """Parsea CSV de propiedades. Retorna (válidos, errores).

    Maneja:
    - Encoding: UTF-8 → Latin-1 fallback
    - Precios venezolanos: 150.000,00 → 150000.00
    - Columnas opcionales faltantes → None
    - amenities y photos → JSON array string
    """
    valid_rows: list[PropertyCSVRow] = []
    errors: list[str] = []

    # Detectar encoding
    try:
        content_str = file_content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            content_str = file_content.decode("latin-1")
        except UnicodeDecodeError:
            raise CSVParseError(
                f"No se pudo decodificar {filename}. Usa UTF-8 o Latin-1."
            )

    reader = csv.DictReader(io.StringIO(content_str))
    if not reader.fieldnames:
        raise CSVParseError(f"CSV vacío o sin headers: {filename}")

    # Normalizar headers
    fieldnames = [f.strip().lower().replace(" ", "_") for f in reader.fieldnames]
    reader.fieldnames = fieldnames

    required = {"title", "property_type"}
    missing = required - set(fieldnames)
    if missing:
        raise CSVParseError(f"Columnas requeridas faltantes: {missing}")

    for row_num, row in enumerate(reader, start=2):
        try:
            row_data = _normalize_row(row)
            _parse_prices(row_data)
            _parse_lists(row_data)

            property_row = PropertyCSVRow(**row_data)
            valid_rows.append(property_row)
        except Exception as e:
            errors.append(f"Fila {row_num}: {str(e)}")

    return valid_rows, errors


def _normalize_row(row: dict[str, str | None]) -> dict[str, Any]:
    """Limpia valores: strip, vacíos → None."""
    result = {}
    for key, value in row.items():
        if key is None:
            # csv.DictReader asigna valores extra a key None — ignorar
            continue
        if value is None:
            result[key] = None
            continue
        value = value.strip()
        result[key] = value if value != "" else None
    return result


def _parse_prices(row_data: dict[str, Any]) -> None:
    """Normaliza precios venezolanos a float.

    Formatos soportados:
    - 150000.00     → 150000.0
    - 150.000,00    → 150000.0  (formato venezolano punto=miles, coma=decimal)
    - $150,000      → 150000.0  (formato US)
    - 150000        → 150000.0
    """
    for field in ("price_usd", "price_bs"):
        if not row_data.get(field):
            continue

        price_str = str(row_data[field])

        # Quitar símbolos de moneda
        price_str = (
            price_str
            .replace("$", "")
            .replace("USD", "")
            .replace("Bs.", "")
            .replace("Bs", "")
            .replace("bs", "")
            .strip()
        )

        # Normalizar separadores
        if "," in price_str and "." in price_str:
            # Formato venezolano: 1.234.567,89 → 1234567.89
            price_str = price_str.replace(".", "").replace(",", ".")
        elif "," in price_str and price_str.count(",") == 1:
            parts = price_str.split(",")
            if len(parts[1]) <= 2:
                # Coma como decimal: 150000,50 → 150000.50
                price_str = price_str.replace(",", ".")
            else:
                # Coma como miles: 150,000 → 150000
                price_str = price_str.replace(",", "")

        try:
            row_data[field] = float(price_str)
        except ValueError:
            raise ValueError(
                f"Precio inválido en campo '{field}': '{row_data[field]}'"
            )


def _parse_lists(row_data: dict[str, Any]) -> None:
    """Normaliza campos comma-separated a JSON array string.

    El modelo Property almacena amenities y photos como JSON en Text.
    Input:  "piscina, gym, terraza"
    Output: '["piscina", "gym", "terraza"]'
    """
    for field in ("amenities", "photos"):
        if row_data.get(field):
            items = [
                x.strip()
                for x in str(row_data[field]).split(",")
                if x.strip()
            ]
            row_data[field] = json.dumps(items, ensure_ascii=False)


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — ingestion/parser.py")

    # Precio venezolano entre comillas para que csv lo trate como un campo
    csv_content = b"""title,property_type,price_usd,bedrooms,location_zone,amenities
Casa Pampatar,venta,150000.00,3,Pampatar,"piscina, gym"
Apto El Yaque,arriendo,80000,2,El Yaque,
Villa Premium,vacacional,"250.000,50",5,Pampatar,"piscina, jacuzzi, terraza"
"""

    valid, errors = parse_properties_csv(csv_content, "test.csv")

    print(f"  Válidos: {len(valid)} | Errores: {len(errors)}")
    for e in errors:
        print(f"    ⚠️  {e}")

    assert len(valid) == 3, f"Esperaba 3 válidos, got {len(valid)}"
    assert valid[0].price_usd == 150000.0
    assert valid[1].price_usd == 80000.0
    assert valid[2].price_usd == 250000.50, f"Got {valid[2].price_usd}"
    assert valid[0].location_zone == "Pampatar"

    # Verificar amenities como JSON
    import json as _json
    amenities = _json.loads(valid[0].amenities)
    assert amenities == ["piscina", "gym"], f"Got {amenities}"
    assert valid[1].amenities is None  # fila sin amenities
    print(f"  ✅ Parse: {len(valid)} válidos, {len(errors)} errores")
    print(f"  ✅ Amenities como JSON: {valid[0].amenities}")
    print(f"  ✅ Precio venezolano: {valid[2].price_usd}")

    # Test encoding latin-1
    latin = "title,property_type\nCasa Latin,venta\n".encode("latin-1")
    v2, e2 = parse_properties_csv(latin, "latin.csv")
    assert len(v2) == 1, f"Esperaba 1 válido, got {len(v2)}"
    print("  ✅ Latin-1 encoding soportado")

    # Test CSV sin headers requeridos
    try:
        bad_csv = b"nombre,tipo\nCasa,venta\n"
        parse_properties_csv(bad_csv, "bad.csv")
        assert False, "Debería haber lanzado CSVParseError"
    except CSVParseError as e:
        print(f"  ✅ CSVParseError en headers faltantes: {e}")

    # Test precio inválido → error en fila, no crash
    bad_prices = b"title,property_type,price_usd\nCasa,venta,NO_ES_PRECIO\n"
    v3, e3 = parse_properties_csv(bad_prices, "bad_prices.csv")
    assert len(v3) == 0
    assert len(e3) == 1
    assert "Precio inválido" in e3[0]
    print(f"  ✅ Precio inválido capturado en errors[]: {e3[0]}")

    # Test key None ignorada (fila con columnas extra)
    extra_cols = b"title,property_type\nCasa,venta,extra_valor\n"
    v4, e4 = parse_properties_csv(extra_cols, "extra.csv")
    assert len(v4) == 1  # no crash por columna extra
    print("  ✅ Columnas extra ignoradas sin crash")

    print("\n🎉 Smoke tests pasaron")
