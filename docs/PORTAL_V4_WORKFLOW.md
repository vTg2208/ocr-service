# User ↔ registry staff workflow update

Merge every folder in the cumulative update ZIP into your existing `ocr-service` project, replacing matching files. Keep your `.env` and database volumes.

```powershell
docker compose up -d --build api worker
docker compose exec api python -m scripts.migrate
```

The schema head remains `20261112_0016`: this update reuses the existing plot, submission, ticket, message and attachment tables. Hard-refresh the pages with Ctrl+F5.

## Remove the old portal examples

Nothing seeds example data at startup. The screens read real API/database records. If you previously ran the seed script, those rows remain until removed. Run:

```powershell
docker compose exec api python -m scripts.clear_portal_examples
docker compose exec api python -m scripts.clear_portal_examples --apply
```

The first command previews counts; the second deletes portal plots, submissions, tickets/messages/documents belonging to the exact fixture accounts `synthetic-portal-alice`, `synthetic-portal-bob`, `synthetic-portal-charlie`, plus publications/resource observations explicitly marked synthetic. Account identities, profiles and audit history remain. Real-user records are retained. Cleanup refuses to proceed if a real record references an example plot or scheme. It is safe to rerun. It does not touch unrelated FRA demo/catalog data or reset the database. Do not rerun the seed script for your manual live test.

## Use separate sessions

Use a normal browser window for the user and an Incognito window for registry staff, or use two different browser profiles/browsers. Two normal windows share the same login cookie; logging into the second account would replace the first session.

- User: sign up at `http://localhost:8000/login`. Public signup always creates a user.
- Staff: use your existing staff login, then open **Community services** at `http://localhost:8000/staff-services`.
- Admin: the same Community services page and protected actions are available to stored admin accounts. No separate admin portal has been introduced.
- If you need a new individual reviewer account, create it and set its password:

```powershell
docker compose exec api python -m scripts.create_user workflow-reviewer --display-name "Workflow Reviewer" --role reviewer
docker compose exec api python -m scripts.set_account_password workflow-reviewer
```

## Test A: user draws the boundary

1. Sign up and log in as a new user. Profile completion is optional. To show your chosen name/community in everyone's Atlas, save an Atlas display label and enable its sharing checkbox in My profile.
2. Choose Map a new plot. Click corners, close the boundary, name it, check overlaps, and save.
3. Click Submit for staff review. My plots shows **Mapped → Awaiting review → Approved → Analysis pending**. Only the steps actually reached are marked complete.
4. As staff, open Community services → Plot reviews. Select the user’s boundary, inspect its location and overlap result, choose Approve for Atlas, enter a review note and record the review.
5. Refresh the user workspace. My plots, My profile and dashboard updates show approval and the review note. The community Atlas shows the approved boundary for this user and other users. Staff have a Community Atlas tab showing the same approved boundaries.
6. Make an edit to an approved boundary. The approved snapshot remains in the Atlas while the changed draft/revision is private until approved. My plots explicitly identifies which revision is currently public.
7. Test Under review, Request changes and Reject as well. Review outcomes persist, and changed boundaries can be saved and resubmitted as a new revision.

## Test B: user uploads a document instead

1. Choose Upload plot document (also available in My profile). Enter a request name and location details, attach a PDF/image, and submit.
2. The document, private message and mapping request save atomically. An invalid upload creates no empty request. The user is taken to its conversation in Help & tickets. No coordinates are required from the user.
3. As staff, open Help requests. Document mapping requests are labelled and show the requester's name. Open one and download/read its private evidence.
4. Use Map this user's plot: locate it with latitude/longitude or map navigation, click corners, close the boundary, enter name/locality, then Create plot & submit for review.
5. The plot belongs to the requester, appears in their My plots, and enters Plot reviews. Creating it does not publish it or automatically approve it. Duplicate/stale attempts are rejected.
6. Approve it in Plot reviews. The boundary appears in the shared Atlas; review feedback also reaches the document-request conversation. The original document remains linked to that request and plot.

## Test C: correspondence and dashboard data

1. Create a Help & tickets request as the user; optionally relate it to an owned plot or published scheme and attach documents.
2. Staff open the request, reply and select the status. The user sees the reply in both the conversation and dashboard updates.
3. Reply as the user. Staff see it in the same conversation. Tickets and downloads are private to their owner and authorized staff.
4. Publish a real announcement or scheme from staff Community services. Published records appear on user dashboards; schemes require an HTTPS source. Draft/archived/expired publications remain hidden from users.
5. Record a real resource observation against the correct plot and verify it. It appears only on that owner's dashboard; it is labelled with its entered source. This does not pretend that segmentation ran.
6. Use Refresh, refocus the window, or wait up to 30 seconds for the active page's periodic refresh. This is API polling, not WebSocket delivery.

## Segmentation boundary

The repository's asset worker currently uses `ManifestAssetDetector`, which replays explicitly synthetic detections. There is no real satellite-image fetch/segmentation runner connected to personal plots in this source tree. This update deliberately does not enqueue fabricated inference results. The final timeline stage remains **Analysis pending**, with that reason visible after approval. Actual **Analysing** / **Complete** states must be driven by the future real worker, not by approval or a staff toggle.

## Files and database mapping

| File | Responsibility |
|---|---|
| `app/api/community_routes.py` | Atomic document request, authorized staff mapping, review feedback and existing private correspondence |
| `app/services/community_portal.py` | Dashboard/profile/Atlas read models, per-plot timeline derived from persisted revisions |
| `app/models/community_schemas.py` | Document mapping category and dashboard workflow response contract |
| `app/static/user/index.html`, `portal.js`, `styles.css` | Registry icon reuse, upload form, review timeline, profile status and ticket navigation |
| `app/static/staff-services/index.html`, `app.js`, `maps.js` | Staff/admin document mapping, review queue, private replies and shared Atlas |
| `scripts/clear_portal_examples.py` | Targeted old fixture cleanup; preview by default |
| `tests/test_portal_live_workflow.py`, `tests/user_interactions.test.js` | Independent-session workflow and frontend interaction checks |

| Stored record | Tables |
|---|---|
| Document mapping request and conversation | `support_tickets`, `ticket_messages` |
| Private document metadata; file bytes in configured private storage | `ticket_attachments` |
| User/staff-drawn boundary, actual owner, closed coordinates and area | `user_plots` |
| Submitted geometry snapshot, revision, staff outcome and note | `plot_submissions` |
| Contact/profile and sharing preference | `community_profiles` |
| Announcements/schemes and verified observations | `portal_publications`, `user_resources` |
| Creation, review and document access events | `audit_events` |

New endpoints: `POST /api/user/plot-requests` (multipart subject/message/file) and `POST /api/staff/portal/tickets/{ticket_id}/plot` (name/village/coordinates/expected_revision). The latter revision is the ticket revision, not a plot revision. Existing review and ticket APIs remain compatible. See `COMMUNITY_PORTAL_OPENAPI.json` for the full API contract.

## Validation of this update

Full backend suite: 498 passed, 15 skipped, 130 subtests passed. JavaScript suite: 89 passed. New API tests exercise independent user/reviewer/admin sessions, atomic failed uploads, private document access, ownership, duplicate mapping, approval propagation, feedback, revised boundary visibility, and targeted fixture cleanup. New workflow tests also enable SQLite foreign-key enforcement.

Live Docker/PostGIS/ClamAV execution was not available here. File-scanning tests stub the scanner; production code retains the scanner checks. Chromium could not start because the execution environment disallows its required socket, so visual browser validation remains for your local test.
