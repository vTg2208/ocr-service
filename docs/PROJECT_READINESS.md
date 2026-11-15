# Project implementation and readiness

Updated 14 November 2026 on `feature/open-source-model-integrations`.

## Implemented software

| Capability | Implementation and verification |
| --- | --- |
| OCR/archive/cases | Existing document intake, entity extraction, review, decisions, versioned geometry and title records |
| Evidence overlays | New OCR runs preserve the processed PNG frame, its exact dimensions and checksum. Reviewer-only routes serve page/field metadata, images and original downloads. Tests compare stored pixels against preprocessing and reject tampered artifacts. Older runs without saved frames remain explicitly unavailable. |
| Satellite inference | Pinned pretrained Dynamic World runs on actual nine-band Sentinel-2 imagery, with matched SCL masks, private inputs/results, provenance and human review |
| Personal plot workflow | Draw/save/submit, independent staff approval, shared Atlas snapshots, real analysis requests, private assistance and correspondence |
| Atlas/assets/DSS/reports | Working interfaces are exposed; reviewed model observations feed the existing FRA asset and fact pipeline |
| OIDC | Authorization code + S256 PKCE, state/nonce, signed ID-token verification, fixed issuer/audience, and operator-provisioned identities. Tests use actual RSA-signed ID tokens against an isolated HTTP transport. Provider role claims never grant application roles. |
| Shared request quotas | Redis atomic Lua INCR/EXPIRE, stable authenticated identity buckets, expiry and fail-closed errors. Tests verify two instances share one quota. Compose includes an internal Redis service. |
| Email/SMS verification | SMTP with TLS and Twilio with authenticated, bounded HTTPS requests. Challenge digests, expiry, retry limits, ownership, and the original delivery method are persisted. Terminal challenges remain development-only. |
| Data migrations | Single head `20261114_0019`, including satellite analyses, persisted OTP delivery method and saved OCR pages |
| Demonstration data | Existing synthetic FRA, cadastral and community fixtures plus explicitly labelled mock evaluation samples. Mock metrics are marked ineligible as real benchmarks and mixed real/mock evaluations are rejected. |

## Production configuration

Use `.env.production.example` as a checklist, and put real secrets in a protected
environment or secret manager. Do not commit the completed environment file.
Production startup requires a non-default signing secret, PostgreSQL/PostGIS,
malware scanning, OIDC, disabled local/demo sign-in, and shared Redis quotas.

1. Register the web application with your OIDC provider. The fixed callback is
   `https://YOUR_HOST/api/auth/oidc/callback`. Request `openid profile` and allow
   authorization-code flow with PKCE S256.
2. Configure `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, and
   `OIDC_REDIRECT_URI`. Leave `OIDC_AUTO_PROVISION=false` for controlled accounts.
3. Provision each provider subject with an explicit stored role:

   ```powershell
   python -m scripts.provision_oidc_user --issuer https://YOUR_ISSUER --subject PROVIDER_SUBJECT --display-name "Review Officer" --role reviewer
   ```

4. Configure SMTP host/sender and TLS credentials; set `OTP_EMAIL_BACKEND=smtp`.
   Configure the approved Twilio sender, account SID/token and
   `OTP_SMS_BACKEND=twilio`. SMS contacts must use international E.164 format.
5. Apply migrations and install the baseline weights in the worker volume:

   ```powershell
   docker compose build api worker
   docker compose run --rm --no-deps worker python -m scripts.install_land_cover_model
   docker compose up -d
   docker compose exec api python -m scripts.migrate
   docker compose exec api alembic current
   ```

6. Validate a real provider login, real email/SMS delivery, scanning, backup and
   restore, and HTTPS in the intended deployment environment. These cannot be
   certified from placeholder credentials or mocked provider transports.

Sign-out clears the application session; provider-wide sign-out remains under
the identity provider's policy. OIDC identities are keyed by issuer and subject,
never automatically merged into local accounts by email.

## Repeatable checks

```powershell
python -m pytest tests -q
node --test tests/*.test.js
python -m playwright install chromium
python -m scripts.validate_browser
python -m scripts.test_postgres
```

The browser command creates an isolated temporary database, runs explicitly
synthetic fixtures, exercises real browser controls and API responses, saves
screenshots and `tmp/browser-validation/validation.json`, and shuts down its
own server. It does not change the application's database. The current browser
run passed nine workflow/rendering checks with no JavaScript errors. External
font requests were blocked so the run also checks system-font fallbacks.

The final full backend run passed 521 tests with 15 service-dependent skips,
including the new evidence/metadata and mock-evaluation checks. The JavaScript
suite passed 93 tests. An isolated native PostgreSQL 16.16/PostGIS 3.7.0 run passed
263 tests and Alembic schema-drift checks, exercising the existing spatial,
concurrency, worker, Atlas and DSS regression targets. That native run used
portable official Windows binaries, not Docker. The test server was bound only
to loopback and stopped after verification.

The installed PaddleOCR 3.7.0/PaddlePaddle 3.3.1 runtime also downloaded and
initialized the official Tamil mobile recognition and mobile detection models.
An actual inference run recognized two lines from a generated English mock
document. This is an execution smoke check, not a verified Tamil OCR benchmark.
The separate live Sentinel-2 execution check is documented in the satellite
integration guide and retains no claim of local ground-truth accuracy.

## Genuine remaining gates

- **Production accounts/configuration:** real OIDC, SMTP and Twilio settings,
  HTTPS host, scanning service and deployment approval are external inputs.
  Provider adapters are implemented; mocked protocol tests are not live delivery.
- **Authoritative cadastral coverage:** importers are implemented, but bundled
  mock parcels are not authoritative boundaries. A licensed source and provenance
  still need to be supplied for operational use.
- **Real model evaluation:** verified Tamil OCR labels, held-out land-cover labels,
  research training data/checkpoints, all-12-band comparisons and wider U-Net
  experiments have not been supplied. Mock data is suitable for demonstrations
  and software tests only. The presentation's compact-model scores do not apply
  to Dynamic World.
- **Research submission:** the final thesis, camera-ready paper and empirical
  conclusions require the actual research artifacts and academic review. They
  have not been fabricated from demonstration fixtures.
- **Docker release:** this machine has no Docker engine. Compose is prepared;
  the image build and full Compose stack must still be exercised on a Docker host.

The application is a testable project prototype. Passing implementation checks
does not by itself certify production operation or complete missing research.
