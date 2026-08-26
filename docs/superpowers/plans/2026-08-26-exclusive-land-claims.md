# Exclusive Land Claims Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add demonstration login, prevent duplicate or overlapping accepted claims, persist claimed polygons, and let authenticated registry staff open the patta linked to a claimed polygon.

**Architecture:** Keep the existing JWT verifier as the authentication adapter, add a server-issued HttpOnly cookie for the browser, and retain bearer-token compatibility. Enforce claim exclusivity inside the claim transaction with a PostgreSQL advisory lock plus an exact-parcel database constraint, expose privacy-safe registry and protected document endpoints, and add a separate claimed-land application view backed entirely by database state.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, PostgreSQL/PostGIS, SQLite test database, PyJWT, Leaflet, static HTML/CSS/JavaScript, Python `unittest`/pytest, Node test runner, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-26-exclusive-land-claims-design.md`

## Global Constraints

- The development demonstration access code is exactly `1234` and is validated only on the server.
- Browser authentication uses an `HttpOnly`, `SameSite=Strict` signed cookie; browser JavaScript never receives the token.
- Existing `Authorization: Bearer <JWT>` API clients remain supported.
- A claim covers the complete stored registry parcel geometry; partial claims are outside scope.
- Exact duplicate and material spatial-overlap attempts create no second claim.
- Claimed-land and patta responses exclude claimant identifiers and private storage keys.
- Patta responses use `Cache-Control: private, no-store` and `X-Content-Type-Options: nosniff`.
- All accepted and rejected claim decisions and successful patta views are audited.
- No claim state is stored in browser local storage.
- Preserve existing user changes in the dirty worktree and commit only files belonging to the task at hand.

---

### Task 1: Demo Session Authentication API

**Files:**
- Create: `app/api/session_routes.py`
- Modify: `app/api/auth.py`
- Modify: `app/config.py`
- Modify: `app/main.py`
- Create: `tests/test_demo_auth.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `create_access_token(external_id: str, *, minutes: int) -> str` in `app.api.auth`.
- Produces: `POST /api/auth/demo-login`, `GET /api/auth/session`, and `POST /api/auth/logout`.
- Produces: browser cookie named `parcel_registry_session`.
- Preserves: `get_current_user()` bearer-token behavior used by all existing `/api` routes.

- [ ] **Step 1: Write failing authentication tests**

Create real FastAPI integration tests that override `get_db`, configure a temporary SQLite database, and assert the observable session contract:

```python
def test_demo_login_sets_http_only_cookie_and_reports_session(self):
    login = self.client.post("/api/auth/demo-login", json={"access_code": "1234"})
    self.assertEqual(login.status_code, 200)
    self.assertIn("parcel_registry_session=", login.headers["set-cookie"])
    self.assertIn("HttpOnly", login.headers["set-cookie"])
    self.assertIn("SameSite=strict", login.headers["set-cookie"])
    session = self.client.get("/api/auth/session")
    self.assertEqual(session.json()["external_id"], "registry-demo")

def test_demo_login_rejects_wrong_code_without_cookie(self):
    response = self.client.post("/api/auth/demo-login", json={"access_code": "9999"})
    self.assertEqual(response.status_code, 401)
    self.assertNotIn("parcel_registry_session=", response.headers.get("set-cookie", ""))

def test_logout_clears_session(self):
    self.client.post("/api/auth/demo-login", json={"access_code": "1234"})
    logout = self.client.post("/api/auth/logout")
    self.assertEqual(logout.status_code, 204)
    self.assertEqual(self.client.get("/api/auth/session").status_code, 401)
```

- [ ] **Step 2: Run the new tests and confirm the missing routes fail**

Run: `python -m pytest tests/test_demo_auth.py -q`

Expected: FAIL with `404` responses for `/api/auth/demo-login` and `/api/auth/session`.

- [ ] **Step 3: Add explicit demo-auth settings**

Add these settings without changing production defaults elsewhere:

```python
demo_auth_enabled: bool = True
demo_access_code: str = "1234"
demo_session_minutes: int = 480
```

Add `DEMO_AUTH_ENABLED`, `DEMO_ACCESS_CODE`, and `DEMO_SESSION_MINUTES` to `.env.example`. Extend the production safeguard so production rejects `demo_auth_enabled=True` unless deployment explicitly opts into the demonstration risk documented in the spec.

- [ ] **Step 4: Implement cookie-aware authentication**

Extract token creation and allow either bearer header or cookie:

```python
SESSION_COOKIE = "parcel_registry_session"

def create_access_token(external_id: str, *, minutes: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": external_id,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
        "iss": settings.auth_issuer,
        "aud": settings.auth_audience,
    }, settings.auth_secret, algorithm="HS256")

def get_current_user(
    authorization: str | None = Header(None),
    session_token: str | None = Cookie(None, alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
) -> AuthenticatedUser:
    token = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else session_token
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    # Existing decode and database lookup remain unchanged.
```

- [ ] **Step 5: Implement the demo session router**

Use a Pydantic request model with `access_code: str`, compare using `secrets.compare_digest`, create or reuse `User(external_id="registry-demo", display_name="Registry staff", role="user")`, and set the cookie on a `JSONResponse`. Set `secure=True` only in production, `httponly=True`, `samesite="strict"`, and `max_age=settings.demo_session_minutes * 60`. Logout deletes the same cookie.

- [ ] **Step 6: Register the router and run authentication regression tests**

Run:

```powershell
python -m pytest tests/test_demo_auth.py tests/test_land_api.py::LandAPITests::test_requires_authentication tests/test_land_api.py::LandAPITests::test_rejects_tampered_authentication_token -q
```

Expected: all pass, proving cookie login and bearer compatibility.

- [ ] **Step 7: Commit the authentication API**

```powershell
git add app/api/session_routes.py app/api/auth.py app/config.py app/main.py tests/test_demo_auth.py .env.example
git commit -m "feat: add demo registry session authentication"
```

---

### Task 2: Dedicated Login Page and Browser Session Gate

**Files:**
- Create: `app/static/login/index.html`
- Create: `app/static/login/styles.css`
- Create: `app/static/login/app.js`
- Modify: `app/main.py`
- Modify: `app/static/land-mapping/index.html`
- Modify: `app/static/land-mapping/app.js`
- Modify: `app/static/land-mapping/styles.css`
- Modify: `tests/test_land_mapping_ui.py`
- Modify: `tests/land_mapping_ui.test.js`
- Modify: `tests/test_land_api.py`

**Interfaces:**
- Consumes: Task 1 session routes and cookie.
- Produces: `/login` page, `/` redirect to `/login`, `ensureBrowserSession()`, and logout behavior.
- Removes: `accessPanel`, `authToken`, `saveAccess`, and manual `Authorization` header creation from the main page.

- [ ] **Step 1: Write failing UI structure and session-helper tests**

Assert that the login page has a labeled password-style access-code input and submit button, while the main application contains no access-code input and has a staff identity plus sign-out control. Add Node tests around a dependency-injected session helper:

```javascript
test('browser session redirects to login when the session endpoint rejects access', async () => {
  let redirected = null;
  const result = await ui.ensureBrowserSession(
    async () => ({ ok: false, status: 401 }),
    (url) => { redirected = url; },
  );
  assert.equal(result, null);
  assert.equal(redirected, '/login');
});
```

- [ ] **Step 2: Run UI tests and verify the old access panel fails the contract**

Run:

```powershell
python -m pytest tests/test_land_mapping_ui.py tests/test_land_api.py::LandAPITests::test_ui_is_served_with_accessible_workflow_and_explicit_map_container -q
node --test tests/land_mapping_ui.test.js
```

Expected: FAIL because the login assets, session helper, and sign-out control do not exist and the access-code field is still present.

- [ ] **Step 3: Build the login surface using the existing design tokens**

Create a restrained institutional page with one heading, one short instruction, a four-digit input (`inputmode="numeric"`, `autocomplete="one-time-code"`, `maxlength="4"`), one primary **Continue** button, and an inline `role="status"` error. Submit JSON to `/api/auth/demo-login`; redirect only after an OK response.

- [ ] **Step 4: Replace the main-page access panel with session identity**

Use semantic markup:

```html
<div class="session-account">
  <span><small>Signed in as</small><strong id="staffName">Registry staff</strong></span>
  <button class="text-button" id="logoutButton" type="button">Sign out</button>
</div>
```

Remove all token-input CSS and JavaScript. Same-origin `fetch` calls rely on the HttpOnly cookie and continue to send `Idempotency-Key` where required.

- [ ] **Step 5: Gate application startup and implement logout**

Export `ensureBrowserSession(fetchImpl, redirect)` for Node tests. In the browser bootstrap, fetch `/api/auth/session`, render `display_name`, and only then initialize the upload workflow. On `401`, redirect to `/login`. Logout posts to `/api/auth/logout` and redirects.

- [ ] **Step 6: Run UI and route tests**

Run the commands from Step 2. Expected: all pass.

- [ ] **Step 7: Commit the login experience**

```powershell
git add app/static/login app/main.py app/static/land-mapping tests/test_land_mapping_ui.py tests/land_mapping_ui.test.js tests/test_land_api.py
git commit -m "feat: add registry staff login page"
```

---

### Task 3: Transactional Exclusive Claim Enforcement

**Files:**
- Create: `app/services/claim_eligibility.py`
- Modify: `app/services/claim_service.py`
- Modify: `app/api/patta_routes.py`
- Create: `migrations/versions/20260826_0002_exclusive_claims.py`
- Modify: `tests/test_claim_service.py`
- Modify: `tests/test_land_api.py`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_spatial_conflicts.py`

**Interfaces:**
- Produces: `ClaimUnavailableError(reason: Literal["same_parcel", "spatial_overlap"])`.
- Produces: `find_claim_blocker(session, parcel_id, *, min_sqm, min_percent) -> ClaimBlocker | None`.
- Changes: `ClaimService.submit()` raises before inserting a second claim.
- Changes: `POST /api/claims` returns privacy-safe HTTP `409` for occupied land.

- [ ] **Step 1: Write failing service tests for exact and spatial blocking**

Add tests that create a successful first claim, submit a second user's claim, and assert the second call raises `ClaimUnavailableError` while the database still contains exactly one `Claim`. Cover a different parcel whose geometry overlaps above the configured threshold.

```python
with self.assertRaisesRegex(ClaimUnavailableError, "already claimed") as raised:
    service.submit(
        claimant_id=self.user_b_id,
        document_id=self.document_b_id,
        parcel_id=self.parcel_id,
        confirmed_fields={"document_area_sqm": 1200},
        idempotency_key="claim-b",
        request_id="r2",
    )
self.assertEqual(raised.exception.reason, "same_parcel")
self.assertEqual(session.scalar(select(func.count(Claim.id))), 1)
```

- [ ] **Step 2: Write failing API and migration tests**

Assert the second API response is `409`, contains `{"success": false, "message": "This land is already claimed.", "reason": "same_parcel"}`, and creates a `claim_rejected` audit event but no second claim. Assert the `claims` table has unique constraint `uq_claim_parcel_exclusive` on `parcel_id`.

- [ ] **Step 3: Run the focused tests and verify current conflicting-claim behavior fails**

Run:

```powershell
python -m pytest tests/test_claim_service.py tests/test_spatial_conflicts.py tests/test_land_api.py tests/test_migrations.py -q
```

Expected: FAIL because a second claim is currently inserted with status `conflicting` and no parcel uniqueness constraint exists.

- [ ] **Step 4: Implement the eligibility query**

Define active legacy statuses as `{"pending", "matched", "conflicting"}`. Acquire `pg_advisory_xact_lock(0x50415243454C)` only for PostgreSQL. Check exact `parcel_id` first. For remaining active claims, use PostGIS intersection/area expressions on PostgreSQL and Shapely geometry intersection in SQLite tests. Return only the blocking reason and internal blocking claim ID; never serialize claimant identity.

- [ ] **Step 5: Enforce eligibility before claim construction**

Call `find_claim_blocker` after validating document ownership and resolution candidate, but before constructing `Claim`. If blocked, write `claim_rejected` with candidate parcel and reason, flush the audit event, and raise `ClaimUnavailableError`. Do not call the old post-insertion conflict detector for new submissions.

- [ ] **Step 6: Return an audited privacy-safe 409**

Catch `ClaimUnavailableError` in `submit_claim`, commit the audit-only transaction, and return:

```python
return JSONResponse(
    status_code=409,
    content={
        "success": False,
        "message": "This land is already claimed.",
        "reason": exc.reason,
    },
)
```

- [ ] **Step 7: Add the exact-parcel database constraint**

Create Alembic revision `20260826_0002` with `down_revision = "20260813_0001"`. Before `op.create_unique_constraint`, query grouped duplicate `parcel_id` values and raise a descriptive `RuntimeError` if any exist. The downgrade removes only `uq_claim_parcel_exclusive`.

- [ ] **Step 8: Run focused tests and verify they pass**

Run the command from Step 3. Expected: all pass, with one accepted claim and rejected duplicate/overlap attempts.

- [ ] **Step 9: Commit exclusivity enforcement**

```powershell
git add app/services/claim_eligibility.py app/services/claim_service.py app/api/patta_routes.py migrations/versions/20260826_0002_exclusive_claims.py tests/test_claim_service.py tests/test_land_api.py tests/test_migrations.py tests/test_spatial_conflicts.py
git commit -m "feat: prevent competing land claims"
```

---

### Task 4: Private Patta Read Adapter and Protected Claim Document Endpoint

**Files:**
- Modify: `app/services/storage.py`
- Create: `app/api/claim_registry_routes.py`
- Modify: `app/main.py`
- Modify: `tests/test_production_safeguards.py`
- Modify: `tests/test_land_api.py`

**Interfaces:**
- Produces: `PrivateStorage.read(key: str) -> bytes` on local and S3 adapters.
- Produces: `GET /api/claims/{claim_id}/patta`.
- Produces audit action: `claim_document_viewed`.

- [ ] **Step 1: Write failing storage round-trip tests**

Extend the existing local-storage test so `put()` followed by `read()` returns identical bytes and invalid traversal keys raise `ValueError`. Use a narrow fake S3 client configured with bucket `claims-test` and key `patta-documents/abc.png`; verify `get_object` receives those exact values and returns the fake body's bytes without asserting boto3 internals.

- [ ] **Step 2: Write failing protected-document integration tests**

Create a real claim pointing at a temporary stored PNG, then assert:

```python
anonymous = self.client.get(f"/api/claims/{claim_id}/patta")
self.assertEqual(anonymous.status_code, 401)

view = self.client.get(f"/api/claims/{claim_id}/patta", headers=self.headers())
self.assertEqual(view.content, png_bytes())
self.assertEqual(view.headers["content-type"], "image/png")
self.assertIn("inline", view.headers["content-disposition"])
self.assertEqual(view.headers["cache-control"], "private, no-store")
self.assertEqual(view.headers["x-content-type-options"], "nosniff")
```

Also assert a `claim_document_viewed` audit event exists and the endpoint works for authenticated registry staff without returning storage keys.

- [ ] **Step 3: Run focused tests and verify missing read/route failures**

Run:

```powershell
python -m pytest tests/test_production_safeguards.py tests/test_land_api.py -q
```

Expected: FAIL because storage has no `read` method and the patta endpoint is `404`.

- [ ] **Step 4: Implement safe storage reads**

For local storage, resolve `root / key`, require `root` in `target.parents`, and return `target.read_bytes()`. For S3, call `get_object`, read the body, and return bytes. Keep all storage objects private.

- [ ] **Step 5: Implement the protected patta route**

Load `Claim` and related `Document`, return privacy-safe `404` when absent, read through `create_storage(settings)`, record the view audit with the authenticated actor and request ID, commit, and return a `Response` with original content type and sanitized filename. Set the required private-cache and nosniff headers.

- [ ] **Step 6: Run focused tests and verify they pass**

Run the command from Step 3. Expected: all pass.

- [ ] **Step 7: Commit private evidence delivery**

```powershell
git add app/services/storage.py app/api/claim_registry_routes.py app/main.py tests/test_production_safeguards.py tests/test_land_api.py
git commit -m "feat: serve claimed patta evidence securely"
```

---

### Task 5: Claimed-Land Registry API and Aggregates

**Files:**
- Modify: `app/api/claim_registry_routes.py`
- Modify: `tests/test_land_api.py`

**Interfaces:**
- Produces: `GET /api/claims/registry` with `summary` and `claims`.
- Consumes: accepted legacy statuses and `parcel_public_dict()`.
- Produces per item: `claim_id`, `status`, `submitted_at`, `parcel`, and privacy-safe `document` metadata.

- [ ] **Step 1: Write a failing registry response test**

Register two non-overlapping parcels and derive expected aggregate values by hand:

```python
payload = self.client.get("/api/claims/registry", headers=self.headers()).json()
self.assertEqual(payload["summary"], {
    "claimed_parcel_count": 2,
    "claimed_official_area_sqm": 3200.0,
})
self.assertEqual(len(payload["claims"]), 2)
self.assertEqual(payload["claims"][0]["document"]["view_url"], f"/api/claims/{claim_id}/patta")
self.assertNotIn("claimant_id", str(payload))
self.assertNotIn("storage_key", str(payload))
```

Add an anonymous `401` assertion and verify geometry survives unchanged.

- [ ] **Step 2: Run the focused registry test and confirm the route is missing**

Run: `python -m pytest tests/test_land_api.py -q`

Expected: FAIL with `404` for `/api/claims/registry`.

- [ ] **Step 3: Implement the registry query and serialization**

Select accepted claims ordered by `submitted_at`, eagerly load parcel and document, sum non-null `official_area_sqm` as `Decimal`, and convert only at the response boundary. Do not return claimant or storage data.

- [ ] **Step 4: Run API tests and verify they pass**

Run: `python -m pytest tests/test_land_api.py -q`

Expected: all pass.

- [ ] **Step 5: Commit the claimed-land API**

```powershell
git add app/api/claim_registry_routes.py tests/test_land_api.py
git commit -m "feat: expose privacy-safe claimed land registry"
```

---

### Task 6: Persistent Claimed-Land Map and Evidence Interaction

**Files:**
- Create: `app/static/land-mapping/claimed-land.js`
- Modify: `app/static/land-mapping/index.html`
- Modify: `app/static/land-mapping/styles.css`
- Modify: `app/static/land-mapping/app.js`
- Modify: `tests/land_mapping_ui.test.js`
- Modify: `tests/test_land_mapping_ui.py`

**Interfaces:**
- Consumes: Task 5 `GET /api/claims/registry` payload.
- Produces: `ClaimedLandUI.registryViewModel(payload, selectedClaimId)` for deterministic UI state.
- Produces: application views `newClaimView` and `claimedLandView`.
- Produces: `claimedLand.load(selectClaimId?)` and `claimedLand.show()`.
- Consumes: claim response `claim_id` to focus the newly registered polygon.

- [ ] **Step 1: Write failing view-model and structure tests**

In Node, test literal summary and selection behavior:

```javascript
test('claimed registry view model keeps all persisted polygons and selects the requested claim', () => {
  const model = registry.registryViewModel({
    summary: {claimed_parcel_count: 2, claimed_official_area_sqm: 3200},
    claims: [{claim_id:'a'}, {claim_id:'b'}],
  }, 'b');
  assert.equal(model.summaryText, '2 claimed parcels · 3,200 m²');
  assert.equal(model.selected.claim_id, 'b');
  assert.equal(model.claims.length, 2);
});
```

In Python HTML structure tests, require the two view-navigation buttons, persistent map, empty state, detail panel, `viewPattaButton`, and post-registration `viewClaimMapButton`.

- [ ] **Step 2: Run UI tests and verify the new claimed-land surface is absent**

Run:

```powershell
node --test tests/land_mapping_ui.test.js
python -m pytest tests/test_land_mapping_ui.py -q
```

Expected: FAIL because `claimed-land.js` and the claimed-land DOM do not exist.

- [ ] **Step 3: Add explicit application view navigation**

Add two genuine navigation buttons—**New claim** and **Claimed land**—with `aria-selected` and `aria-controls`. Wrap the existing workflow in `newClaimView`. Add `claimedLandView` with a compact count/area readout, map, non-modal detail panel, and helpful empty state. Keep actions visually distinct from read-only statuses.

- [ ] **Step 4: Implement the claimed-land controller**

Fetch the registry on each entry and after registration. Initialize Leaflet only while visible, clear and rebuild layers from API claims, bind each polygon click to `selectClaim`, fit all bounds, and render selected parcel facts with `textContent`. The patta action uses `window.open(selected.document.view_url, "_blank", "noopener")`.

- [ ] **Step 5: Integrate successful registration and blocked claims**

After claim success, expose **View on claimed-land map** and call `claimedLand.load(result.claim_id)` when selected. For HTTP `409`, keep the review screen, disable confirmation and registration, render “This land is already claimed,” and provide a **View claimed land** navigation action without exposing the claimant.

- [ ] **Step 6: Add responsive and accessibility styles**

Use the existing 4-point spacing tokens, current green/earth palette, visible focus styles, and a two-column desktop map/detail layout that becomes one column below 920px. Keep map and detail accessible at 390px with no horizontal scroll. Claimed polygons use registry green; the selected polygon adds weight rather than an unrelated color.

- [ ] **Step 7: Run UI tests and verify they pass**

Run the command from Step 2. Expected: all pass.

- [ ] **Step 8: Commit the claimed-land interface**

```powershell
git add app/static/land-mapping/claimed-land.js app/static/land-mapping/index.html app/static/land-mapping/styles.css app/static/land-mapping/app.js tests/land_mapping_ui.test.js tests/test_land_mapping_ui.py
git commit -m "feat: add persistent claimed land map"
```

---

### Task 7: Documentation, Full Verification, and Browser Acceptance

**Files:**
- Modify: `README.md`
- Modify: `LAND_MAPPING_IMPLEMENTATION_PLAN.md` only if it still describes manual token entry or post-insertion conflicts.

**Interfaces:**
- Documents: demo login, exclusivity semantics, registry endpoint, protected patta endpoint, and production authentication follow-up.
- Verifies: all earlier task interfaces together.

- [ ] **Step 1: Update operator documentation**

Replace manual token-field instructions with `/login` and demo code `1234`. Document that `409` rejects duplicate/overlapping claims, list `GET /api/claims/registry` and `GET /api/claims/{id}/patta`, and state that production must disable demo auth and use OIDC.

- [ ] **Step 2: Run formatting and focused checks**

```powershell
git diff --check
node --test tests/land_mapping_ui.test.js
python -m pytest tests/test_demo_auth.py tests/test_claim_service.py tests/test_land_api.py tests/test_land_mapping_ui.py tests/test_migrations.py tests/test_production_safeguards.py tests/test_spatial_conflicts.py -q
```

Expected: zero whitespace errors and all focused tests pass.

- [ ] **Step 3: Run the complete suite in the application image**

```powershell
docker compose run --rm -T -v "${PWD}:/workspace" -w /workspace api sh -c "pip install --disable-pip-version-check -q -r requirements-dev.txt && env -u ENVIRONMENT -u DATABASE_URL python -m pytest -q"
```

Expected: the complete Python suite passes with zero failures.

- [ ] **Step 4: Rebuild and verify service health**

```powershell
docker compose up -d --build api
Invoke-RestMethod -Uri http://localhost:8000/health
docker compose ps
```

Expected: API response `{"status":"healthy"}` and API, database, and ClamAV containers report healthy.

- [ ] **Step 5: Run the browser acceptance flow**

Using the Browser plugin at desktop and `390x844`:

1. Open `/login`, enter `1234`, and confirm redirect to `/land-mapping` with no staff-code field.
2. Open **Claimed land** and record its initial persisted polygon count.
3. Open **New claim**, upload a synthetic patta that resolves to an unclaimed synthetic parcel, verify fields and polygon, confirm, and register.
4. Select **View on claimed-land map**, verify the new polygon and totals, click it, and open **View registered patta** in a protected browser tab.
5. Refresh `/land-mapping`, return to **Claimed land**, and verify the polygon persists.
6. Upload a new document for the same parcel and confirm the API/UI blocks registration with no second claim.
7. Verify desktop and mobile DOM snapshots, no framework overlay, no horizontal overflow, and no relevant `warn`/`error` console logs.

- [ ] **Step 6: Inspect persistence and privacy directly**

Query the database through the API container and confirm exactly one claim exists for the tested parcel, its document remains in private storage, and registry JSON contains neither `claimant_id` nor `storage_key`.

- [ ] **Step 7: Commit documentation and any verification-only corrections**

```powershell
git add README.md LAND_MAPPING_IMPLEMENTATION_PLAN.md
git commit -m "docs: document exclusive claimed land workflow"
```

- [ ] **Step 8: Leave the verified application state open**

Reset the browser viewport, keep only the claimed-land map tab and protected patta tab as deliverables, and finalize the browser session after all screenshots and console checks are complete.
