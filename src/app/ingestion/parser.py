# src/app/ingestion/parser.py
"""Parser de CSV de propiedades con manejo de formatos venezolanos."""

import csv
import io
from typing import List, Tuple

from src.app.schemas.ingestion import PropertyCSVRow


class CSVParseError(Exception):
    """Error fatal en parsing de CSV."""
    pass


def parse_properties_csv(file_content: bytes, filename: str = "upload.csv") -> Tuple[List[PropertyCSVRow], List[str]]:
    """Parsea CSV de propiedades. Retorna (válidos, errores).
    
    Maneja:
    - Encoding: UTF-8 → Latin-1 fallback
    - Precios venezolanos: 150.000,00 → 150000.00
    - Columnas opcionales faltantes → None
    """
    valid_rows: List[PropertyCSVRow] = []
    errors: List[str] = []
    
    # Detectar encoding
    try:
        content_str = file_content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            content_str = file_content.decode("latin-1")
        except UnicodeDecodeError:
            raise CSVParseError(f"No se pudo decodificar {filename}. Usa UTF-8 o Latin-1.")
    
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
        if value is None:
            result[key] = None
            continue
        value = value.strip()
        result[key] = value if value != "" else None
    return result


def _parse_prices(row_data: dict[str, Any]) -> None:
    """Normaliza precios venezolanos a float."""
    for field in ("price_usd", "price_bs"):
        if row_data.get(field):
            price_str = str(row_data[field])
            # Quitar símbolos
            price_str = price_str.replace("$", "").replace("USD", "").replace("Bs", "").replace("bs", "").strip()
            
            # Normalizar separadores
            if "," in price_str and "." in price_str:
                # 1.234.567,89 → 1234567.89
                price_str = price_str.replace(".", "").replace(",", ".")
            elif "," in price_str and price_str.count(",") == 1:
                parts = price_str.split(",")
                if len(parts[1]) <= 2:
                    price_str = price_str.replace(",", ".")
                else:
                    price_str = price_str.replace(",", "")
            
            try:
                row_data[field] = float(price_str)
            except ValueError:
                row_data[field] = None


def _parse_lists(row_data: dict[str, Any]) -> None:
    """Normaliza campos comma-separated."""
    for field in ("amenities", "photos"):
        if row_data.get(field):
            items = [x.strip() for x in str(row_data[field]).split(",") if x.strip()]
            row_data[field] = ",".join(items)


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — ingestion/parser.py")
    
    csv_content = b"""title,property_type,price_usd,bedrooms,location_zone
Casa Pampatar,venta,150000.00,3,Pampatar
Apto El Yaque,arriendo,80000,2,El Yaque
Villa Premium,vacacional,250.000,50,5,Pampatar"""
    
    valid, errors = parse_properties_csv(csv_content, "test.csv")
    assert len(valid) == 3, f"Esperaba 3 válidos, got {len(valid)}"
    assert valid[0].price_usd == 150000.0
    assert valid[1].price_usd == 80000.0
    # 250.000,50 → 250000.50 (formato venezolano)
    assert valid[2].price_usd == 250000.50
    assert valid[0].location_zone == "Pampatar"
    print(f"  ✅ Parse: {len(valid)} válidos, {len(errors)} errores")
    
    # Test encoding latin-1
    latin = "Casa,venta,100000,2,Zona".encode("latin-1")
    v2, e2 = parse_properties_csv(latin)
    assert len(v2) == 1
    print("  ✅ Latin-1 encoding soportado")
    
    print("\n🎉 Smoke tests pasaron")