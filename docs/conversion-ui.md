# Conversion interface

The authenticated conversion page is available at `/convert`. A successful browser sign-in redirects
there; an unauthenticated request redirects to `/login`. The page is server-rendered, and one external
native JavaScript module adds drag-and-drop, template search, asynchronous submission, polling,
cancellation, and download behavior. Template and account administration remain outside this page.

## Start a conversion

1. Choose or drop exactly one `.md` or `.zip` file. A standalone Markdown file cannot depend on local
   resources; use a ZIP package for Markdown plus images or other approved local assets.
2. Keep the resolved preferred or system fallback template, or search active templates by name and
   choose another result. The interface shows which kind of default was resolved. The submitted job
   always carries the displayed immutable template-version identifier.
3. Choose DOCX, PDF, or both. “Both” downloads as a ZIP archive.
4. Select **Start conversion**. Submission returns immediately; the interface shows the safe job step
   and percentage while it polls with progressive backoff.

The browser assigns an idempotency key before submission and reuses it after an ambiguous network or
server failure. Repeating that request therefore cannot enqueue a second equivalent job. Choosing a
different file or template starts a new request identity.

## Status, cancellation, and downloads

Recent conversions on the page can be reopened. Queued and running jobs may be cancelled. Terminal
states are displayed as succeeded, failed, cancelled, or expired; failure text comes only from the
API's safe error contract. An expired conversion no longer offers a download. Successful downloads
use an owner-authorized API route, a server-generated filename, `nosniff`, and private no-store cache
policy. Page text and errors are announced through accessible live regions.

If the session expires, sign in again. Browser mutations send the session-bound CSRF value from the
Secure, SameSite=Lax `__Host-md_converter_csrf` cookie as `X-CSRF-Token`. The opaque session cookie
remains HttpOnly, and all ownership and permission decisions remain server-side.

## JavaScript verification

Run the independent JavaScript tests and blocking 90% line, branch, and function coverage gates with:

```bash
npm run test:web
```

T16 provides unit tests for rendering and browser logic plus functional HTTP coverage over real local
authentication, SQLite, and filesystem boundaries. The final hardened application image does not
exist until T20, so Playwright E2E against that rootless image remains sequenced to T20/T21. T21 must
repeat submission, polling, cancellation, expiration, download, authorization, restart recovery, and
concurrency behavior for both storage profiles. This sequencing is reviewable debt, not a waiver.
