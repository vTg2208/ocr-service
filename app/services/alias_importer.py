from sqlalchemy import select

from app.db.models import AdministrativeAlias
from app.services.parcel_normalization import normalize_admin_key


def import_aliases(payload: list[dict], session) -> dict[str, int]:
    report = {"inserted": 0, "updated": 0, "skipped": 0, "invalid": 0}
    for item in payload:
        level = str(item.get("level", "")).casefold()
        alias = str(item.get("alias", "")).strip()
        canonical = str(item.get("canonical_name", "")).strip()
        if level not in {"state", "district", "taluk", "village"} or not alias or not canonical:
            report["invalid"] += 1
            continue
        key = normalize_admin_key(alias)
        existing = session.scalar(select(AdministrativeAlias).where(
            AdministrativeAlias.level == level,
            AdministrativeAlias.normalized_alias == key,
        ))
        if existing is None:
            session.add(AdministrativeAlias(
                level=level, alias=alias, normalized_alias=key,
                canonical_name=canonical, language=item.get("language"),
            ))
            report["inserted"] += 1
        elif (
            existing.alias == alias and existing.canonical_name == canonical
            and existing.language == item.get("language")
        ):
            report["skipped"] += 1
        else:
            existing.alias, existing.canonical_name = alias, canonical
            existing.language = item.get("language")
            report["updated"] += 1
        session.flush()
    return report
