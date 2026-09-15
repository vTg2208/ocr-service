# FRA consistency fixes: authorized scope

The user authorized completion of steps 1 and 2 from the project assessment: make existing access/review rules consistent, protect concurrent reviewer updates, and repair the connections between assets, facts, rules, Atlas, filters, and dashboards.

## Required behavior

- Normal users can access and mutate only their own native cases; reviewer/admin access spans cases. Apply the existing can_view_case policy to old and new case-bound endpoints, including related recommendation/asset/report operations. Keep intentionally shared, minimized Atlas/reference views available.
- Documents attached to a case/evidence must be accessible to the actor; an arbitrary UUID must not expose another user's document through an owned case.
- All legacy-to-native promotion endpoints require reviewer/admin, reviewed intake, current revision, and consistent intake state/linkage. Existing service-only promotion remains an internal primitive, not an unreviewed public route.
- Existing review validation, reasons, immutable history, lifecycle rules, and stale-update rejection must hold at the server. Concurrent reviews cannot both succeed from the same revision; their audits and side effects are transactional. Preserve existing spatial policy; do not invent automatic legal decisions or mandatory new adjudication gates.
- Asset classes, derived facts, reference kinds, and sample rules must interoperate. Preserve numeric cover fractions for numeric rules, provenance, unknown/missing/stale semantics, and historical snapshots. Do not turn a boolean into a numeric cover measurement.
- Case, asset, Atlas, and dashboard location filtering must resolve claim Gram Sabha, holder Gram Sabha, and parcel locations consistently. Dashboard totals must not be silently truncated by queue display limits, must include relevant archive jobs and village assets, and UI context changes must invalidate stale displays.
- A spatial disposition clears the existing reviewer queue only when recorded by a reviewer for the current persisted boundary. Retain old unversioned evidence, but do not treat it as review of a later geometry. This is a queue-integrity rule, not an adjudication/title gate.
- Add producer-to-consumer regression tests using actual services/API boundaries, then run the Python and JavaScript suites.

## Constraints

- Scope is the existing Tamil Nadu implementation. No new ML models, real scheme eligibility definitions, frameworks, or national rollout.
- Retain provenance and history; do not overwrite stored recommendation/fact/extraction evidence to repair contracts.
- Existing test DBs only. Do not touch the user's local database, uploads, secrets, untracked icon, analysis document, or problem-statement file.
- Keep dependencies unchanged unless a demonstrated failure makes a change necessary.
- Use the existing venv at C:/Users/vvthe/Downloads/projects/ocr-service/venv/Scripts/python.exe.
