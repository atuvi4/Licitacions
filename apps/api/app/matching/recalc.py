"""Recalcul controlat del pre-fit d'una empresa (F-06b).

    python -m app.matching.recalc --company <uuid> --out <dir> [--dry-run] [--now ISO]
    python -m app.matching.recalc --company <uuid> --restore <dir> [--dry-run]

Pensat per a un canvi de regles (`RULES_VERSION`) sobre dades reals:

- **Exportacio**: abans de res, `before.json` amb TOTES les columnes de
  `company_tender_matches` i `company_tender_lot_matches` de l'empresa, i el seu
  sha256. Es la copia que permet tornar exactament a l'estat anterior. Les
  columnes JSON (`factors`, tipus `json` a PostgreSQL, que desa el text tal com
  arriba) s'exporten i es restauren com a TEXT literal: tornar-les a serialitzar
  canviaria l'ordre de les claus i la restauracio ja no seria identica.
- **Registre de canvis**: `changes.jsonl` (una linia per fila creada, esborrada o
  modificada, amb el valor anterior i el nou de cada camp) i `summary.json` (els
  recomptes per camp, per categoria, les transicions d'estat i de motiu economic).
- **Dry-run**: tot el calcul dins d'una transaccio que es desfa. No escriu res.
- **Idempotent**: les files el resultat de les quals no canvia no es toquen (ni
  `calculated_at`); una segona execucio no escriu res.
- **Recuperacio**: `--restore <dir>` comprova el sha256 de `before.json`, esborra
  les files de l'empresa i les torna a inserir tal com eren, columna a columna, i
  verifica que el resultat es identic a l'exportacio abans de confirmar.

Nomes toca les dues taules de pre-fit de l'empresa indicada. No toca licitacions,
revisions, events de canvi, candidatures ni cap altra empresa.
"""

from __future__ import annotations

import argparse
import asyncio
import enum
import hashlib
import json
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.matching.rules import RULES_VERSION
from app.matching.service import MatchingService
from app.models.matching import CompanyTenderLotMatch, CompanyTenderMatch

TABLES: dict[str, sa.Table] = {
    "company_tender_matches": CompanyTenderMatch.__table__,  # type: ignore[dict-item]
    "company_tender_lot_matches": CompanyTenderLotMatch.__table__,  # type: ignore[dict-item]
}

#: Que diu el resultat: si canvia, el pre-fit que veu l'usuari canvia.
CONTENT_FIELDS = frozenset(
    {
        "score",
        "status",
        "exclusion_kind",
        "exclusion_reason_code",
        "cpv_score",
        "geographic_score",
        "economic_score",
        "timing_score",
        "data_coverage",
        "factors",
        "best_lot_id",
        "candidate_lot_count",
        "total_lot_count",
    }
)
#: Amb quines regles s'ha calculat.
VERSION_FIELDS = frozenset({"rules_version"})
#: Sobre quina base (perfil, revisio de la licitacio) s'ha calculat.
BASIS_FIELDS = frozenset({"company_profile_changed_at", "tender_changed_at", "tender_revision_id"})
#: Quan.
TIME_FIELDS = frozenset({"calculated_at", "updated_at", "created_at"})


def _category(field: str) -> str:
    if field in CONTENT_FIELDS:
        return "content"
    if field in VERSION_FIELDS:
        return "version"
    if field in BASIS_FIELDS:
        return "basis"
    if field in TIME_FIELDS:
        return "time"
    return "other"


# --------------------------------------------------------------------------- #
# Exportacio                                                                   #
# --------------------------------------------------------------------------- #


def _dump(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _is_json(column: sa.Column) -> bool:
    return isinstance(column.type, sa.JSON)


#: Les columnes JSON de les dues taules (es comparen com a text, es mostren interpretades).
JSON_FIELDS = frozenset(
    column.name for table in TABLES.values() for column in table.columns if _is_json(column)
)


def _load(column: sa.Column, value: Any) -> Any:
    if value is None:
        return None
    column_type = column.type
    if isinstance(column_type, sa.Uuid):
        return uuid.UUID(value)
    if isinstance(column_type, sa.DateTime):
        return datetime.fromisoformat(value)
    enum_class = getattr(column_type, "enum_class", None)
    if enum_class is not None:
        return enum_class(value)
    return value


async def snapshot(session: AsyncSession, company_id: uuid.UUID) -> dict[str, list[dict[str, Any]]]:
    """Totes les files i columnes de pre-fit de l'empresa, en un ordre estable."""
    out: dict[str, list[dict[str, Any]]] = {}
    for name, table in TABLES.items():
        columns = [
            sa.cast(column, sa.Text).label(column.name) if _is_json(column) else column
            for column in table.columns
        ]
        rows = (
            await session.execute(
                sa.select(*columns).where(table.c.company_id == company_id).order_by(table.c.id)
            )
        ).mappings()
        out[name] = [{key: _dump(value) for key, value in row.items()} for row in rows]
    return out


def _canonical(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(data: Any) -> str:
    return hashlib.sha256(_canonical(data)).hexdigest()


# --------------------------------------------------------------------------- #
# Diferencies                                                                  #
# --------------------------------------------------------------------------- #


def _parsed(field: str, value: Any) -> Any:
    return json.loads(value) if field in JSON_FIELDS and isinstance(value, str) else value


def _economic_reason(row: dict[str, Any] | None) -> str | None:
    for factor in _parsed("factors", (row or {}).get("factors")) or []:
        if factor.get("dimension") == "economic":
            return factor.get("reason_code")
    return None


def diff(
    before: dict[str, list[dict[str, Any]]], after: dict[str, list[dict[str, Any]]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Les files que canvien (amb el valor anterior i el nou) i el resum."""
    changes: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for name in TABLES:
        old = {row["id"]: row for row in before.get(name, [])}
        new = {row["id"]: row for row in after.get(name, [])}
        fields: Counter[str] = Counter()
        categories: Counter[str] = Counter()
        statuses: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        counts = Counter(
            rows_before=len(old), rows_after=len(new), created=0, deleted=0, updated=0, unchanged=0
        )
        rows_by_category: Counter[str] = Counter()
        for row_id in sorted(old.keys() | new.keys()):
            previous, current = old.get(row_id), new.get(row_id)
            ref = current or previous or {}
            base = {
                "table": name,
                "id": row_id,
                "tender_id": ref.get("tender_id"),
                "tender_lot_id": ref.get("tender_lot_id"),
            }
            if previous is None:
                counts["created"] += 1
                changes.append({**base, "kind": "created", "row": current})
                continue
            if current is None:
                counts["deleted"] += 1
                changes.append({**base, "kind": "deleted", "row": previous})
                continue
            # Es compara el valor desat (el text, per al JSON); es registra interpretat.
            changed = {
                key: [_parsed(key, previous.get(key)), _parsed(key, current.get(key))]
                for key in previous.keys() | current.keys()
                if previous.get(key) != current.get(key)
            }
            if not changed:
                counts["unchanged"] += 1
                continue
            counts["updated"] += 1
            fields.update(changed.keys())
            row_categories = {_category(key) for key in changed}
            categories.update(row_categories)
            # La categoria mes «forta» de la fila: contingut > base > versio > temps.
            for strongest in ("content", "basis", "version", "time", "other"):
                if strongest in row_categories:
                    rows_by_category[strongest] += 1
                    break
            if "status" in changed:
                statuses[f"{previous['status']} -> {current['status']}"] += 1
            old_reason, new_reason = _economic_reason(previous), _economic_reason(current)
            if old_reason != new_reason:
                reasons[f"{old_reason} -> {new_reason}"] += 1
            changes.append({**base, "kind": "updated", "fields": changed})
        summary[name] = {
            **counts,
            "fields_changed": dict(fields.most_common()),
            "rows_with_category": dict(categories.most_common()),
            "rows_by_strongest_category": dict(rows_by_category.most_common()),
            "status_transitions": dict(statuses.most_common()),
            "economic_reason_transitions": dict(reasons.most_common()),
        }
    return changes, summary


def _write_json(path: Path, data: Any) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True)
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Recalcul                                                                     #
# --------------------------------------------------------------------------- #


async def recalculate(
    session: AsyncSession,
    company_id: uuid.UUID,
    out_dir: Path,
    *,
    dry_run: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Exporta, recalcula, registra i (si no es dry-run) confirma."""
    out_dir.mkdir(parents=True, exist_ok=False)  # mai no es trepitja una evidencia
    before = await snapshot(session, company_id)
    _write_json(out_dir / "before.json", before)
    (out_dir / "before.sha256").write_text(digest(before) + "\n", encoding="utf-8")

    summary_run = await MatchingService(session).recalculate(
        company_id, now=now, commit=False, preserve_unchanged=True
    )
    after = await snapshot(session, company_id)
    changes, summary = diff(before, after)
    # El registre es escrit ABANS de confirmar: si el commit falla, se sap que s'intentava.
    with (out_dir / "changes.jsonl").open("w", encoding="utf-8") as handle:
        for change in changes:
            handle.write(json.dumps(change, ensure_ascii=False, sort_keys=True) + "\n")
    _write_json(out_dir / "after.json", after)

    if dry_run:
        await session.rollback()
        persisted = None
    else:
        await session.commit()
        persisted = digest(await snapshot(session, company_id)) == digest(after)

    result = {
        "company_id": str(company_id),
        "mode": "dry-run" if dry_run else "applied",
        "rules_version": RULES_VERSION,
        "now": now.isoformat() if now else None,
        "tenders_evaluated": summary_run.tenders_evaluated,
        "lots_evaluated": summary_run.lots_evaluated,
        "before_sha256": digest(before),
        "after_sha256": digest(after),
        "persisted_equals_after": persisted,
        "tables": summary,
    }
    _write_json(out_dir / "summary.json", result)
    return result


async def restore(
    session: AsyncSession, company_id: uuid.UUID, source_dir: Path, *, dry_run: bool
) -> dict[str, Any]:
    """Torna les dues taules de l'empresa exactament a `before.json`."""
    data = json.loads((source_dir / "before.json").read_text(encoding="utf-8"))
    expected = (source_dir / "before.sha256").read_text(encoding="utf-8").strip()
    if digest(data) != expected:
        raise SystemExit("before.json no coincideix amb before.sha256: no es restaura res")
    for name, rows in data.items():
        if any(row.get("company_id") != str(company_id) for row in rows):
            raise SystemExit(f"{name}: l'exportacio conte files d'una altra empresa")

    current = await snapshot(session, company_id)
    # Abans de substituir res, l'estat actual queda exportat al costat (mai trepitjat).
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    replaced_dir = source_dir / f"restore_{stamp}"
    replaced_dir.mkdir(exist_ok=False)
    _write_json(replaced_dir / "replaced.json", current)
    (replaced_dir / "replaced.sha256").write_text(digest(current) + "\n", encoding="utf-8")
    # Els lots primer (apunten als matches); despres els matches; i a l'inreves en inserir.
    for name in ("company_tender_lot_matches", "company_tender_matches"):
        table = TABLES[name]
        await session.execute(sa.delete(table).where(table.c.company_id == company_id))
    dialect = session.get_bind().dialect.name
    for name in ("company_tender_matches", "company_tender_lot_matches"):
        table = TABLES[name]
        raw = [column for column in table.columns if _is_json(column)]
        # El JSON torna com el text exportat, sense tornar-lo a serialitzar.
        statement = sa.insert(table).values(
            {
                column.name: (
                    sa.cast(sa.bindparam(f"{column.name}__raw", type_=sa.Text), column.type)
                    if dialect == "postgresql"
                    else sa.type_coerce(sa.bindparam(f"{column.name}__raw"), sa.Text)
                )
                for column in raw
            }
        )
        rows = []
        for row in data[name]:
            values = {
                key: _load(table.c[key], value)
                for key, value in row.items()
                if key not in {column.name for column in raw}
            }
            values.update({f"{column.name}__raw": row.get(column.name) for column in raw})
            rows.append(values)
        if rows:
            await session.execute(statement, rows)
    restored = await snapshot(session, company_id)
    identical = digest(restored) == expected
    if not identical or dry_run:
        await session.rollback()
    else:
        await session.commit()
    _, summary = diff(current, restored)
    return {
        "company_id": str(company_id),
        "mode": "dry-run" if dry_run else ("applied" if identical else "refused"),
        "restored_equals_export": identical,
        "export_sha256": expected,
        "replaced_export": str(replaced_dir),
        "tables": summary,
    }


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


async def _main(args: argparse.Namespace) -> int:
    from app.db.session import SessionFactory

    company_id = uuid.UUID(args.company)
    async with SessionFactory() as session:
        if args.restore:
            result = await restore(session, company_id, Path(args.restore), dry_run=args.dry_run)
        else:
            now = datetime.fromisoformat(args.now) if args.now else None
            if now is not None and now.tzinfo is None:
                raise SystemExit("--now ha de portar zona horaria (p. ex. ...T12:00:00+00:00)")
            result = await recalculate(
                session, company_id, Path(args.out), dry_run=args.dry_run, now=now
            )
    print(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True))
    return 0 if result.get("mode") != "refused" else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.matching.recalc", description=__doc__)
    parser.add_argument("--company", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--out", help="directori nou per a l'exportacio i el registre")
    group.add_argument("--restore", help="directori d'un recalcul anterior (before.json)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--now", help="moment del calcul (ISO 8601), per reproduir-lo")
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
