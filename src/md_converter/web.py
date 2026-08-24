"""Server-rendered conversion-page helpers and immutable Web assets."""

from __future__ import annotations

from html import escape

from md_converter.auth.models import User
from md_converter.jobs.models import ConversionJob
from md_converter.templates.models import TemplateIdentity

WEB_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'; object-src 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def render_login_page(*, invalid: bool = False) -> str:
    """Render the local-login form without reflecting submitted values."""
    error = (
        '<p class="alert" role="alert">The username or password is incorrect.</p>'
        if invalid
        else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · Markdown Converter</title><link rel="stylesheet" href="/static/conversion.css"></head>
<body><main class="auth-shell"><section class="panel"><p class="eyebrow">Markdown Converter</p><h1>Sign in</h1>
{error}<form method="post" action="/login" class="stack">
<label>Username <input name="username" autocomplete="username" required></label>
<label>Password <input name="password" type="password" autocomplete="current-password" required></label>
<button type="submit">Sign in</button></form></section></main></body></html>"""


def render_conversion_page(
    user: User,
    selected: TemplateIdentity | None,
    selection_label: str | None,
    jobs: tuple[ConversionJob, ...],
    *,
    maximum_upload_bytes: int,
) -> str:
    """Render the authenticated shell; native JavaScript supplies live behavior."""
    selected_id = str(selected.id) if selected is not None else ""
    version_id = (
        str(selected.current_version_id)
        if selected is not None and selected.current_version_id is not None
        else ""
    )
    selected_name = (
        escape(selected.name) if selected is not None else "No template selected"
    )
    label = escape(selection_label or "Choose a template")
    recent = "".join(
        '<li><button type="button" class="job-link" '
        f'data-job-id="{job.id}">Conversion {str(job.id)[:8]} · '
        f"{escape(job.state.value)}</button></li>"
        for job in jobs
    )
    empty_recent = "" if jobs else '<li class="muted">No recent conversions.</li>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Convert · Markdown Converter</title><link rel="stylesheet" href="/static/conversion.css">
<script type="module" src="/static/conversion.js"></script></head>
<body data-max-upload-bytes="{maximum_upload_bytes}"><header class="topbar"><a class="brand" href="/convert">Markdown Converter</a>
<span>Signed in as {escape(user.username)}</span></header><main class="layout">
<section class="panel"><p class="eyebrow">New conversion</p><h1>Convert Markdown</h1>
<p>Upload one Markdown file without local resources, or a ZIP package containing Markdown and its local assets.</p>
<div id="page-alert" class="alert" role="alert" aria-live="assertive" hidden></div>
<form id="conversion-form" class="stack">
<label id="drop-zone" class="drop-zone">Source file
<input id="source" name="source" type="file" accept=".md,.zip,text/markdown,application/zip" required>
<span>Choose or drop a <strong>.md</strong> or <strong>.zip</strong> file</span></label>
<fieldset><legend>Output</legend><label><input type="radio" name="output" value="docx" checked> DOCX</label>
<label><input type="radio" name="output" value="pdf"> PDF</label><label><input type="radio" name="output" value="both"> DOCX and PDF (ZIP)</label></fieldset>
<div><label for="template-search">Template</label><input id="template-search" type="search" autocomplete="off" placeholder="Search active templates">
<p id="selected-template" class="selection" data-template-id="{selected_id}" data-version-id="{version_id}"><span>{label}</span><strong>{selected_name}</strong></p>
<ul id="template-results" class="results" aria-label="Template search results"></ul></div>
<button id="submit-conversion" type="submit"{"" if version_id else " disabled"}>Start conversion</button></form></section>
<aside class="stack"><section class="panel" aria-labelledby="status-heading"><h2 id="status-heading">Conversion status</h2>
<div id="job-status" aria-live="polite"><p class="muted">Submit a conversion or choose a recent one.</p></div>
<progress id="job-progress" max="100" value="0" hidden>0%</progress>
<div class="actions"><button id="cancel-job" type="button" hidden>Cancel conversion</button>
<a id="download-result" class="button" href="#" hidden>Download result</a></div></section>
<section class="panel"><h2>Recent conversions</h2><ul id="recent-jobs" class="results">{recent}{empty_recent}</ul></section></aside>
</main></body></html>"""
