# Production integrations

Code is complete and tested against fakes; **live behaviour is unverified until
you supply real credentials** (issuer, SMTP account, Twilio account, Redis).

| Concern | Setting | Notes |
|---|---|---|
| Sign-in | `OIDC_ENABLED`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI` | Any standards-based provider. Production requires local and demo sign-in off. |
| Staff provisioning | `python -m scripts.provision_oidc_user` | Binds an OIDC subject to a role; `OIDC_AUTO_PROVISION` stays off unless wanted. |
| Rate limiting | `REDIS_URL` | Required in production; fails closed (503) if Redis is down. |
| Email OTP | `OTP_EMAIL_BACKEND=smtp` + `SMTP_*` | `SMTP_TLS_MODE=plain` is rejected in production. |
| SMS OTP | `OTP_SMS_BACKEND=twilio` + `TWILIO_*` | |
| Migrations | `20261114_0017`–`0019` | Land cover, contact delivery, document evidence. |

Mock data lives only in `data/mock_eval/` (labelled `mock: true`) and must never be
used as a benchmark. Real accuracy figures need verified labels and cadastral data.
