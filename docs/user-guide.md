# User guide

Markweave is a multi-user service. Every conversion, saved template preference, and result belongs
to the authenticated user unless an administrator performs an explicitly authorized template or
account action.

## Sign in and out

Open `/login` over HTTPS and enter the credentials supplied by an administrator. The session cookie
is Secure, HTTP-only, and same-site restricted. Inactivity and the absolute session lifetime can
end the session; sign in again if the interface returns to the login page. Sign out when using a
shared device.

The initial administrator account is created only when the database has no users. Operators must
rotate its bootstrap password after first sign-in. Administrators can create, disable, reactivate,
and reset passwords for local accounts from `/templates`.

## Convert a document

Open `/convert`, select a Markdown file or a supported archive, choose the output format, and keep
**Pandoc default** or explicitly select a visible template. DOCX and PDF are available individually;
**both** produces a ZIP containing
`document.docx`, `document.pdf`, and `traceability.json`.

Pandoc default requires no saved template and uses Pandoc's native reference document. When custom
Word styles are required, select an explicit immutable template version. Preferred templates and
the system fallback may be preselected when the page opens; choose **Use Pandoc default** to clear
that selection. They remain convenient choices and never block a template-free conversion.

The submission is asynchronous. Keep the conversion page open or reopen the job from its recent
conversion list to see its state. A queued or running job can be cancelled. Cancellation is
cooperative while a document engine is active, so the state may not change instantly. Completed
output remains downloadable until the configured result-retention deadline. Failed and cancelled
jobs do not expose a result.

Uploads are rejected when they violate the configured request, source, archive, image, diagram, or
PDF limits. Ordinary absolute HTTP(S) links are preserved as clickable document hyperlinks when
they have a valid host and contain no embedded credentials or control characters. They are not
downloaded during conversion. Remote images and other remotely loaded resources remain forbidden;
archives may contain one Markdown source plus approved local resources, and paths escaping the
archive are rejected. See [archives and images](archive-images.md) and [local Mermaid
rendering](mermaid.md).

## Templates

The template library at `/templates` shows visible active and archived templates. A DOCX reference
template is scanned and structurally validated before publication. A replacement creates a new
immutable version; it never modifies prior versions in place. Font declarations are checked against
the image's pinned font manifest.

Owners can edit metadata, publish replacements, restore an older version as a new version, archive,
or delete where the authorization and retention rules permit. Administrators have the documented
intervention powers, and those actions are audited. Concurrent edits can return a conflict; reload
the current template and repeat the intended change rather than blindly overwriting it. Full rules
are in [the template guide](templates.md).

## Traceability and support

Every response carries a correlation identifier. Record it with the job identifier when reporting a
problem. DOCX output embeds traceability metadata; PDF output has a canonical sidecar, and combined
output includes the sidecar in its ZIP. These identifiers help an operator correlate the request,
audit record, exact source, selected document-style mode, and worker logs without logging document
content. Versioned jobs identify the exact template version; Pandoc-default jobs record null
template fields instead of inventing an identity.
