"""Escaped, printable FRA archive, case, and village reports."""

from datetime import datetime, timezone
from html import escape

from sqlalchemy import select

from app.db.fra_completion_models import AssetFeature, FRAArchiveRecord, FRAVillageProfile
from app.db.fra_models import DSSRecommendation, FRAClaim
from app.db.fra_operational_models import ImageryArtifact
from app.db.models import User


SUPPORTING_WARNING = (
    "Automated and satellite observations are supporting evidence and do not determine legal validity."
)
ADVISORY_WARNING = (
    "Scheme recommendations and referrals are advisory and do not approve or sanction benefits."
)
SYNTHETIC_WARNING = "Synthetic sample data are not authoritative government records."


class ReportNotFoundError(LookupError):
    pass


def _safe(value) -> str:
    text = str(value if value is not None else "")
    if "private://" in text.casefold() or text.casefold().startswith("private/"):
        text = "[private source redacted]"
    return escape(text, quote=True)


def _page(title: str, body: str, *, synthetic: bool) -> str:
    created = datetime.now(timezone.utc).isoformat()
    synthetic_notice = f"<p class='warning'>{_safe(SYNTHETIC_WARNING)}</p>" if synthetic else ""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{_safe(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;color:#17251d;max-width:960px;margin:2rem auto;line-height:1.5}"
        "h1,h2{font-family:Georgia,serif}table{border-collapse:collapse;width:100%}th,td{border:1px solid #9cac9f;padding:.5rem;text-align:left}"
        ".warning{border-left:4px solid #7a5d16;padding:.65rem;background:#fff8df}.meta{color:#526158}"
        "@media print{body{margin:12mm}.no-print{display:none}a{color:inherit;text-decoration:none}}</style>"
        "</head><body>"
        f"<header><p class='meta'>AranyaSetu · Tamil Nadu FRA research prototype · Generated {_safe(created)}</p>"
        f"<h1>{_safe(title)}</h1>{synthetic_notice}"
        f"<p class='warning'>{_safe(SUPPORTING_WARNING)}</p>"
        f"<p class='warning'>{_safe(ADVISORY_WARNING)}</p></header>{body}</body></html>"
    )


def _actor(session, actor_id) -> User:
    actor = session.get(User, actor_id)
    if actor is None:
        raise PermissionError("Authentication is required to render an FRA report.")
    return actor


def render_archive_report(session, record_id, *, actor_id) -> str:
    actor = _actor(session, actor_id)
    if actor.role not in {"reviewer", "admin"}:
        raise PermissionError("Archive reports require a reviewer or admin.")
    record = session.get(FRAArchiveRecord, record_id)
    if record is None:
        raise ReportNotFoundError("FRA archive record not found.")
    rows = "".join(
        f"<tr><th>{_safe(key.replace('_', ' ').title())}</th><td>{_safe(value)}</td></tr>"
        for key, value in sorted((record.reviewed_fields_json or {}).items())
    ) or "<tr><td colspan='2'>No reviewed fields are available.</td></tr>"
    runs = "".join(
        "<section><h3>Extraction run " + _safe(index) + "</h3>"
        f"<p>Entity model: {_safe(run.entity_model_version or 'not recorded')} · Confidence: {_safe(run.overall_confidence)}</p>"
        f"<pre>{_safe(run.raw_text)}</pre></section>"
        for index, run in enumerate(record.extraction_runs, start=1)
    ) or "<p>No extraction runs are available.</p>"
    body = (
        f"<section><h2>Archive record</h2><p>Legacy reference: {_safe(record.legacy_reference)}</p>"
        f"<p>Review state: {_safe(record.review_state)} · State: {_safe(record.state_code)}</p>"
        f"<table><tbody>{rows}</tbody></table></section><section><h2>Extraction history</h2>{runs}</section>"
    )
    return _page(f"Archive report · {record.legacy_reference}", body, synthetic=record.synthetic)


def render_claim_report(session, claim_id, *, actor_id) -> str:
    actor = _actor(session, actor_id)
    claim = session.get(FRAClaim, claim_id)
    if claim is None:
        raise ReportNotFoundError("FRA claim not found.")
    privileged = actor.role in {"reviewer", "admin"}
    holder = (
        f"<p>Rights holder: {_safe(claim.rights_holder.display_name)}</p>"
        if privileged
        else "<p>Rights-holder details are withheld in this privacy-safe report.</p>"
    )
    evidence_rows = "".join(
        f"<tr><td>{_safe(item.category)}</td><td>{_safe(item.legal_role)}</td><td>{_safe(item.verification_state)}</td></tr>"
        for item in claim.evidence_items
    ) or "<tr><td colspan='3'>No evidence items are recorded.</td></tr>"
    body = (
        f"<section><h2>Case summary</h2><p>Claim number: {_safe(claim.claim_number)}</p>"
        f"<p>Right type: {_safe(claim.right_type)} · Status: {_safe(claim.status)}</p>{holder}</section>"
        f"<section><h2>Evidence timeline</h2><table><thead><tr><th>Category</th><th>Legal role</th><th>Verification</th></tr></thead><tbody>{evidence_rows}</tbody></table></section>"
    )
    return _page(
        f"FRA case report · {claim.claim_number}",
        body,
        synthetic=bool(claim.provenance_json.get("synthetic")),
    )


def _metric_rows(statistics: dict) -> str:
    return "".join(
        f"<tr><th>{_safe(str(key).replace('_', ' ').title())}</th><td>{_safe(value)}</td></tr>"
        for key, value in sorted((statistics or {}).items())
    ) or "<tr><td colspan='2'>No derived metrics are available.</td></tr>"


def render_historical_evidence_report(session, claim_id, *, actor_id) -> str:
    actor = _actor(session, actor_id)
    claim = session.get(FRAClaim, claim_id)
    if claim is None:
        raise ReportNotFoundError("FRA claim not found.")
    if actor.role not in {"reviewer", "admin"} and claim.submitted_by != actor.id:
        raise PermissionError("Historical evidence report is not available.")
    artifacts = session.scalars(
        select(ImageryArtifact)
        .where(ImageryArtifact.claim_id == claim.id)
        .order_by(ImageryArtifact.target_year, ImageryArtifact.created_at)
    ).all()
    sections = []
    for artifact in artifacts:
        scene = artifact.imagery_scene
        geometry = artifact.geometry_version
        acquired = (
            f"{scene.acquired_at.day} {scene.acquired_at.strftime('%B %Y')}"
            if scene is not None else "Not recorded"
        )
        flags = ", ".join(str(flag).replace("_", " ") for flag in artifact.quality_flags_json) or "None recorded"
        model_version = artifact.model_version.version if artifact.model_version else "Not attached"
        reviewed = artifact.reviewer.display_name if artifact.reviewer else "Not yet reviewed"
        sections.append(
            "<section class='evidence-card'>"
            f"<h2>Target year {_safe(artifact.target_year or 'Not recorded')}</h2>"
            f"<p><strong>Actual acquisition date:</strong> {_safe(acquired)}</p>"
            f"<p><strong>Provider and collection:</strong> {_safe(scene.provider if scene else 'Not recorded')} Â· {_safe(scene.collection if scene else 'Not recorded')}</p>"
            f"<p><strong>Cloud cover:</strong> {_safe(float(scene.cloud_cover) if scene and scene.cloud_cover is not None else 'Not recorded')}</p>"
            f"<p><strong>Quality flags:</strong> {_safe(flags)}</p>"
            f"<p><strong>Geometry version {geometry.version}:</strong> {_safe(geometry.source)} Â· {_safe(geometry.boundary_quality)}</p>"
            f"<p><strong>Geometry provenance:</strong> {_safe(geometry.provenance_json or 'Not recorded')}</p>"
            f"<p><strong>Processing/model version:</strong> {_safe(artifact.processor_version)} Â· {_safe(model_version)}</p>"
            f"<p><strong>Reviewer disposition:</strong> {_safe(artifact.verification_state.replace('_', ' '))} Â· {_safe(reviewed)}</p>"
            f"<p><strong>Source licence:</strong> {_safe(scene.license_reference if scene else 'Not recorded')}</p>"
            f"<table><caption>Derived metrics</caption><tbody>{_metric_rows(artifact.statistics_json)}</tbody></table>"
            "<p class='meta'>This observation is supporting evidence only. It does not prove tenure or determine the claim outcome.</p>"
            "</section>"
        )
    body = (
        f"<section><h2>Case context</h2><p>Claim number: {_safe(claim.claim_number)} Â· Right type: {_safe(claim.right_type)}</p>"
        "<p>Observations are compared with the recorded claim boundary and require human review.</p></section>"
        + ("".join(sections) or "<section><h2>No historical observations</h2><p>No completed historical evidence artifacts are recorded for this claim.</p></section>")
    )
    return _page(
        f"Historical evidence report Â· {claim.claim_number}", body,
        synthetic=bool(claim.provenance_json.get("synthetic")),
    )


def render_village_report(session, village_id, *, actor_id) -> str:
    _actor(session, actor_id)
    village = session.get(FRAVillageProfile, village_id)
    if village is None:
        raise ReportNotFoundError("FRA village profile not found.")
    assets = session.scalars(
        select(AssetFeature).where(AssetFeature.village_id == village.id).order_by(AssetFeature.asset_class)
    ).all()
    asset_rows = "".join(
        f"<tr><td>{_safe(item.asset_class)}</td><td>{_safe(item.verification_state)}</td><td>{_safe(item.confidence)}</td></tr>"
        for item in assets
    ) or "<tr><td colspan='3'>No asset observations are recorded.</td></tr>"
    provenance = {
        key: ("[private source redacted]" if "private" in str(value).casefold() else value)
        for key, value in (village.provenance_json or {}).items()
    }
    body = (
        f"<section><h2>Administrative context</h2><p>State: Tamil Nadu (TN)</p>"
        f"<p>District: {_safe(village.district_name)} · Block/Taluk: {_safe(village.block_name)} · Village: {_safe(village.village_name)}</p>"
        f"<p>Reference version: {_safe(village.reference_version)}</p>"
        f"<p>Provenance: {_safe(provenance)}</p></section>"
        f"<section><h2>Asset observations</h2><table><thead><tr><th>Class</th><th>Verification</th><th>Confidence</th></tr></thead><tbody>{asset_rows}</tbody></table></section>"
        "<section><h2>Planning scope</h2><p>No benefit is approved or transmitted by this report. Human departmental review is required.</p></section>"
    )
    return _page(
        f"Village planning report · {village.village_name}", body, synthetic=village.synthetic
    )
