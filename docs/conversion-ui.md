# Conversion interface

The authenticated conversion page is available at `/convert`. A successful browser sign-in redirects
there; an unauthenticated request redirects to `/login`. The Next.js workspace presents drag-and-drop,
template search, asynchronous submission, polling, cancellation, recent jobs, and downloads while
calling FastAPI directly through same-origin `/api/v1` requests. FastAPI remains authoritative for
authentication, validation, template resolution, quotas, capacity, persistence, job state, and result
headers. The T64 cutover removed the legacy FastAPI-rendered conversion page after the complete
pre-removal matrix passed in both storage profiles.

## Start a conversion

1. Choose or drop exactly one `.md` or `.zip` file. A standalone Markdown file cannot depend on local
   resources; use a ZIP package for Markdown plus images or other approved local assets.
2. Keep **Pandoc default**, keep the resolved preferred or system fallback template, or search active
   templates by name and choose another result. **Use Pandoc default** clears a selected template.
   The submitted job carries template identifiers only for an immutable selected version.
3. Choose DOCX, PDF, or both. "Both" downloads as a ZIP archive.
4. Select **Start conversion**. Submission returns immediately; the interface shows the safe job step
   and percentage while it polls with progressive backoff.

The browser assigns an idempotency key before submission and reuses it after an ambiguous network or
server failure. Repeating that request therefore cannot enqueue a second equivalent job. Choosing a
different file, output, or template starts a new request identity. Client-side suffix, emptiness, and
configured-size checks provide immediate feedback, but FastAPI repeats and owns every validation.

## Status, cancellation, and downloads

The ten most recent owner conversions can be reopened. Queued and running jobs may be cancelled. Terminal
states are displayed as succeeded, failed, cancelled, or expired; failure text comes only from the
API's safe error contract. An expired conversion no longer offers a download. Successful downloads
use an owner-authorized API route and preserve the uploaded filename stem while replacing its
extension with `.docx`, `.pdf`, or `.zip`. Jobs without persisted source metadata instead use
`conversion-<job-id>` as the filename stem. Their responses also enforce `nosniff` and a private
no-store cache policy. Page text and errors are announced through accessible live regions.

If the session expires, sign in again. Browser mutations send the session-bound CSRF value from the
Secure, SameSite=Lax `__Host-md_converter_csrf` cookie as `X-CSRF-Token`. The opaque session cookie
remains HttpOnly, and all ownership and permission decisions remain server-side.

## Frontend verification

Run the TypeScript component/controller tests and blocking 90% statement, line, branch, and function
coverage gates with:

```bash
cd web && npm run test:coverage
```

Unit tests cover rendering, schema handling, request identity, races, polling, cancellation, and safe
downloads. The dedicated real-browser final-image workflow runs through the test-only same-origin
router for both SQLite and PostgreSQL profiles; this is now the production browser contract.
