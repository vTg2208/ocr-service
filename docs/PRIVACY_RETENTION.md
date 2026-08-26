# Privacy and retention policy baseline

This service records document claims; it does not determine or transfer legal ownership.

## Data classification and access

- Uploaded pattas, raw OCR, claimant identifiers, and claim evidence are restricted personal data.
- FRA rights-holder identities, external references, claimant categories, Gram Sabha links, declared DSS facts, and evidence provenance are restricted case data.
- Normal users may read only their documents, claims, and generic notifications.
- Normal FRA users do not receive rights-holder external references; reviewer and administrator access remains authenticated and purpose-limited.
- Parcel endpoints contain registry geometry and provenance only; they never contain claimant or document data.
- Detailed conflict evidence is restricted to the `admin` role and every resolution is audited.
- S3 storage must use private buckets, blocked public access, server-side encryption, and a narrowly scoped API role.
- Satellite source URIs are private metadata. API responses expose the observation and supporting-evidence label without returning the source URI.
- DSS audit and recommendation rows may retain declared facts and rule identifiers, but never raw document content.

## Retention

- Open claims, open conflicts, audit events, and the cadastral source/version history are retained while legally or operationally required.
- FRA decisions, geometry versions, title versions, evidence verification history, satellite observations, and DSS evaluations remain versioned or append-only while the case and applicable retention duty require them.
- Rejected uploads that never create a valid document row are removed immediately.
- Closed-case document retention must be configured with the responsible authority before production; the recommended baseline is seven years unless local law requires otherwise.
- Audit events are append-only. Corrections add events and do not overwrite historical evidence.
- A legal hold suspends deletion for all related documents, OCR results, claims, conflicts, and audits.

## Subject and administrator operations

- Access/export and erasure requests require identity verification and an authorization/legal-retention check.
- Deletion must remove the storage object and personal database records in a controlled job while preserving any legally required pseudonymized audit evidence.
- Never place raw OCR, names, tokens, document contents, or signed storage URLs in application logs.

## Production release gate

Before launch, document the controller/processor, lawful basis, geographic storage region, retention duration, incident contact, encryption/key ownership, backup retention, restore test date, and approved cadastral-data license.
