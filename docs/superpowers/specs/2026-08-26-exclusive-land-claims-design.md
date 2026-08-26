# Exclusive Land Claims, Demo Authentication, and Claim Evidence

## Purpose

Give registry office staff a simple demonstration login, prevent more than one person from claiming the same land, and make every accepted claim traceable to its original patta from a persistent claimed-land map.

## Goals

- Replace the staff access-code control inside the application with a dedicated login page using the demonstration code `1234`.
- Reject a claim before creation when its registry parcel is already claimed or materially overlaps an existing claimed parcel.
- Persist accepted claim polygons in the central database and reload them on every authenticated session.
- Show the number of claimed parcels and total claimed official area.
- Let authenticated registry staff select a claimed polygon and view its registered patta.
- Keep claimant identity private in normal staff-facing responses.
- Audit successful claim registration, rejected competing claims, and patta views.

## Non-goals

- Production identity-provider integration, password recovery, or multi-factor authentication.
- Public document access or public parcel ownership lookup.
- Claim withdrawal, parcel release, or administrative reassignment.
- Partial claims inside a registry parcel. A claim applies to the complete stored parcel geometry.
- Resolving legacy duplicate claims automatically.

## Authentication

### User flow

`/login` presents a compact access-code form. Code `1234` signs the user in as the demonstration registry staff account and redirects to `/land-mapping`. Invalid codes remain on the page with an inline error. The main application no longer contains an access-code field. A clear sign-out action ends the session and returns to `/login`.

### Server behavior

- `POST /api/auth/demo-login` validates the submitted code on the server.
- The code is configurable as `DEMO_ACCESS_CODE` and defaults to `1234` in development.
- A successful login creates or reuses one `registry-demo` user and returns a short-lived signed JWT in an `HttpOnly`, `SameSite=Strict` cookie.
- `GET /api/auth/session` returns the current staff display name and role.
- `POST /api/auth/logout` clears the cookie.
- The authentication adapter accepts the session cookie for the browser while retaining bearer-token support for API clients and existing tests.
- Production configuration rejects demonstration authentication when `DEMO_AUTH_ENABLED` is not explicitly enabled. Replacing this adapter with OIDC remains a later deployment task.

The access code is never embedded in browser JavaScript and the signed token is never exposed to the page.

## Exclusive Claim Rule

### Eligibility

A parcel is claimable only when both conditions are true:

1. No accepted claim already references the same `parcel_id`.
2. Its geometry does not materially overlap an accepted claim on a different parcel, using the existing configured overlap thresholds.

Statuses from legacy data that represent a live claim (`matched`, `conflicting`, or `pending`) count as claimed. Rejected attempts are not claims.

### Transaction and concurrency

Claim submission performs eligibility checking and insertion in one database transaction.

- PostgreSQL obtains a short transaction-level advisory lock for the claim eligibility section. This serializes the overlap check and insertion so two simultaneous requests cannot both pass.
- A database uniqueness constraint on `claims.parcel_id` independently prevents duplicate exact-parcel claims.
- SQLite tests exercise the same service behavior without the PostgreSQL advisory lock.
- The migration refuses to create the uniqueness constraint if legacy duplicate parcel claims exist, requiring explicit administrative cleanup instead of silently deleting evidence.

### Rejection

If land is already claimed, the API returns HTTP `409` with a privacy-safe response:

```json
{
  "success": false,
  "message": "This land is already claimed.",
  "reason": "same_parcel"
}
```

The reason may be `same_parcel` or `spatial_overlap`. No second `Claim` or `ClaimConflict` record is created. An audit event records the rejected attempt, actor, candidate parcel, reason, and request identifier without exposing the existing claimant to the requester.

## Persistent Claimed-Land Registry

### API

`GET /api/claims/registry` returns every accepted claim available to authenticated registry staff:

- claim ID, status, and registration time;
- privacy-safe parcel metadata and GeoJSON geometry;
- registered patta filename and content type;
- a protected document-view URL;
- aggregate `claimed_parcel_count` and `claimed_official_area_sqm`.

The response excludes claimant identifiers and storage keys.

### Interface

The authenticated application has two explicit views:

- **New claim** retains the upload, extraction, parcel verification, and registration workflow.
- **Claimed land** loads all accepted polygons from the registry API on entry and after every successful claim.

The claimed-land view contains a concise summary, a persistent Leaflet map, and a detail panel. Accepted polygons use the established registry green. Selecting a polygon shows survey reference, village, official area, claim date, and status. The detail panel contains the clearly interactive **View registered patta** action.

After a successful registration, the completion panel offers **View on claimed-land map**, which switches views, refreshes the registry, and selects the newly accepted polygon. Browser refreshes and new devices reconstruct the same map from database state; no claim state depends on local storage.

## Protected Patta Retrieval

`GET /api/claims/{claim_id}/patta` requires an authenticated registry session.

- The endpoint resolves the claim to its immutable `Document.storage_key`.
- Local and S3 storage adapters expose a read operation without making the object public.
- The response uses the stored content type, the sanitized original filename, `Content-Disposition: inline`, `Cache-Control: private, no-store`, and `X-Content-Type-Options: nosniff`.
- Missing claims and inaccessible documents return `404` without revealing whether unrelated documents exist.
- Every successful view records `claim_document_viewed` in the audit log.

The browser opens the protected image or PDF in a new tab. The session cookie authorizes the request.

## Existing Conflict Behavior

The current post-insertion conflict workflow becomes legacy administrative behavior. New submissions never create a second conflicting claim. Existing conflicting claims remain visible to administrators and continue to count as occupied until they are resolved outside this pass.

## Error and Empty States

- Invalid access code: inline login error without revealing server details.
- Expired session: redirect to `/login` and preserve no sensitive page state.
- Empty registry: map area explains that no land has been claimed yet.
- Already-claimed candidate: review page names the blocking condition, disables registration, and leaves the existing claimant anonymous.
- Patta retrieval failure: detail panel displays a recoverable error and keeps parcel facts visible.

## Testing

### Automated

- Demo login accepts `1234`, rejects other codes, sets an HttpOnly cookie, reports the session, and logs out.
- Existing bearer authentication continues to work.
- Exact-parcel and material-overlap attempts return `409` and create no second claim.
- A successful first claim persists and appears in the registry aggregate response.
- Registry responses include geometry and document-view metadata but exclude claimant IDs and storage keys.
- Protected patta retrieval rejects anonymous requests, returns the original bytes and security headers to authenticated staff, and writes an audit event.
- UI tests cover authentication gating, view switching, persistent registry rendering, polygon selection, and already-claimed feedback.
- Migration tests cover the parcel uniqueness constraint.

### Browser

The end-to-end verification flow is:

`/login` -> enter `1234` -> open **New claim** -> upload a synthetic patta matching a registry parcel -> register it -> switch to **Claimed land** -> select its polygon -> open **View registered patta** -> refresh -> confirm the polygon and totals remain -> attempt the same claim again -> confirm registration is blocked.

Run this at desktop and 390-pixel mobile widths, verify no horizontal overflow, and confirm no browser console warnings or errors.

## Security Follow-up

The demonstration code is intentionally temporary. A production pass must disable demo authentication, configure an external OIDC provider, define finer-grained document-view roles, and move shared rate limiting to the deployment gateway or Redis.
