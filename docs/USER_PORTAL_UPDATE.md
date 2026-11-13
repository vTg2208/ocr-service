# Superseded by community portal v2

Read [COMMUNITY_PORTAL_V2.md](COMMUNITY_PORTAL_V2.md) for the current installation, dashboard, review workflow, API contracts, and synthetic data instructions. The text below documents the initial plot-only implementation.

# User accounts and personal plot workspace

This update adds a personal plot workspace to the existing AranyaSetu application. It reuses the staff visual system: green navigation, paper backgrounds, Public Sans, Literata headings, and existing button styles.

## Apply this update to your running project

1. Make a backup of your existing project and database.
2. Extract this update ZIP into your existing `ocr-service` directory. Merge the `app`, `migrations`, `scripts`, `tests`, and `docs` directories; replace the included files. This is an update package, not a standalone repository. It does not contain or replace your `.env`.
3. Rebuild the application services:

```bash
docker compose up -d --build api worker
docker compose ps
docker compose logs --tail=100 api worker
```

The API startup runs `alembic upgrade head`, including the new migration `20261112_0014`. Wait for the API to become healthy.

4. Open `http://localhost:8000/login`, choose **Create account**, and enter your name, a username, and a password of 10–128 characters.
5. The new account opens `/my-plots`. Staff and administrators use the same **Sign in** form after password provisioning. The existing staff demo remains available in its own tab.

If your `.env` uses `ENVIRONMENT=production`, cookies require HTTPS. Use `ENVIRONMENT=development` for local HTTP testing; use HTTPS in production. Existing demo login behavior uses this same rule.

## User journey

1. Find the plot by moving/zooming the map, allowing browser location, or entering latitude/longitude.
2. Optionally switch between street and satellite basemaps. The satellite basemap is only visual context; it is not a Sentinel-2 analysis artifact.
3. Select **Start marking corners**, then click each corner in order.
4. Select **Close boundary**, or click the first marker after at least three corners.
5. Review the highlighted closed region, estimated area, and coordinates. Drag numbered corners to refine it; use **Undo corner** to reopen and extend the boundary.
6. Name the plot, optionally add its locality, and select **Save my plot**.
7. Open **My saved plots** to return to saved boundaries. Reopen a plot to edit its details/boundary or download its GeoJSON.

Users can save multiple plots. Unsaved changes trigger warnings before leaving, starting another plot, or logging out. Failed saves preserve the current drawing. Concurrent edits are rejected when the saved revision has changed.

## Accounts and staff provisioning

Public registration always creates role `user`. It never accepts a role selected by the browser. Login looks up the role stored in the database. Passwords use individually salted scrypt hashes; browser sessions use HttpOnly, SameSite=Strict cookies.

To create individual staff/admin accounts, run:

```bash
docker compose exec api python -m scripts.create_user reviewer-one --display-name "Review Officer" --role reviewer
docker compose exec api python -m scripts.set_account_password reviewer-one

docker compose exec api python -m scripts.create_user admin-one --display-name "Administrator" --role admin
docker compose exec api python -m scripts.set_account_password admin-one
```

The password command asks interactively and does not print the password. Usernames must be lowercase, 3–80 characters, starting with a letter or number and using letters, numbers, underscores, dots, or hyphens. Password provisioning preserves the account's current role. Do not use `registry-demo` for an individual account: the original demo-login endpoint resets that special account to reviewer.

Sign in with the provisioned username/password. `user` opens `/my-plots`; `reviewer` and `admin` open the existing `/fra` workspace. Ordinary users visiting `/fra` are redirected to `/my-plots`. This update does not add new admin management screens.

## Stored data and ownership

- `users`: existing individual identity, display name, role, creation time.
- `account_credentials`: one salted password hash linked to `users.id`.
- `user_plots`: UUID, owner foreign key, name, locality, WGS84 MultiPolygon, estimated area in square metres, revision, creation/update timestamps.
- `audit_events`: plot creation/update events with actor and revision.

The drawn ring is always closed on the server. GeoJSON coordinate order is `[longitude, latitude]`, not `[latitude, longitude]`. PostgreSQL stores a native `geometry(MultiPolygon,4326)` with a GIST spatial index. SQLite development storage uses JSON.

The server rejects crossed/degenerate boundaries, duplicate corners, invalid/non-finite coordinates, more than 500 submitted points, date-line-spanning plots, and areas outside 1 m²–100 km². The browser allows up to 499 distinct corners and provides immediate boundary feedback. Area is a spherical estimate, not a survey measurement.

All plot reads/updates are scoped to the authenticated owner, including requests made by staff/admin accounts through these personal endpoints. A client cannot assign another owner. Updates require `expected_revision` and use an atomic database comparison. The latest boundary is persisted; earlier geometry revisions are not retained as a history table.

## API

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/auth/register` | Create a user account and browser session |
| POST | `/api/auth/login` | Authenticate an existing individual account |
| GET | `/api/user/plots` | List the signed-in account's plots |
| POST | `/api/user/plots` | Save a new owner-linked closed boundary |
| GET | `/api/user/plots/{id}` | Read an owned plot |
| PUT | `/api/user/plots/{id}` | Update an owned plot with revision protection |

Example plot payload:

```json
{
  "name": "North field",
  "village": "Salem",
  "coordinates": [[78.14, 11.66], [78.141, 11.66], [78.141, 11.661], [78.14, 11.661]]
}
```

An update also includes `"expected_revision": 1` (use the actual revision returned by the API).

## Files to change next

| File | Role |
|---|---|
| `app/static/user/index.html` | Personal workspace markup and controls |
| `app/static/user/styles.css` | User layouts and responsive styling on top of staff CSS |
| `app/static/user/app.js` | Map drawing, validation, saved list, edits, export |
| `app/static/login/index.html`, `app.js`, `styles.css` | Individual login, registration, staff demo |
| `app/api/user_portal_routes.py` | Account endpoints, owner-scoped plot APIs, geometry validation |
| `app/services/account_passwords.py` | Password hashing/verification |
| `app/db/user_plot_models.py` | Credential and plot database models |
| `migrations/versions/20261112_0014_user_plots.py` | New schema and indexes |
| `app/db/__init__.py`, `migrations/env.py` | Register models for creation/schema checks |
| `app/main.py` | Register APIs, serve `/my-plots`, route users to their workspace |
| `app/middleware.py` | Extend existing rate limiting to account and personal-plot APIs |
| `scripts/create_user.py` | Allow reviewer account provisioning |
| `scripts/set_account_password.py` | Set/reset an existing account password interactively |
| `tests/test_user_portal.py` | Login, ownership, boundary validation, revisions, page routing |
| `tests/user_plot_ui.test.js`, `tests/login_ui.test.js` | Boundary helpers and login route behavior |
| `tests/test_migrations.py` | Updated migration-head/schema assertions |

## Satellite integration boundary

Saving a plot does not create a legal FRA claim or request imagery automatically. The response explicitly reports `imagery_status: not_requested` and `model_status: not_connected`.

The existing satellite-ingestion service accepts an `FRAClaim`, so a later update must add a plot-specific job target or generalize that service. Use the saved plot ID, revision, WGS84 geometry, band order/preprocessing definition, requested dates, and model version as the provenance contract. Do not fabricate an FRA claim just to reuse the imagery endpoint. Segmentation weights, inference, prediction display, and continuous monitoring are not added in this update.

## Verification and limitations

- New account/plot integration tests cover registration, role assignment, login, owner isolation, closed persistence, invalid boundaries, concurrent-edit conflicts, redirects, and anonymous denial.
- Migration tests cover fresh schema, upgrades, downgrade/rebuild, and ORM drift on SQLite.
- JavaScript suite includes updated login tests and boundary helper tests.
- Existing demo authentication remains covered by its regression tests.
- Chromium could not start in the execution environment because socket creation was blocked. Desktop/mobile rendering and live map interactions therefore still need checking in your browser.
- Docker/PostGIS were not available here; run the container flow locally. No trained-model accuracy or live satellite acquisition was tested.
- Mapping requires internet access for Leaflet, fonts, and tile providers. Location permission requires localhost or HTTPS.
- Registration currently uses username/password, without email verification, password-recovery UI, or multi-factor authentication. An operator can reset passwords using the provided CLI. Existing rate limiting is process-local.
