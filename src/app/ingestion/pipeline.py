# src/app/ingestion/pipeline.py
"""Pipeline completo: parse → hash → upsert SQLite → embed sqlite-vec."""

import json
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.ingestion_log import IngestionLog
from src.app.db.models.property import Property
from src.app.ingestion.embedder import embed_text, generate_raw_embed_text
from src.app.ingestion.hasher import file_checksum, property_hash
from src.app.ingestion.parser import parse_properties_csv
from src.app.schemas.ingestion import IngestionResult, PropertyCSVRow


class IngestionPipeline:
    """Orquesta la ingesta de propiedades desde CSV."""
    
    async def process_csv(
        self,
        session: AsyncSession,
        tenant_id: str,
        file_content: bytes,
        filename: str,
    ) -> IngestionResult:
        """Procesa CSV completo: parse → validate → upsert → embed → log."""
        
        # 1. Checksum del archivo
        checksum = file_checksum(file_content)
        
        # Verificar si ya fue procesado sin cambios
        existing_log = await session.execute(
            select(IngestionLog).where(
                IngestionLog.tenant_id == tenant_id,
                IngestionLog.file_checksum == checksum,
            )
        )
        if existing_log.scalar_one_or_none():
            return IngestionResult(
                filename=filename,
                total_rows=0,
                valid_rows=0,
                inserted_rows=0,
                updated_rows=0,
                skipped_rows=0,
                failed_rows=0,
                errors=[],
                status="skipped",  # archivo idéntico ya procesado
            )
        
        # 2. Parsear CSV
        try:
            valid_rows, parse_errors = parse_properties_csv(file_content, filename)
        except Exception as e:
            log = IngestionLog(
                tenant_id=tenant_id,
                filename=filename,
                file_checksum=checksum,
                status="failed",
                errors=json.dumps([str(e)]),
            )
            session.add(log)
            await session.commit()
            return IngestionResult(
                filename=filename,
                total_rows=0, valid_rows=0, inserted_rows=0,
                updated_rows=0, skipped_rows=0, failed_rows=0,
                errors=[str(e)], status="failed",
            )
        
        # 3. Upsert propiedades
        stats = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
        insert_errors: List[str] = []
        
        for row in valid_rows:
            try:
                await self._upsert_property(session, tenant_id, row, stats)
            except Exception as e:
                stats["failed"] += 1
                insert_errors.append(f"{row.title}: {str(e)}")
        
        await session.commit()
        
        # 4. Log del proceso
        total_errors = parse_errors + insert_errors
        status = (
            "success" if not total_errors else
            "partial" if stats["inserted"] > 0 or stats["updated"] > 0 else
            "failed"
        )
        
        log = IngestionLog(
            tenant_id=tenant_id,
            filename=filename,
            file_checksum=checksum,
            total_rows=len(valid_rows) + len(parse_errors),
            valid_rows=len(valid_rows),
            inserted_rows=stats["inserted"],
            updated_rows=stats["updated"],
            skipped_rows=stats["skipped"],
            failed_rows=stats["failed"] + len(parse_errors),
            errors=json.dumps(total_errors) if total_errors else None,
            status=status,
        )
        session.add(log)
        await session.commit()
        
        return IngestionResult(
            filename=filename,
            total_rows=len(valid_rows) + len(parse_errors),
            valid_rows=len(valid_rows),
            inserted_rows=stats["inserted"],
            updated_rows=stats["updated"],
            skipped_rows=stats["skipped"],
            failed_rows=stats["failed"] + len(parse_errors),
            errors=total_errors,
            status=status,
        )
    
    async def _upsert_property(
        self,
        session: AsyncSession,
        tenant_id: str,
        row: PropertyCSVRow,
        stats: dict,
    ) -> None:
        """Inserta o actualiza una propiedad según su hash."""
        from sqlalchemy import select
        
        # Generar hash de la fila
        row_dict = row.model_dump()
        row_dict["tenant_id"] = tenant_id
        new_hash = property_hash(row_dict)
        
        # Buscar por external_id (si existe) o title + zone
        existing = None
        if row_dict.get("external_id"):
            result = await session.execute(
                select(Property).where(
                    Property.tenant_id == tenant_id,
                    Property.external_id == row_dict["external_id"],
                )
            )
            existing = result.scalar_one_or_none()
        
        if not existing:
            # Buscar por title + zone como fallback
            result = await session.execute(
                select(Property).where(
                    Property.tenant_id == tenant_id,
                    Property.title == row.title,
                    Property.location_zone == row.location_zone,
                )
            )
            existing = result.scalar_one_or_none()
        
        # Generar raw_embed_text
        raw_text = generate_raw_embed_text(row_dict)
        
        if existing:
            if existing.property_hash == new_hash:
                stats["skipped"] += 1
                return
            
            # Update
            for key, value in row_dict.items():
                if hasattr(existing, key) and key not in ("id", "created_at"):
                    setattr(existing, key, value)
            existing.property_hash = new_hash
            existing.raw_embed_text = raw_text
            stats["updated"] += 1
        else:
            # Insert
            new_prop = Property(
                tenant_id=tenant_id,
                property_hash=new_hash,
                raw_embed_text=raw_text,
                **{k: v for k, v in row_dict.items() if k != "tenant_id"},
            )
            session.add(new_prop)
            stats["inserted"] += 1
        
        # TODO: Actualizar sqlite-vec embeddings (Fase 2 completa)


# ── Smoke Test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🔥 Smoke Test — ingestion/pipeline.py")
    
    # Solo verificar que la clase existe y tiene los métodos
    pipeline = IngestionPipeline()
    assert hasattr(pipeline, "process_csv")
    assert hasattr(pipeline, "_upsert_property")
    print("  ✅ IngestionPipeline inicializado")
    print("\n🎉 Smoke test pasó (test de integración requiere DB)")