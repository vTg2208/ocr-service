Latest workflow update: see [PORTAL_V4_WORKFLOW.md](PORTAL_V4_WORKFLOW.md).

# User portal fixes: drawing, logout, completed profile and contact OTP

## Install

Download the latest cumulative update ZIP and merge **all** included files and folders into your existing `ocr-service` project. Keep `.env` and your data volumes. This update includes the new `migrations/versions/20261112_0016_contact_verification.py` file; copying only the UI files is insufficient.

```powershell
docker compose up -d --build api worker
docker compose exec api python -m scripts.migrate
docker compose exec api alembic current
```

Expected migration revision: `20261112_0016`. Refresh the browser with Ctrl+F5. No reset, re-seeding, or volume deletion is needed.

## Drawing a plot

Open Profile → Map my plot, or Map a new plot. Marking is active immediately. Move/zoom or fetch your location, then click each corner in order. After the third corner a translucent polygon previews automatically. Add more corners, then click the first marker or **Close boundary** to review and save. **Pause marking** lets you move around without adding points; **Resume marking** re-enables clicks. Use **Undo corner** to remove the last point. Once closed, corners can be dragged before saving.

The preview polygon does not intercept subsequent map clicks. Markers are draggable only during closed-boundary editing. Repeated identical/double-click points are ignored. Draft storage, staff submission/review, and approved snapshots retain their existing behavior.

## Logout

The personal portal, original FRA staff/admin workspace, Community services and Cadastral evidence now include the same independent logout module. Logout calls the existing cookie-clearing endpoint and returns to the login page. It does not depend on Leaflet or successful map initialization. Unsaved plot changes still require a discard confirmation.

## Completed-profile prompt

The entire Dashboard completion panel is hidden when the server returns completion of 100%. It appears again if completion falls below 100%. The completion rule remains name, locality, livelihood and a mapped plot. Optional contacts do not force onboarding or change completion criteria.

## Email and phone OTP (development)

Profile now includes **Email / Gmail** alongside phone. Other valid email providers are accepted too. Both contacts support the development OTP workflow:

1. Save the profile with the email/phone you want to verify.
2. In another PowerShell terminal, watch the API log:

```powershell
docker compose logs -f api
```

3. Choose **Get email code** or **Get phone code**.
4. The log prints a line beginning `DEV OTP` with the account, channel, target and six-digit code.
5. Enter that code in the profile verification form.
6. The database records the verification timestamp and `development_terminal` verification method. Reloading the page/session preserves the badge.

Codes expire after five minutes, allow five incorrect attempts, are single use, and have a 60-second resend cooldown. Issuing another code invalidates older outstanding codes for that channel. Changing a saved email/phone clears its previous verification and invalidates pending challenges. Codes are bound to the signed-in user, channel and saved contact. Code digests, not plaintext codes, are stored in the database. Codes are never returned in API responses or exposed in the public Atlas.

This is a development simulation: no email/SMS is sent and terminal access does not prove possession of the actual inbox/phone. The UI therefore labels it **Verified (development)**. Terminal code issuance/verification is available only with `ENVIRONMENT=development`; production requires a real delivery adapter.

Email/phone verification is a profile feature, not a new requirement for password login. Existing accounts can still sign in.

## New endpoints

| Method | Path | Input |
|---|---|---|
| POST | `/api/user/profile/otp/request` | `{"channel":"email"}` or `{"channel":"phone"}` |
| POST | `/api/user/profile/otp/verify` | `{"channel":"email","challenge_id":"returned-uuid","code":"123456"}` |

The existing profile PUT accepts optional `email`; GET/profile and dashboard/profile return email, verification flags/timestamps/methods. Profile saves still require `expected_revision`. Successful verification advances the profile revision so older-tab saves return a conflict rather than overwriting it.

## Database changes

| Table | Stored state |
|---|---|
| `community_profiles` | Email, email/phone verification timestamps and verification methods, existing contacts/details/revision |
| `contact_otps` | Owner, channel, contact digest, challenge-specific code digest, creation/expiry, attempts, consumed time |
| `user_plots` | Existing owner-linked closed WGS84 boundary, name, locality, area and revision |
| `audit_events` | Existing profile/plot updates and contact-verified events |

The migration supplies an empty email for existing profiles and preserves their existing details and plots. Existing profiles/plots do not need recreation.

## Files to update later

| File | Role |
|---|---|
| `app/static/user/app.js` | Click-to-corner drawing, preview, closing and saving |
| `app/static/shared/session.js` | Logout across all protected workspaces |
| `app/static/user/portal.js` | Completion visibility and OTP UI |
| `app/static/user/index.html` | Email and verification controls |
| `app/services/contact_otp.py` | Challenge generation, expiry/retry/ownership checks, development delivery |
| `app/models/community_schemas.py` | Email/OTP request validation |
| `app/api/community_routes.py` | Contact API wiring and invalidation on profile change |
| `app/db/community_models.py` | Persisted contact/challenge models |
| `migrations/versions/20261112_0016_contact_verification.py` | Upgrade existing profiles without deleting data |
| `tests/user_interactions.test.js` | Click sequence → polygon → save, completion visibility, independent logout |
| `tests/test_contact_otp.py` | Contact persistence, expiry, attempts, ownership, replay/invalidation and populated-schema upgrade |

Validation uses isolated databases and browser event mocks. Native PostGIS/Docker and real browser rendering remain local verification steps; no actual email/SMS delivery was tested.

Verified baseline: 493 Python tests passed (16 service-dependent skips), and 86 JavaScript tests passed. The contact-upgrade tests also passed after the final verification-method fields were added.
