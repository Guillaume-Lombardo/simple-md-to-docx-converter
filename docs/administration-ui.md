# Template and account administration interface

The **Administration** navigation entry opens `/admin` for administrators. Its setup hub links to
**LLM settings**, **Templates**, **Typed fill templates**, and **Users**. Style references at `/templates` remain visible to every
authenticated user, including non-administrators. Existing `/composer/connections` remains the
personal-connection route for users with that permission.

## LLM settings

Open **Administration → LLM settings** (`/admin/llm`) to configure Composer in a compatible
deployment. The currently pinned published 0.7.3 simple quickstart does not include this setup;
a matched Composer-capable image pair requires the explicit
`MARKWEAVE_SIMPLE_COMPOSER_SETUP=true` opt-in until its published release pair is adopted. In that
standalone evaluation setup, the initial policy is disabled with no approved destination. Enter an HTTPS
endpoint, preview the resolved addresses, then explicitly approve its exact host, port and
individual addresses. The policy update uses `ETag`/`If-Match`; a stale page must reload before
another edit. DNS re-resolution is checked again on every model call, so a changed address needs
new approval. The service never calls an endpoint during the DNS preview.

After approval, add an instance connection, submit a write-only API key and/or mTLS identity and
internal CA, choose a permitted model, run a bounded test, and enable the connection. Grant access
to each intended user explicitly, including the administrator if they will use it; the right to
manage a connection does not automatically grant model use. The forms display only credential
presence after submission. Revocation, rotation, provider outages and disabled policy state do not
remove existing owner drafts or exports. Production deployments display the immutable operator
destination ceiling and permit the administrator to disable or re-enable model access within it.

**Templates** are Pandoc DOCX/PPTX style references. **Typed fill templates** opens the separate
private Word field-filling directory. It does not change a Pandoc style reference or confer
access to private author entries. An administrator can manage only their own typed templates or
ones explicitly shared with them; their role alone does not permit access to another user's
private content. Non-administrator owners use the same typed-template page from Composer.

The authenticated Next.js interface is available at `/templates`. Unauthenticated requests are
sent to `/login` by the frontend after the FastAPI session authority rejects the session. Dynamic
HTML carries the reviewed nonce CSP, and user-controlled names and identity text are rendered as
text rather than interpreted markup. FastAPI exposes no administration page or static browser asset.

## Template library

Every authenticated user can browse and download active templates. Each card identifies its owner,
status, name, and description. Search covers those displayed fields, the **My templates** filter
restricts the result to the signed-in owner, and the browser follows every page from the paginated
API rather than silently truncating the library.

Users can make any visible active template their preferred template or clear their preference.
The conversion page resolves that preference first and uses the system fallback only when no active
preference applies.

Owners and global administrators receive lifecycle controls for a template:

- rename and update the description;
- download the current DOCX;
- replace content after complete activation validation;
- inspect and download immutable historical versions;
- restore a historical version by creating a new copy-forward version;
- archive an active template;
- permanently delete an archived template when the API's reference guards allow it.

Create and replace forms accept a `.docx` file and comma-separated expected fonts. The browser
performs only immediate extension, emptiness, and configured-size checks. The server remains
authoritative for OpenXML, font, engine, ownership, status, and storage validation.

Every concurrent lifecycle mutation sends the revision-derived identity ETag through `If-Match`.
A stale page therefore receives the stable precondition error instead of overwriting a newer
change. Archive and permanent deletion require explicit browser confirmation. All permission
checks are repeated in the service after authentication; hiding owner controls from other users is
only a presentation aid.

## Typed fill templates

Upload a DOCX containing supported named content controls together with its field schema.
The form creates an immutable first version; replacing it creates another exact version.
The server scans uploads before parsing and validates the complete OOXML package, schema,
controls, and resource bounds. Owners can list versions and grant or revoke a named user's
access. Every replacement and grant uses `If-Match` so a concurrent update cannot be silently
overwritten. Only the owner may change content or grants, even when an administrator has a
read grant. A template grant does not grant access to any author entry. See the
[Composer guide](composer.md#fill-a-typed-word-template) for review and publication.

## Administrator users tab

Open **Users** (`/users`) for account management and the collapsible **Session policy** section.
The policy section starts collapsed and supports keyboard activation. The former `/session-policy`
address redirects here. The shared page header no longer repeats an inactivity reminder.

Only an administrator sees the local-accounts section, and every underlying endpoint independently
requires the administrator role. It lists and filters accounts by username and supports account
creation, deactivation, reactivation, and password reset. Account status changes and password
resets revoke the affected sessions through the authentication service. The interface never
receives password hashes, session tokens, or authentication versions.

Account creation and password reset can require renewal at the next sign-in. Administrators can
also add or cancel that requirement independently from an account card; changing it revokes
existing sessions. A required user authenticates with the current password, chooses and confirms a
new password on the dedicated page, and then signs in again with the new password.

## Idle-session policy API

FastAPI exposes the administrator-only `GET` and `PUT /api/v1/admin/session-policy` operations.
The read returns both role-specific whole-minute durations, authoritative per-role minimum/default/
maximum bounds, the one-minute granularity, the operator-configured absolute lifetime ceiling in
exact seconds, a revision, and an `ETag`. The update
must send that exact validator in `If-Match` and replaces both values in one transaction; a missing
precondition returns `428`, and a stale or malformed validator returns `412` without partial state
or audit. Standard-user access is forbidden. The accepted inclusive ranges are 5–300 minutes for
standard users and 5–60 minutes for administrators.

The **Session policy** section displays the effective values, bounds, absolute ceiling, and
revision. Updates preserve FastAPI authorization, concurrency checks, and session enforcement.

## Errors and accessibility

The page has an assertive live error region and a polite live result list. Expected API failures use
their stable English message; an invalid or non-JSON response becomes a generic English failure and
is never reflected into markup. Forms use labels, native controls, headings, and buttons. Downloads
retain the API's generated names, `nosniff`, content digest, and authorization behavior.

## Verification

`pnpm --filter @markweave/web run test:coverage` executes the frontend unit and component suites
through the root workspace and independently blocks line, branch, and function coverage below
90%. Functional tests exercise the ASGI
application through an HTTPS test origin over real SQLite and filesystem boundaries and separately verify owner
representation, search, authorization, and storage failures against live PostgreSQL and RustFS.
A pinned-Chromium Next.js browser scenario runs two ordinary users and an administrator against the
final rootless backend, frontend, and router images through template creation and invalid uploads,
download, metadata changes including a
stale `If-Match`, replacement, version history and restoration, preference changes, guarded
archive/deletion, CSRF and revoked-session denial, account creation and search, status changes, and
password reset. It also checks that duplicate form submission does not create duplicate mutations.
The final-image browser workflow additionally requires password renewal, proves that the current
password must succeed before the restricted page is shown, confirms that normal application routes
remain unavailable, renews the password, and verifies the required fresh login. In both storage
profiles it also mounts a startup CSV into the rootless API container, exercises its provisioned
account, replaces the mounted password, restarts the image, and proves the old password was revoked.

The complete browser suite is part of the final-image E2E harness and runs in both storage profiles.
Its committed CI-equivalent invocations are:

```bash
bash scripts/e2e/run.sh standalone
bash scripts/e2e/run.sh distributed
```

The final-image E2E suite exercises the primary administration workflows and relevant authorization,
failure, recovery, and concurrency cases in both storage profiles. Deployment-specific TLS and
rootless controls are described in [container-deployment.md](container-deployment.md).
