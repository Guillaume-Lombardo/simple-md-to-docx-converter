# Composer workspace

Composer is an optional, permission-gated workspace beside the quick **Convert** workflows.
The selector near the logo opens Composer without changing a conversion job. **2docx**,
**2pptx**, and experimental **2md** remain separate. Administrators configure an instance
connection through **Administration → LLM settings**; users with permission can configure a
personal connection at **My connections**. Managing a connection does not grant model use.

## Start or reopen a draft

Open **Composer**, choose a saved draft, or upload Markdown, a ZIP with Markdown and local assets,
DOCX, PPTX, or PDF. The backend scans the uploaded bytes before accepting the draft. You can also
choose **Open in Composer** from an existing quick conversion. That copies an authorized result
into a new draft; it leaves the conversion job and its download untouched. The selected source,
conversion inputs, and unsent fields survive navigation and a page refresh in this browser;
saved Composer drafts and their history are stored by the service. Signing out clears locally
cached conversion inputs.

From experimental **2md**, a selected DOCX, PPTX, or PDF can be copied into Composer. Other
supported Revert inputs remain selected but have no Composer source handoff. After a successful
Revert job, **Open Markdown result in Composer** copies its Markdown or Markdown-and-assets ZIP
through owner checks and a fresh scan. The original Revert job, selected source, and settings
remain available. Direct native Office editing is a later capability; copying an Office source
does not make it editable in Composer today.

The selected draft shows its source type and ID. For a Markdown or ZIP source, edit the Markdown in
the draft. **Save draft** stores the working text; **Publish saved Markdown** records an immutable
text revision without generating another format. **Generate** also saves any current human edit
and freezes the approved Markdown before starting conversion, so separate publication steps are
not required to get an output file. An accepted proposal advances the working Markdown with the
approved text before later edits. For an
Office or PDF source, draft notes remain separate from the original file and do not edit it. A
source revision captures the exact uploaded or handed-off bytes. For ZIP, its source-revision
download is the complete original package and its preview is the validated main Markdown. A
later draft edit does not rewrite a captured source revision. A published ZIP edit retains the
original scanned archive as a separate source artifact; its preview and download are the approved
Markdown text.

## Review a model suggestion

Save a message or answer in the conversation, or drop Markdown or plain text into its message box.
Under **Ask a model**, inspect the authorized
connection, HTTPS destination, model, and exact text that will be transmitted. Composer sends only
that displayed text; it does not attach the draft file implicitly. Choose a maximum output-token
count within the service's advertised limit. A call creates a bounded step
that can be cancelled. A suggestion step yields a pending proposal, never an automatic document
change.

Choose **Ask for missing information** when the assistant should return one explicit question.
The question remains pending across refresh and provider outages. Save your answer to link it to
that question and the conversation. **Prepare answer for model** fills the exact question-and-answer
text for your review; sending it starts a fresh authorized, bounded step. No worker waits while you
answer, and saving the answer alone makes no document change or model call. If the original
question used selected author entries, refresh their exact prompt preview before resuming; a
revoked or changed entry blocks that step.

Expand a proposal to inspect its result, provenance, proposed change, and validation state. Model
statements are unverified unless you approve them; a model response does not create a citation.
Accept the proposed text, enter a correction with your own value, or reject it. Rejection leaves
the document unchanged. A stale decision after a concurrent edit asks you to reload and review.
For approved Markdown proposals, **Publish approved text** creates a new immutable revision.
Native Office editing is a separate later capability; an unsupported publication returns a safe
error and leaves the existing artifact intact.

## Use author knowledge

**Authors** opens the private author directory. Create an entry with a name and structured fields,
and mark each field as supplied, cited with a source reference, model suggested, human approved, or
human edited. An unresolved fact has no value until you review it. The owner may grant an entry to
one named user and revoke that grant; access changes are audited. Administrator status alone does
not grant access to another owner's entry. Author information is included in a model request only
when you select it and review the exact transmitted prompt. Composer rechecks access when the
request starts, before sending it, when a paused step resumes, and before its result is published.
If access or the entry version changes, the step stops without publishing the stale result.

Revocation prevents new disclosure, while an already published revision retains its provenance
under its own owner and retention rules. A style-reference template never grants access to an
author entry.

## Fill a typed Word template

**Fill templates** manages private DOCX templates with an explicit field schema. These are distinct
from the style-reference templates used by ordinary conversions. The schema names required and
optional text, date, boolean, and integer fields, their constraints, any authored defaults, and
bounded paragraph repeats. An owner may share a template with a named user without sharing any
author entries. Uploads are scanned before DOCX parsing; unsupported content controls or unsafe
Office packages are rejected. A replacement creates a new immutable version, leaving prior
versions available to authorized users.

In a draft's **Fill template** panel, select one exact template version and any authorized author
entries. Composer proposes matching author values and asks durable questions for missing or
ambiguous fields. Edit the values and provenance, then approve the complete plan. Approval makes
no document change by itself. **Publish fill** creates a new immutable DOCX revision whose preview
and download are identical. The revision records the exact source and template digests, schema,
approved values, provenance, author references, and component version. It does not ask a model to
fill the document. **Regenerate** uses those retained approved inputs and the exact template
version, checks the output digest, and makes no model call. A changed template or value requires a
new plan and review. Typed filling supports the qualified content-control subset; general Word
editing remains a later capability.

## Generate a reviewed document

Choose DOCX, PDF, or PPTX and an active style-reference template, or the Pandoc default. PPTX also
offers the existing Markdown/Marp dialect and slide heading-level controls. **Generate** uses the
current approved Markdown, including any unsaved human edit, and runs through the ordinary
asynchronous conversion queue. It does not send another model request. A generated result becomes
a new immutable revision only after the complete artifact is ready and the draft still matches
the approved source. Its preview and download contain the same exact bytes; the previous revision
remains available while work is pending or if conversion fails. A concurrent change requires a
fresh review before publication. The selected template version, render options, approved text,
and model provenance are recorded with the generated revision. The generation card supports
cancellation and can be recovered after refresh.

## Inspect and restore revisions

Select a revision in **History**. The preview, download URL, source digest, and displayed operation
belong to that exact revision. Markdown revisions show text and a highlighted semantic line diff
against the preceding revision. Native Office revisions use a private viewer; a PDF revision uses
its own PDF preview. An unavailable semantic comparison is labeled explicitly. **Restore as new
revision** copies the chosen bytes forward, preserving the earlier history. Neither restoration
nor reopening an existing export calls a model.

Native Office previews are a visual aid. DOCX pagination can differ from Word, and unsupported
features may display differently. Download the exact revision for the authoritative file. The
browser may download and parse the complete private file before showing visible pages or slides;
display virtualization does not make the transfer size constant. See the
[preview qualification and limits](composer-preview.md) for measured fidelity and security
evidence.

When Composer has no configured or enabled connection, model use was not granted, or a provider
is temporarily unavailable, the workspace explains the state. Authorized retained drafts,
history, and downloads remain available. Ordinary conversions do not require a model connection.
