Latest workflow update: see [PORTAL_V4_WORKFLOW.md](PORTAL_V4_WORKFLOW.md).

# Latest fixes

For current installation and contact verification, read [PORTAL_V3_FIXES.md](PORTAL_V3_FIXES.md). The current migration head is `20261112_0016`.

# AranyaSetu community portal v2

## Install this cumulative update

This ZIP contains all changed/new files relative to the original uploaded `ocr-service` repository, including the first user-portal update. Apply it over your existing project directory; merge the included folders. It does not include or replace `.env`, uploaded documents, database volumes, or Git history.

1. Back up your project/database.
2. Extract the ZIP into your existing `ocr-service` folder. In particular, merge the contents of `migrations/versions`, not just files directly inside `migrations`.
3. From PowerShell in the project directory, run:

```powershell
docker compose up -d --build api worker
docker compose exec api python -m scripts.migrate
docker compose exec api alembic current
docker compose ps
```

Expected migration revision: `20261112_0015`. API startup now runs the migration command automatically; the explicit invocation above also verifies the tables in the application's database. The command selects the application's `DATABASE_URL`, not an independently configured migration database. Readiness rejects a schema missing required portal tables.

This directly addresses the reported `relation "account_credentials" does not exist`: the update includes its original `0014` migration, the new `0015` migration, and startup verification. No database reset or `docker compose down -v` is required. If a revision is already stamped but its tables were manually removed, migration verification will stop with an explicit missing-table error; share that output rather than stamping or resetting the database.

Open:

- `http://localhost:8000/login` — account login/registration and existing staff demo.
- `http://localhost:8000/my-plots` — user portal, opening on Dashboard by default.
- `http://localhost:8000/staff-services` — reviewer/admin community services.
- `http://localhost:8000/docs` — live API documentation.

Refresh the browser after applying the update. For local HTTP use `ENVIRONMENT=development`. Production cookies require HTTPS.

## User journey

The first screen is Dashboard. Users can browse announcements, schemes, and approved community boundaries without finishing their profile or mapping land. A non-blocking completion prompt leads to Profile.

| Section | Functionality |
|---|---|
| Dashboard | Profile progress; owned plot/review/ticket/resource counts; published announcements and schemes; recent staff replies and review feedback |
| My profile | Name, locality, optional community, livelihood, optional private phone, chosen Atlas label, label-sharing permission; plot mapping links |
| Community Atlas | Approved snapshot polygons, different owner colors, consent-controlled labels, owning-user indication, shared legend |
| My plots | Private saved drafts, open/edit/export, submission history and staff feedback |
| Map boundary | Click ordered corners, close and adjust the polygon, see estimated area, check intersections, save, submit for review |
| Schemes & resources | Current/upcoming published schemes with sources; verified observations belonging to the signed-in user; assistance link |
| Help & tickets | General, technical, boundary dispute, or scheme-assistance requests; optional own-plot/scheme links; document attachments; two-way correspondence |

Profile completion records the presence of name, locality, livelihood and at least one mapped plot. Completion is not approval or legal eligibility. Optional phone/community fields are not required.

## Boundary review and publication

- Draft plots remain private in `user_plots`.
- Submit creates an immutable geometry/label snapshot for that plot revision in `plot_submissions`.
- Repeated submission of the same revision returns the same snapshot.
- Review states are `submitted`, `under_review`, `approved`, `changes_requested`, `rejected`, and `superseded`.
- A reviewer/admin records an outcome and explanation. Only `approved` snapshots are used by the shared Atlas and overlap checks.
- Changes to a saved plot do not alter its previously approved Atlas geometry.
- Approving a newer revision supersedes older approved and pending snapshots for that plot. Dashboard pending counts and review queues therefore stay consistent.
- Final rejections/changes requests require the owner to edit/save and submit a new plot revision.
- Submission and review writes guard revisions; stale edits/reviews return `409`.
- Overlap checks measure intersection area using a local equal-area projection, returning square metres, percentage, and intersection GeoJSON. They flag an issue for review instead of automatically rejecting it.
- Atlas colors are stable per owner. Other users do not receive private phone numbers, usernames or owner IDs. Chosen labels/community names are shown only when permission was captured at submission and is still enabled in the current profile. Revoking permission anonymizes existing labels immediately. Staff retain the review context.

Platform approval is not a legal grant, official cadastral survey, or issued FRA title. Existing native FRA cases/titles are not fabricated from personal map submissions.

## Staff workspace

The original FRA workspace now links to **Community services**. It contains:

1. Plot review queue, selected boundary map, overlap findings, review outcome/reason.
2. Ticket list, full conversation, protected document download/upload, reply and status update.
3. Publication creation/editing: announcements or schemes, draft/published/archived state, optional start/end dates, source links, synthetic marker.
4. Resource observation creation and verification, attached to a selected owner's plot.

Staff role is required on every `/api/staff/portal/*` endpoint. A user cannot grant themself reviewer/admin access. Use the existing Staff demo for local review, or provision an individual staff account:

```powershell
docker compose exec api python -m scripts.create_user reviewer-one --display-name "Review Officer" --role reviewer
docker compose exec api python -m scripts.set_account_password reviewer-one
```

The password command prompts interactively. `reviewer`/`admin` account login opens the existing FRA workspace; ordinary account login opens the user dashboard. Public registration always creates role `user`.

## Announcements, schemes and resources

All users can browse staff-published announcements and scheme listings. `district` is a displayed programme-location label, not a visibility filter or automated eligibility rule. Upcoming items remain visible with their start date; expired and archived items are hidden. Non-synthetic published schemes require an HTTPS source link. Changes appear on the next read, on manual refresh, or within the active-tab 30-second refresh interval.

Resources are staff-recorded, source-attributed observations, not invented segmentation results. Only `verified` observations are shown to their owner. Unverified/rejected observations remain in the staff workspace. No observations means **Not assessed**. A source and synthetic marker are preserved.

Scheme assistance is a ticket request, not an official government application or sanction. Portal publications do not replace the existing governed DSS catalogue/rules; that connection can be added separately.

## Tickets and attachments

- Requests and conversations are visible only to their owner and reviewer/admin staff.
- User replies reopen a request. Staff replies can set `open`, `in_progress`, `awaiting_user`, `resolved`, or `closed`.
- Ticket revision checks prevent stale replies/uploads from overwriting newer activity.
- Attachments use private local/S3 storage, actual file-type validation, SHA-256 duplicate checks, and existing ClamAV controls.
- Maximum 10 MB per file, ten files per ticket. Supported: PDF, JPG/JPEG, PNG, TIFF, BMP.
- Downloads authenticate and authorize the ticket owner/staff, record an audit event, and return an attachment without exposing storage keys.
- Production scanning fails closed if unavailable. Integration tests replace the scanner boundary; they do not claim to exercise a live ClamAV daemon.
- Replies are delivered inside the application and dashboard; no email/SMS is sent.

## Repeatable synthetic workflow data

Fixture: `data/synthetic_community_portal.json`.

Run from the project directory after containers are healthy:

```powershell
docker compose exec api python -m scripts.seed_community_portal --confirm-synthetic
```

The command asks for a password of 10–128 characters for the synthetic accounts. Reuse that same password on later runs. Account names, plots, publications and observations explicitly say **Synthetic**. Existing mismatching accounts are not overwritten.

Synthetic usernames:

- `synthetic-portal-alice`
- `synthetic-portal-bob`
- `synthetic-portal-charlie`
- `synthetic-portal-reviewer`

Use separate browser profiles/private windows to compare user and reviewer sessions. Signing in as another account in the same browser cookie jar replaces that session.

| Expected result | Alice | Bob | Charlie |
|---|---|---|---|
| Saved plots | 1 | 1 | 1 |
| Approved plots | 1 | 1 | 0 |
| Pending reviews | 0 | 0 | 1 |
| Shared Atlas features | 2 | 2 | 2 |
| Staff-verified resources | 1 | 1 | 0 |
| Own ticket with staff response | 1 | 1 | 1 |
| Published example scheme/announcement | Visible | Visible | Visible |

Charlie's pending boundary overlaps Alice's approved boundary by approximately 50%; it does not appear in the shared Atlas until approved. The seed records no legal right or official scheme entitlement.

The runner provisions only its explicitly named synthetic accounts through the database; plot/profile/submission/review/resource/publication/ticket operations use the same HTTP API endpoints as the UI. Re-running the unchanged fixture is tested not to duplicate plots, reviews, resources, publications, or tickets. It emits a JSON summary of created/reused records and dashboard counts.

To change synthetic scenarios later, edit the fixture or create a separate fixture and run:

```powershell
docker compose exec api python -m scripts.seed_community_portal --confirm-synthetic --fixture data/your_fixture.json
```

Keep `synthetic: true` and `synthetic-portal-` username prefixes. Existing fixture plots are reused rather than overwritten; use new plot/user names for a new scenario.

## API contracts and verification

`docs/COMMUNITY_PORTAL_OPENAPI.json` is the exported portal/auth OpenAPI definition. Runtime `/openapi.json` remains the source of truth. Collection responses and community read models identify `community-portal-v1`. Coordinates remain `[longitude, latitude]`, WGS84; persisted plot geometry is a closed MultiPolygon. Inputs reject unknown fields for the new community operations. Errors use the application error response; stale revisions return 409, unauthorized roles 403, inaccessible objects 404, and missing identity 401.

Cross-user responses are computed from the current database through shared serializers/read models, with private API responses marked `Cache-Control: no-store`. There is no per-user hardcoded sample dashboard. The browser refreshes current records after mutations and polls active workspaces every 30 seconds. This is polling, not WebSocket push.

The tests exercise signup/profile → plot → submission → review → Atlas → overlap → dashboard; publication lifecycle → multiple dashboards; owner-specific resource verification; ticket → private attachment → staff reply → owner dashboard; stale revisions; consent revocation; and replaying the synthetic fixture twice. A contract test checks that frontend API path literals correspond to registered OpenAPI paths.

Verified baseline: **488 Python tests passed, 16 skipped**, plus **82 JavaScript tests passed**. Skips include checks requiring unavailable external/native services. The seed replay and cross-user workflows are included in the passing tests.

Local verification commands (after installing `requirements-dev.txt`):

```powershell
$env:ENVIRONMENT = "development"
$env:MALWARE_SCAN_REQUIRED = "false"
$env:CLAMAV_HOST = ""
python -m pytest tests -q
node --test tests/*.test.js
```

Those environment overrides are for isolated local tests, not production deployment.

Native PostgreSQL/PostGIS checks require the repository's existing isolated database test setup. No live Docker, PostGIS, ClamAV or external Sentinel-2 service was available during this implementation. Browser rendering remains unverified because Chromium could not create its required socket in this environment. DOM references and JavaScript syntax/tests were checked. Satellite inference is still a future integration; plots retain their owner, UUID and revision for that work.

## Code ownership map

| File | Responsibility |
|---|---|
| `app/db/community_models.py` | Profiles, submission snapshots, publications, resources, tickets/messages/attachments |
| `app/models/community_schemas.py` | Validated API request contracts and response envelopes |
| `app/services/community_portal.py` | Shared dashboard/Atlas serializers, visibility rules, overlap measurements |
| `app/api/community_routes.py` | User and reviewer/admin endpoints and transactional state changes |
| `app/static/user/portal.js` | Dashboard/navigation/profile/Atlas/schemes/tickets and refresh handling |
| `app/static/user/app.js` | Existing plot drawing/editing, integrated with portal navigation |
| `app/static/user/index.html`, `styles.css` | Portal sections and shared staff visual system |
| `app/static/staff-services/index.html`, `app.js` | Staff review, correspondence, publishing and resource controls |
| `app/static/fra/index.html` | Link from original staff workspace to Community services |
| `scripts/migrate.py`, `app/services/portal_schema.py` | Application-targeted migration and schema readiness verification |
| `migrations/versions/20261112_0015_community_portal.py` | Frozen v2 database schema |
| `scripts/seed_community_portal.py`, `data/synthetic_community_portal.json` | Repeatable synthetic workflow scenarios |
| `tests/test_community_portal.py`, `test_community_contracts.py` | Cross-user integration, permissions, seed replay, API wiring |

## Endpoint directory

The following table is generated from the application's registered OpenAPI routes.

| Method | Endpoint | Operation |
|---|---|---|
| `POST` | `/api/auth/demo-login` | Demo Login |
| `GET` | `/api/auth/session` | Current Session |
| `POST` | `/api/auth/logout` | Logout |
| `POST` | `/api/auth/register` | Register |
| `POST` | `/api/auth/login` | Login |
| `GET` | `/api/user/plots` | List Plots |
| `POST` | `/api/user/plots` | Create Plot |
| `GET` | `/api/user/plots/{plot_id}` | Get Plot |
| `PUT` | `/api/user/plots/{plot_id}` | Update Plot |
| `GET` | `/api/user/dashboard` | User Dashboard |
| `GET` | `/api/user/profile` | Get Profile |
| `PUT` | `/api/user/profile` | Save Profile |
| `GET` | `/api/user/atlas` | User Atlas |
| `POST` | `/api/user/overlap-check` | Overlap Check |
| `GET` | `/api/user/submissions` | My Submissions |
| `POST` | `/api/user/plots/{plot_id}/submit` | Submit Plot |
| `GET` | `/api/user/publications` | User Publications |
| `GET` | `/api/user/resources` | My Resources |
| `POST` | `/api/user/tickets` | Create Ticket |
| `GET` | `/api/user/tickets` | My Tickets |
| `GET` | `/api/user/tickets/{ticket_id}` | My Ticket |
| `POST` | `/api/user/tickets/{ticket_id}/messages` | User Reply |
| `POST` | `/api/community/tickets/{ticket_id}/attachments` | Attach File |
| `GET` | `/api/community/tickets/{ticket_id}/attachments/{attachment_id}` | Download Attachment |
| `GET` | `/api/staff/portal/submissions` | Staff Submissions |
| `POST` | `/api/staff/portal/submissions/{submission_id}/review` | Review Submission |
| `GET` | `/api/staff/portal/tickets` | Staff Tickets |
| `GET` | `/api/staff/portal/tickets/{ticket_id}` | Staff Ticket |
| `POST` | `/api/staff/portal/tickets/{ticket_id}/messages` | Staff Reply |
| `GET` | `/api/staff/portal/publications` | Staff Publications |
| `POST` | `/api/staff/portal/publications` | Create Publication |
| `PUT` | `/api/staff/portal/publications/{publication_id}` | Edit Publication |
| `GET` | `/api/staff/portal/plots` | Staff Plots |
| `GET` | `/api/staff/portal/resources` | Staff Resources |
| `POST` | `/api/staff/portal/resources` | Create Resource |
| `PATCH` | `/api/staff/portal/resources/{resource_id}` | Review Resource |
| `GET` | `/api/staff/portal/submissions/{submission_id}/overlaps` | Staff Submission Overlaps |