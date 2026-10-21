import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import test from "node:test";

import { chromium } from "playwright-core";

const baseURL = process.env.MARKWEAVE_E2E_BASE_URL || "http://localhost:3100";
const profile = process.env.MARKWEAVE_E2E_PROFILE || "standalone";
const typedDocx = "/spikes/anydoc/corpus/docx/typed.docx";
const typedSchema = "/spikes/anydoc/corpus/docx/typed-schema.json";
const editableDocx = "/spikes/anydoc/corpus/docx/text.docx";
const editablePptx = "/spikes/anydoc/corpus/pptx/pres.pptx";
const pptxMediaType =
  "application/vnd.openxmlformats-officedocument.presentationml.presentation";
const docxMediaType =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const malwareMarker =
  "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*";
const providerEndpoint = "https://e2e-llm:8443/v1";
const providerModel = "composer-e2e-model";
const providerSecret = "composer-e2e-write-only-secret";
// The browser runner is the final image, so this is the packaged CLI.
const cliBinary = "/opt/md-converter/venv/bin/markweave";
const cliPython = "/opt/md-converter/venv/bin/python";
const cliStateHome = "/work/markweave-cli-state";
const cliFiles = "/work/markweave-cli-files";
const csrfCookie = "__Host-md_converter_csrf";

async function login(page, username, password) {
  await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
  await page.getByRole("textbox", { name: "Username" }).fill(username);
  await page.getByLabel("Password").fill(password);
  await Promise.all([
    page.waitForURL("**/convert"),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);
}

async function api(page, method, path, options = {}) {
  return page.evaluate(
    async ({ method, path, options }) => {
      const csrf = document.cookie
        .split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith("__Host-md_converter_csrf="))
        ?.split("=", 2)[1];
      const headers = { ...options.headers };
      let body;
      if (options.source) {
        const form = new FormData();
        form.set(
          "source",
          new File([options.source.content], options.source.filename, {
            type: "text/markdown",
          }),
        );
        form.set("title", options.source.title);
        body = form;
      } else if (options.upload) {
        const form = new FormData();
        form.set(
          "source",
          new File(
            [new Uint8Array(options.upload.bytes)],
            options.upload.filename,
            { type: options.upload.type },
          ),
        );
        form.set("title", options.upload.title);
        body = form;
      } else if (options.typed) {
        const form = new FormData();
        form.set("name", options.typed.name);
        form.set("schema", options.typed.schema);
        form.set(
          "file",
          new File([new Uint8Array(options.typed.bytes)], "typed.docx", {
            type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          }),
        );
        body = form;
      } else if (options.json !== undefined) {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(options.json);
      }
      if (method !== "GET" && csrf) {
        headers["X-CSRF-Token"] = decodeURIComponent(csrf);
      }
      const response = await fetch(path, {
        method,
        headers,
        body,
        cache: "no-store",
        credentials: "same-origin",
      });
      const text = await response.text();
      let json;
      try {
        json = JSON.parse(text);
      } catch {
        json = null;
      }
      return { status: response.status, text, json };
    },
    { method, path, options },
  );
}

function expectStatus(result, expected) {
  assert.equal(result.status, expected, result.text);
  return result.json;
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function createDraftWithSource(page, suffix) {
  const draft = expectStatus(
    await api(page, "POST", "/api/v1/composer/drafts", {
      source: {
        content: `# Typed Composer ${suffix}\n\nReviewed source.\n`,
        filename: "typed-source.md",
        title: `Typed Composer ${suffix}`,
      },
    }),
    201,
  );
  const path = `/api/v1/composer/drafts/${draft.id}`;
  const source = expectStatus(
    await api(page, "POST", `${path}/revisions/from-source`, {
      headers: {
        "If-Match": draft.etag,
        "Idempotency-Key": `typed-source-${suffix}`,
      },
    }),
    201,
  );
  const current = expectStatus(await api(page, "GET", path), 200);
  assert.equal(current.current_revision_id, source.id);
  return { path, source, current };
}

async function exactOfficeBytes(page, path, revision) {
  const artifactPath = `${path}/revisions/${revision.id}/artifacts`;
  const [preview, download] = await Promise.all([
    page.request.get(`${artifactPath}/preview`),
    page.request.get(`${artifactPath}/download`),
  ]);
  assert.equal(preview.status(), 200);
  assert.equal(download.status(), 200);
  const [previewBytes, downloadBytes] = await Promise.all([
    preview.body(),
    download.body(),
  ]);
  assert.deepEqual(previewBytes, downloadBytes);
  assert.equal(downloadBytes.subarray(0, 2).toString(), "PK");
  assert.equal(
    revision.artifacts.find((artifact) => artifact.kind === "download")?.sha256,
    sha256(downloadBytes),
  );
  assert.equal(
    revision.artifacts.find((artifact) => artifact.kind === "preview")?.sha256,
    sha256(previewBytes),
  );
  return downloadBytes;
}

async function docxTextEdits(alice, bob, suffix) {
  const sourceBytes = await readFile(editableDocx);
  const draft = expectStatus(
    await api(alice, "POST", "/api/v1/composer/drafts", {
      upload: {
        bytes: Array.from(sourceBytes),
        filename: "editable.docx",
        type: docxMediaType,
        title: `Editable report ${suffix}`,
      },
    }),
    201,
  );
  const path = `/api/v1/composer/drafts/${draft.id}`;
  const base = expectStatus(
    await api(alice, "POST", `${path}/revisions/from-source`, {
      headers: {
        "If-Match": draft.etag,
        "Idempotency-Key": `docx-edit-source-${suffix}`,
      },
    }),
    201,
  );
  assert.deepEqual(await exactOfficeBytes(alice, path, base), sourceBytes);
  const targetsPath = `${path}/revisions/${base.id}/docx-text-targets`;
  const listed = expectStatus(
    await api(alice, "GET", `${targetsPath}?limit=500`),
    200,
  );
  assert.equal(listed.download_sha256, sha256(sourceBytes));
  assert.equal(listed.engine_version, 1);
  const title = listed.targets[0];
  const cell = listed.targets.find((item) => item.in_table);
  assert.equal(title.target, "word/document.xml#t0");
  assert.ok(cell, "The qualified fixture has an editable table cell");
  const editPath = `${path}/revisions/${base.id}/docx-edits`;
  const operation = (target, expected, text) => ({
    op: "replace_text",
    target,
    expected_text: expected,
    text,
  });

  const ready = expectStatus(await api(alice, "GET", path), 200);
  assert.equal(ready.current_revision_id, base.id);
  assert.equal((await api(bob, "GET", targetsPath)).status, 404);
  assert.equal(
    (
      await api(bob, "POST", editPath, {
        headers: {
          "If-Match": ready.etag,
          "Idempotency-Key": `docx-edit-foreign-${suffix}`,
        },
        json: { operations: [operation(title.target, title.text, "Taken")] },
      })
    ).status,
    404,
  );
  for (const [rejected, code] of [
    [operation(title.target, "Stale text", "x"), "TARGET_CHANGED"],
    [operation("word/document.xml#t99999", "", "x"), "UNSUPPORTED_TARGET"],
    [operation(title.target, title.text, "line\nbreak"), "INVALID_TEXT"],
  ]) {
    const response = await api(alice, "POST", editPath, {
      headers: {
        "If-Match": ready.etag,
        "Idempotency-Key": `docx-edit-rejected-${code}-${suffix}`,
      },
      json: { operations: [rejected] },
    });
    assert.equal(response.status, 422, response.text);
    assert.equal(response.json.error.code, `COMPOSER_DOCX_EDIT_${code}`);
  }
  assert.equal(
    expectStatus(await api(alice, "GET", path), 200).etag,
    ready.etag,
  );

  const editedTitle = `Edited E2E title ${suffix}`;
  const winnerRequest = {
    operations: [
      operation(title.target, title.text, editedTitle),
      operation(cell.target, cell.text, "Edited E2E cell"),
    ],
  };
  const race = await Promise.all(
    [0, 1, 2].map((index) =>
      api(alice, "POST", editPath, {
        headers: {
          "If-Match": ready.etag,
          "Idempotency-Key": `docx-edit-race-${index}-${suffix}`,
        },
        json:
          index === 0
            ? winnerRequest
            : {
                operations: [
                  operation(title.target, title.text, `Racer ${index}`),
                ],
              },
      }),
    ),
  );
  assert.deepEqual(race.map((result) => result.status).sort(), [201, 412, 412]);
  const winnerIndex = race.findIndex((result) => result.status === 201);
  const edited = race[winnerIndex].json;
  assert.equal(edited.operation, `docx_edit:${base.id}`);
  assert.equal(edited.provenance, "human:docx_edit");
  assert.equal(edited.model_identity, null);
  const replay = expectStatus(
    await api(alice, "POST", editPath, {
      headers: {
        "If-Match": ready.etag,
        "Idempotency-Key": `docx-edit-race-${winnerIndex}-${suffix}`,
      },
      json:
        winnerIndex === 0
          ? winnerRequest
          : {
              operations: [
                operation(title.target, title.text, `Racer ${winnerIndex}`),
              ],
            },
    }),
    201,
  );
  assert.equal(replay.id, edited.id);
  const editedBytes = await exactOfficeBytes(alice, path, edited);
  assert.notDeepEqual(editedBytes, sourceBytes);
  const reread = expectStatus(
    await api(
      alice,
      "GET",
      `${path}/revisions/${edited.id}/docx-text-targets?limit=500`,
    ),
    200,
  );
  assert.equal(reread.total, listed.total);
  const expectedTitle =
    winnerIndex === 0 ? editedTitle : `Racer ${winnerIndex}`;
  assert.equal(reread.targets[0].text, expectedTitle);

  await alice.goto(`${baseURL}/composer`, { waitUntil: "networkidle" });
  await alice
    .getByRole("combobox", { name: "Saved drafts" })
    .selectOption(draft.id);
  await alice
    .frameLocator(`iframe[title="DOCX revision ${edited.id} preview"]`)
    .getByText(expectedTitle, { exact: true })
    .waitFor();

  const afterEdit = expectStatus(await api(alice, "GET", path), 200);
  assert.equal(afterEdit.current_revision_id, edited.id);
  assert.equal(
    (
      await api(alice, "POST", editPath, {
        headers: {
          "If-Match": afterEdit.etag,
          "Idempotency-Key": `docx-edit-stale-base-${suffix}`,
        },
        json: winnerRequest,
      })
    ).status,
    412,
  );
  const restored = expectStatus(
    await api(alice, "POST", `${path}/revisions/${base.id}/restore`, {
      headers: {
        "If-Match": afterEdit.etag,
        "Idempotency-Key": `docx-edit-restore-${suffix}`,
      },
    }),
    201,
  );
  assert.deepEqual(await exactOfficeBytes(alice, path, restored), sourceBytes);
  const history = expectStatus(
    await api(alice, "GET", `${path}/revisions`),
    200,
  ).revisions.map((item) => item.operation);
  assert.deepEqual(history, [
    "capture_source",
    `docx_edit:${base.id}`,
    "restore",
  ]);
}

async function pptxTextEdits(alice, bob, suffix) {
  const sourceBytes = await readFile(editablePptx);
  const draft = expectStatus(
    await api(alice, "POST", "/api/v1/composer/drafts", {
      upload: {
        bytes: Array.from(sourceBytes),
        filename: "editable.pptx",
        type: pptxMediaType,
        title: `Editable deck ${suffix}`,
      },
    }),
    201,
  );
  const path = `/api/v1/composer/drafts/${draft.id}`;
  const base = expectStatus(
    await api(alice, "POST", `${path}/revisions/from-source`, {
      headers: {
        "If-Match": draft.etag,
        "Idempotency-Key": `pptx-edit-source-${suffix}`,
      },
    }),
    201,
  );
  const targetsPath = `${path}/revisions/${base.id}/pptx-text-targets`;
  const listed = expectStatus(
    await api(alice, "GET", `${targetsPath}?limit=500`),
    200,
  );
  assert.equal(listed.download_sha256, sha256(sourceBytes));
  const title = listed.targets[0];
  const notes = listed.targets.find((item) => item.notes);
  assert.equal(title.slide, 1);
  assert.ok(notes, "The qualified deck has editable speaker notes");
  assert.equal((await api(bob, "GET", targetsPath)).status, 404);
  const ready = expectStatus(await api(alice, "GET", path), 200);
  const editPath = `${path}/revisions/${base.id}/pptx-edits`;
  const stale = await api(alice, "POST", editPath, {
    headers: {
      "If-Match": ready.etag,
      "Idempotency-Key": `pptx-edit-stale-${suffix}`,
    },
    json: {
      operations: [
        {
          op: "replace_text",
          target: title.target,
          expected_text: "Stale title",
          text: "x",
        },
      ],
    },
  });
  assert.equal(stale.status, 422, stale.text);
  assert.equal(stale.json.error.code, "COMPOSER_PPTX_EDIT_TARGET_CHANGED");
  const editedTitle = `Edited E2E deck ${suffix}`;
  const request = {
    operations: [
      {
        op: "replace_text",
        target: title.target,
        expected_text: title.text,
        text: editedTitle,
      },
      {
        op: "replace_text",
        target: notes.target,
        expected_text: notes.text,
        text: "Edited E2E note",
      },
    ],
  };
  const edited = expectStatus(
    await api(alice, "POST", editPath, {
      headers: {
        "If-Match": ready.etag,
        "Idempotency-Key": `pptx-edit-${suffix}`,
      },
      json: request,
    }),
    201,
  );
  assert.equal(edited.operation, `pptx_edit:${base.id}`);
  assert.equal(
    expectStatus(
      await api(alice, "POST", editPath, {
        headers: {
          "If-Match": ready.etag,
          "Idempotency-Key": `pptx-edit-${suffix}`,
        },
        json: request,
      }),
      201,
    ).id,
    edited.id,
  );
  const editedBytes = await exactOfficeBytes(alice, path, edited);
  assert.notDeepEqual(editedBytes, sourceBytes);
  const reread = expectStatus(
    await api(
      alice,
      "GET",
      `${path}/revisions/${edited.id}/pptx-text-targets?limit=500`,
    ),
    200,
  );
  assert.equal(reread.total, listed.total);
  assert.equal(reread.targets[0].text, editedTitle);
  assert.equal(
    reread.targets.find((item) => item.target === notes.target).text,
    "Edited E2E note",
  );
  await alice.goto(`${baseURL}/composer`, { waitUntil: "networkidle" });
  await alice
    .getByRole("combobox", { name: "Saved drafts" })
    .selectOption(draft.id);
  await alice
    .frameLocator(`iframe[title="PPTX revision ${edited.id} preview"]`)
    .locator("#document")
    .getByText(editedTitle, { exact: true })
    .waitFor();
  const afterEdit = expectStatus(await api(alice, "GET", path), 200);
  const restored = expectStatus(
    await api(alice, "POST", `${path}/revisions/${base.id}/restore`, {
      headers: {
        "If-Match": afterEdit.etag,
        "Idempotency-Key": `pptx-edit-restore-${suffix}`,
      },
    }),
    201,
  );
  assert.deepEqual(await exactOfficeBytes(alice, path, restored), sourceBytes);
}

async function settledStep(page, path, stepId) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const step = expectStatus(
      await api(page, "GET", `${path}/model-steps/${stepId}`),
      200,
    );
    if (step.status !== "running") {
      return step;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("Office edit model step did not settle");
}

async function modelProposedOfficeEdit(alice, connection, suffix) {
  const sourceBytes = await readFile(editableDocx);
  const draft = expectStatus(
    await api(alice, "POST", "/api/v1/composer/drafts", {
      upload: {
        bytes: Array.from(sourceBytes),
        filename: "model-edit.docx",
        type: docxMediaType,
        title: `Model-edited report ${suffix}`,
      },
    }),
    201,
  );
  const path = `/api/v1/composer/drafts/${draft.id}`;
  const base = expectStatus(
    await api(alice, "POST", `${path}/revisions/from-source`, {
      headers: {
        "If-Match": draft.etag,
        "Idempotency-Key": `office-step-source-${suffix}`,
      },
    }),
    201,
  );
  const stepPath = `${path}/revisions/${base.id}/office-edit-steps`;
  const request = (instruction) => ({
    connection_id: connection.id,
    approved_endpoint: providerEndpoint,
    approved_model: providerModel,
    instruction,
    targets: ["word/document.xml#t0"],
    max_output_tokens: 64,
  });
  const runStep = async (instruction, key) => {
    const ready = expectStatus(await api(alice, "GET", path), 200);
    const preview = expectStatus(
      await api(alice, "POST", `${stepPath}/preview`, {
        headers: { "If-Match": ready.etag },
        json: request(instruction),
      }),
      200,
    );
    assert.deepEqual(preview.targets, [
      { target: "word/document.xml#t0", text: "Fixture Document" },
    ]);
    assert.doesNotMatch(preview.transmitted_content, /Plain paragraph with/);
    const started = expectStatus(
      await api(alice, "POST", stepPath, {
        headers: { "If-Match": ready.etag, "Idempotency-Key": key },
        json: {
          ...request(instruction),
          preview_digest: preview.preview_digest,
        },
      }),
      202,
    );
    return settledStep(alice, path, started.id);
  };

  const misuse = await runStep(
    "Make it formal. E2E inject an undisclosed target.",
    `office-step-misuse-${suffix}`,
  );
  assert.equal(misuse.status, "failed");
  assert.equal(misuse.error_code, "provider_invalid");
  assert.equal(misuse.proposal_id, null);

  const step = await runStep("Make the title formal.", `office-step-${suffix}`);
  assert.equal(step.status, "completed", step.error_code ?? "");
  const proposal = expectStatus(
    await api(alice, "GET", `${path}/proposals/${step.proposal_id}`),
    200,
  );
  const proposed = JSON.parse(proposal.proposed_value);
  assert.equal(proposed.kind, "office_edit");
  assert.deepEqual(proposed.operations, [
    {
      op: "replace_text",
      target: "word/document.xml#t0",
      expected_text: "Fixture Document",
      text: "Model E2E title",
    },
  ]);
  const beforeDecision = expectStatus(await api(alice, "GET", path), 200);
  expectStatus(
    await api(alice, "POST", `${path}/proposals/${proposal.id}/decision`, {
      headers: { "If-Match": beforeDecision.etag },
      json: { state: "accepted" },
    }),
    200,
  );
  const reviewed = expectStatus(await api(alice, "GET", path), 200);
  const published = expectStatus(
    await api(alice, "POST", `${path}/proposals/${proposal.id}/office-edits`, {
      headers: {
        "If-Match": reviewed.etag,
        "Idempotency-Key": `office-step-publish-${suffix}`,
      },
    }),
    201,
  );
  assert.equal(published.operation, `office_edit_proposal:${proposal.id}`);
  assert.equal(published.model_identity, providerModel);
  assert.equal(published.provenance, `human:accepted:model-step:${step.id}`);
  await exactOfficeBytes(alice, path, published);
  const reread = expectStatus(
    await api(
      alice,
      "GET",
      `${path}/revisions/${published.id}/docx-text-targets?limit=1`,
    ),
    200,
  );
  assert.equal(reread.targets[0].text, "Model E2E title");
}

function runInstalled(command, args, input = "") {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      env: {
        HOME: "/work/home",
        LANG: "C.UTF-8",
        PATH: "/usr/bin:/bin",
        XDG_STATE_HOME: cliStateHome,
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    const stdout = [];
    const stderr = [];
    const timer = setTimeout(() => child.kill("SIGKILL"), 60_000);
    child.stdout.on("data", (chunk) => stdout.push(chunk));
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({
        code,
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: Buffer.concat(stderr).toString("utf8"),
      });
    });
    child.stdin.end(input);
  });
}

function lastJson(text) {
  const line = text.trim().split("\n").at(-1);
  return line ? JSON.parse(line) : null;
}

/** Bind an installed-CLI profile to the browser session of one account. */
async function cliProfile(page, name) {
  await mkdir(cliStateHome, { recursive: true, mode: 0o700 });
  // Secure cookies are omitted when filtering by an http URL, so read them all.
  const cookies = await page.context().cookies();
  const csrf = cookies.find((cookie) => cookie.name === csrfCookie);
  const session = cookies.find(
    (cookie) => cookie.httpOnly && cookie.name !== csrfCookie,
  );
  assert.ok(csrf && session, "browser session cookies are available");
  const saved = await runInstalled(
    cliPython,
    [
      "-c",
      [
        "import json, sys",
        "from markweave.cli.profiles import ProfileStore",
        "from markweave.cli.types import ConnectionProfile",
        "data = json.load(sys.stdin)",
        "ProfileStore().save(ConnectionProfile(data['name'], data['url'], data['session'], data['csrf']))",
      ].join("\n"),
    ],
    JSON.stringify({
      name,
      url: "http://127.0.0.1:8080",
      session: `${session.name}=${session.value}`,
      csrf: decodeURIComponent(csrf.value),
    }),
  );
  assert.equal(saved.code, 0, saved.stderr);
  return name;
}

async function cli(profileName, ...args) {
  const result = await runInstalled(cliBinary, [
    "--json",
    ...args,
    "--profile",
    profileName,
  ]);
  return {
    code: result.code,
    json: result.code === 0 ? lastJson(result.stdout) : null,
    error: result.code === 0 ? null : lastJson(result.stderr)?.error,
    stderr: result.stderr,
  };
}

function expectCli(result) {
  assert.equal(result.code, 0, result.stderr);
  return result.json;
}

async function privateFile(name, content) {
  await mkdir(cliFiles, { recursive: true, mode: 0o700 });
  const path = `${cliFiles}/${name}`;
  await writeFile(path, content, { mode: 0o600, flag: "wx" });
  return path;
}

async function officeDraft(page, name, suffix) {
  const draft = expectStatus(
    await api(page, "POST", "/api/v1/composer/drafts", {
      upload: {
        bytes: Array.from(await readFile(editableDocx)),
        filename: `${name}.docx`,
        type: docxMediaType,
        title: `${name} ${suffix}`,
      },
    }),
    201,
  );
  const path = `/api/v1/composer/drafts/${draft.id}`;
  const base = expectStatus(
    await api(page, "POST", `${path}/revisions/from-source`, {
      headers: {
        "If-Match": draft.etag,
        "Idempotency-Key": `${name}-source-${suffix}`,
      },
    }),
    201,
  );
  return { draft, path, base };
}

async function currentOfficeRevision(page, path) {
  const draft = expectStatus(await api(page, "GET", path), 200);
  return expectStatus(
    await api(page, "GET", `${path}/revisions/${draft.current_revision_id}`),
    200,
  );
}

async function officeTitle(page, path, revisionId) {
  return expectStatus(
    await api(
      page,
      "GET",
      `${path}/revisions/${revisionId}/docx-text-targets?limit=1`,
    ),
    200,
  ).targets[0].text;
}

/** Edit, reject a stale edit, and publish a model proposal through the panel. */
async function officePanelJourney(alice, connection, suffix) {
  const { draft, path } = await officeDraft(alice, "panel-edit", suffix);
  const stale = await alice.context().newPage();
  try {
    for (const page of [alice, stale]) {
      await page.goto(`${baseURL}/composer?draft=${draft.id}`, {
        waitUntil: "networkidle",
      });
    }
    const panel = alice.getByRole("region", { name: "Office text editing" });
    await panel
      .getByRole("textbox", { name: /: Fixture Document$/ })
      .fill("Panel E2E title");
    await panel.getByRole("button", { name: "Publish text edits (1)" }).click();
    await panel.getByText(/Text edits published as revision \d+\./).waitFor();
    const edited = await currentOfficeRevision(alice, path);
    assert.match(edited.operation, /^docx_edit:/);
    assert.equal(await officeTitle(alice, path, edited.id), "Panel E2E title");
    await exactOfficeBytes(alice, path, edited);

    const stalePanel = stale.getByRole("region", {
      name: "Office text editing",
    });
    await stalePanel
      .getByRole("textbox", { name: /: Fixture Document$/ })
      .fill("Stale browser title");
    await stalePanel
      .getByRole("button", { name: "Publish text edits (1)" })
      .click();
    await stalePanel
      .getByText(
        "The draft or document changed. Reload the current revision before retrying.",
      )
      .waitFor();
    assert.equal((await currentOfficeRevision(alice, path)).id, edited.id);
  } finally {
    await stale.close();
  }

  await alice
    .getByRole("combobox", { name: "Connection" })
    .selectOption(connection.id);
  await alice
    .getByRole("spinbutton", { name: /Maximum output tokens/ })
    .fill("64");
  const panel = alice.getByRole("region", { name: "Office text editing" });
  await panel
    .getByRole("checkbox", { name: "Share “Panel E2E title” with the model" })
    .check();
  await panel
    .getByRole("textbox", { name: "Instruction for the model" })
    .fill("Make the title formal.");
  await panel.getByRole("button", { name: "Preview model request" }).click();
  await panel.getByText("Exact text selected for transmission:").waitFor();
  await panel
    .getByRole("button", { name: "Send shared text to the model" })
    .click();
  await panel
    .getByText("The model suggestion is ready for review in the proposals list.")
    .waitFor({ timeout: 30_000 });
  const pending = alice
    .locator("details")
    .filter({ hasText: "Suggestion · pending" })
    .last();
  await pending.locator("summary").waitFor({ timeout: 15_000 });
  await pending.locator("summary").click();
  await pending
    .getByRole("list", { name: "Proposed text changes" })
    .getByText(/Panel E2E title.*Model E2E title/)
    .waitFor();
  await pending.getByRole("button", { name: "Accept" }).click();
  await alice.getByText(/Your decision was saved/).waitFor();
  await alice
    .getByRole("button", { name: "Publish approved text edits" })
    .click();
  await alice.getByText(/Approved text edits published/).waitFor();
  const proposed = await currentOfficeRevision(alice, path);
  assert.match(proposed.operation, /^office_edit_proposal:/);
  assert.equal(proposed.model_identity, providerModel);
  assert.equal(await officeTitle(alice, path, proposed.id), "Model E2E title");
  await exactOfficeBytes(alice, path, proposed);
}

async function cliBytes(profileName, path, draftId, revisionId, kind) {
  const output = `${cliFiles}/${revisionId}-${kind}.docx`;
  expectCli(
    await cli(
      profileName,
      "composer",
      "revisions",
      "download",
      draftId,
      revisionId,
      "--kind",
      kind,
      "--output",
      output,
    ),
  );
  return readFile(output);
}

/** Drive targets, edit, stale/foreign refusal, model proposal and publication. */
async function officeCliJourney(alice, bob, connection, suffix) {
  const aliceCli = await cliProfile(alice, `office-alice-${profile}`);
  const bobCli = await cliProfile(bob, `office-bob-${profile}`);
  const created = expectCli(
    await cli(
      aliceCli,
      "composer",
      "drafts",
      "create",
      editableDocx,
      "--title",
      `CLI Office ${suffix}`,
    ),
  ).draft;
  const path = `/api/v1/composer/drafts/${created.id}`;
  const base = expectCli(
    await cli(
      aliceCli,
      "composer",
      "revisions",
      "capture",
      created.id,
      "--etag",
      created.etag,
      "--idempotency-key",
      `cli-office-source-${suffix}`,
    ),
  ).revision;
  const targets = expectCli(
    await cli(
      aliceCli,
      "composer",
      "office",
      "targets",
      created.id,
      base.id,
      "--format",
      "docx",
    ),
  ).targets;
  assert.deepEqual(
    { target: targets[0].target, text: targets[0].text },
    { target: "word/document.xml#t0", text: "Fixture Document" },
  );
  const foreign = await cli(
    bobCli,
    "composer",
    "office",
    "targets",
    created.id,
    base.id,
    "--format",
    "docx",
  );
  assert.notEqual(foreign.code, 0);
  assert.match(foreign.error?.code ?? "", /not_found/);

  const operations = await privateFile(
    `operations-${suffix}.json`,
    JSON.stringify([
      {
        target: "word/document.xml#t0",
        expected_text: "Fixture Document",
        text: "CLI E2E title",
      },
    ]),
  );
  const ready = expectStatus(await api(alice, "GET", path), 200);
  const edit = (etag, key) =>
    cli(
      aliceCli,
      "composer",
      "office",
      "edit",
      created.id,
      base.id,
      "--format",
      "docx",
      "--operations-file",
      operations,
      "--etag",
      etag,
      "--idempotency-key",
      key,
    );
  const edited = expectCli(await edit(ready.etag, `cli-office-edit-${suffix}`))
    .revision;
  assert.match(edited.operation, /^docx_edit:/);
  const staleEdit = await edit(ready.etag, `cli-office-stale-${suffix}`);
  assert.notEqual(staleEdit.code, 0);
  assert.match(staleEdit.error?.code ?? "", /precondition/);
  const editedRevision = await currentOfficeRevision(alice, path);
  assert.equal(editedRevision.id, edited.id);
  const [download, preview] = await Promise.all([
    cliBytes(aliceCli, path, created.id, edited.id, "download"),
    cliBytes(aliceCli, path, created.id, edited.id, "preview"),
  ]);
  assert.equal(
    sha256(download),
    editedRevision.artifacts.find((item) => item.kind === "download")?.sha256,
  );
  assert.equal(
    sha256(preview),
    editedRevision.artifacts.find((item) => item.kind === "preview")?.sha256,
  );
  assert.deepEqual(download, await exactOfficeBytes(alice, path, editedRevision));
  assert.equal(await officeTitle(alice, path, edited.id), "CLI E2E title");

  const instruction = await privateFile(
    `instruction-${suffix}.txt`,
    "Make the title formal.",
  );
  const beforeStep = expectStatus(await api(alice, "GET", path), 200);
  let step = expectCli(
    await cli(
      aliceCli,
      "composer",
      "office",
      "propose",
      created.id,
      edited.id,
      connection.id,
      "--target",
      "word/document.xml#t0",
      "--instruction-file",
      instruction,
      "--max-output-tokens",
      "64",
      "--etag",
      beforeStep.etag,
      "--idempotency-key",
      `cli-office-step-${suffix}`,
      "--force",
    ),
  ).model_step;
  for (let attempt = 0; step.status === "running" && attempt < 60; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    step = expectCli(
      await cli(aliceCli, "composer", "model-steps", "status", created.id, step.id),
    ).model_step;
  }
  assert.equal(step.status, "completed", step.error_code ?? "");
  const beforeDecision = expectStatus(await api(alice, "GET", path), 200);
  expectCli(
    await cli(
      aliceCli,
      "composer",
      "proposals",
      "decide",
      created.id,
      step.proposal_id,
      "--state",
      "accepted",
      "--etag",
      beforeDecision.etag,
    ),
  );
  const reviewed = expectStatus(await api(alice, "GET", path), 200);
  const published = expectCli(
    await cli(
      aliceCli,
      "composer",
      "office",
      "publish",
      created.id,
      step.proposal_id,
      "--etag",
      reviewed.etag,
      "--idempotency-key",
      `cli-office-publish-${suffix}`,
    ),
  ).revision;
  assert.equal(published.operation, `office_edit_proposal:${step.proposal_id}`);
  assert.equal(await officeTitle(alice, path, published.id), "Model E2E title");
  const publishedRevision = await currentOfficeRevision(alice, path);
  assert.equal(
    sha256(await cliBytes(aliceCli, path, created.id, published.id, "download")),
    publishedRevision.artifacts.find((item) => item.kind === "download")?.sha256,
  );
}

test(`typed Composer shares, human decisions, and exact regeneration in ${profile}`, async () => {
  const suffix = `${profile}-${Date.now()}`;
  const [templateBytes, schema] = await Promise.all([
    readFile(typedDocx),
    readFile(typedSchema, "utf8"),
  ]);
  assert.ok(templateBytes.length > 1000);
  const browser = await chromium.launch({
    executablePath:
      process.env.MARKWEAVE_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
    headless: true,
  });
  const contexts = await Promise.all(
    [0, 1, 2].map(() =>
      browser.newContext({ baseURL, serviceWorkers: "block" }),
    ),
  );
  const [admin, alice, bob] = await Promise.all(
    contexts.map((context) => context.newPage()),
  );
  try {
    await login(admin, "e2e-admin", "e2e-admin-password");
    const newUser = async (name) => {
      const password = `Composer-${name}-password`;
      const user = expectStatus(
        await api(admin, "POST", "/api/v1/admin/users", {
          json: {
            username: name,
            password,
            password_change_required: false,
          },
        }),
        201,
      );
      return { ...user, password };
    };
    const aliceUser = await newUser(`typed-alice-${suffix}`);
    const bobUser = await newUser(`typed-bob-${suffix}`);
    await Promise.all([
      login(alice, aliceUser.username, aliceUser.password),
      login(bob, bobUser.username, bobUser.password),
    ]);
    const connection = expectStatus(
      await api(admin, "POST", "/api/v1/composer/connections", {
        json: {
          scope: "instance",
          identity_mode: "shared",
          name: `Typed filling ${suffix}`,
          endpoint: providerEndpoint,
          permitted_models: [providerModel],
          allowed_user_ids: [aliceUser.id],
          selected_model: null,
          enabled: false,
        },
      }),
      201,
    );
    const connectionPath = `/api/v1/composer/connections/${connection.id}`;
    const credential = expectStatus(
      await api(admin, "PUT", `${connectionPath}/credentials`, {
        headers: { "If-Match": connection.etag },
        json: {
          api_key: providerSecret,
          internal_ca: await readFile("/run/composer-e2e-ca.crt", "utf8"),
        },
      }),
      200,
    );
    expectStatus(
      await api(admin, "PATCH", connectionPath, {
        headers: { "If-Match": credential.etag },
        json: { enabled: true, selected_model: providerModel },
      }),
      200,
    );
    expectStatus(
      await api(alice, "POST", `${connectionPath}/test`, { json: {} }),
      200,
    );

    const authorName = `Ada Lovelace ${suffix}`;
    let author = expectStatus(
      await api(admin, "POST", "/api/v1/composer/authors", {
        json: {
          name: authorName,
          fields: {
            role: { value: "Reviewer", provenance: "supplied" },
          },
        },
      }),
      201,
    );
    const authorPath = `/api/v1/composer/authors/${author.id}`;
    const templateCount = expectStatus(
      await api(admin, "GET", "/api/v1/composer/fill-templates"),
      200,
    ).templates.length;
    const infected = await api(
      admin,
      "POST",
      "/api/v1/composer/fill-templates",
      {
        typed: {
          name: `Rejected typed decision ${suffix}`,
          schema,
          bytes: Array.from(
            Buffer.concat([templateBytes, Buffer.from(malwareMarker)]),
          ),
        },
      },
    );
    assert.equal(infected.status, 422);
    assert.equal(infected.json.error.code, "UPLOAD_MALWARE_DETECTED");
    assert.equal(
      expectStatus(
        await api(admin, "GET", "/api/v1/composer/fill-templates"),
        200,
      ).templates.length,
      templateCount,
    );
    const malformedSchema = await api(
      admin,
      "POST",
      "/api/v1/composer/fill-templates",
      {
        typed: {
          name: `Malformed typed decision ${suffix}`,
          schema: '{"fields":"not-an-array","repeats":[]}',
          bytes: Array.from(templateBytes),
        },
      },
    );
    assert.equal(malformedSchema.status, 422);
    assert.equal(
      expectStatus(
        await api(admin, "GET", "/api/v1/composer/fill-templates"),
        200,
      ).templates.length,
      templateCount,
    );
    let template = expectStatus(
      await api(admin, "POST", "/api/v1/composer/fill-templates", {
        typed: {
          name: `Typed decision ${suffix}`,
          schema,
          bytes: Array.from(templateBytes),
        },
      }),
      201,
    );
    const templatePath = `/api/v1/composer/fill-templates/${template.id}`;
    const versionPath = `${templatePath}/versions/${template.active_version_id}`;
    const version = expectStatus(await api(admin, "GET", versionPath), 200);
    assert.equal(version.docx_sha256, sha256(templateBytes));
    assert.equal(version.schema_version, 1);
    assert.deepEqual(version.schema, JSON.parse(schema));
    const original = await admin.request.get(`${versionPath}/content`);
    assert.equal(original.status(), 200);
    assert.deepEqual(await original.body(), templateBytes);

    for (const page of [alice, bob]) {
      assert.equal((await api(page, "GET", authorPath)).status, 404);
      assert.equal((await api(page, "GET", templatePath)).status, 404);
      assert.equal(
        (await api(page, "GET", `${versionPath}/content`)).status,
        404,
      );
    }
    author = expectStatus(
      await api(admin, "PUT", `${authorPath}/grants/${aliceUser.id}`, {
        headers: { "If-Match": author.etag },
      }),
      200,
    );
    template = expectStatus(
      await api(admin, "PUT", `${templatePath}/grants/${aliceUser.id}`, {
        headers: { "If-Match": template.etag },
      }),
      200,
    );
    assert.equal((await api(alice, "GET", authorPath)).status, 200);
    assert.equal((await api(alice, "GET", versionPath)).status, 200);
    assert.equal((await api(bob, "GET", authorPath)).status, 404);
    assert.equal((await api(bob, "GET", templatePath)).status, 404);
    await alice.goto(`${baseURL}/composer/authors`);
    await alice.getByRole("heading", { name: "Author directory" }).waitFor();
    await alice
      .getByRole("button", { name: `${authorName} (shared)` })
      .waitFor();
    await alice.goto(`${baseURL}/composer/fill-templates`);
    await alice
      .getByRole("heading", { name: "Typed filling templates" })
      .waitFor();
    await alice
      .getByRole("button", { name: `${template.name} (shared)` })
      .waitFor();

    const browserDraft = await createDraftWithSource(
      alice,
      `${suffix}-browser`,
    );
    await alice.goto(`${baseURL}/composer`, { waitUntil: "networkidle" });
    await alice
      .getByRole("combobox", { name: "Saved drafts" })
      .selectOption(browserDraft.current.id);
    await alice
      .getByRole("combobox", { name: "Connection" })
      .selectOption(connection.id);
    await alice
      .getByRole("combobox", { name: "Model", exact: true })
      .selectOption(providerModel);
    await alice
      .getByRole("textbox", { name: "Exact approved text for transmission" })
      .fill("Review the named author facts for this report.");
    await alice
      .getByRole("spinbutton", { name: /Maximum output tokens/ })
      .fill("128");
    await alice
      .getByRole("group", { name: "Author entries for this model step" })
      .getByRole("checkbox", { name: authorName })
      .check();
    const [browserPromptResponse] = await Promise.all([
      alice.waitForResponse(
        (response) =>
          response.url().endsWith(`${browserDraft.path}/model-steps/preview`) &&
          response.request().method() === "POST",
      ),
      alice.getByRole("button", { name: "Preview exact prompt" }).click(),
    ]);
    assert.equal(browserPromptResponse.status(), 200);
    const browserPrompt = await browserPromptResponse.json();
    assert.match(
      browserPrompt.transmitted_content,
      /Review the named author facts/,
    );
    assert.match(browserPrompt.transmitted_content, new RegExp(authorName));
    assert.deepEqual(browserPrompt.author_refs, [
      { id: author.id, version: author.version },
    ]);
    await alice.getByText("Exact text selected for transmission:").waitFor();
    const [browserStepResponse] = await Promise.all([
      alice.waitForResponse(
        (response) =>
          response.url().endsWith(`${browserDraft.path}/model-steps`) &&
          response.request().method() === "POST",
      ),
      alice.getByRole("button", { name: "Send reviewed text" }).click(),
    ]);
    assert.equal(browserStepResponse.status(), 202);
    const browserStep = await browserStepResponse.json();
    assert.equal(browserStep.connection_id, connection.id);
    await alice.getByText("Model step queued for review.").waitFor();
    let completedStep = null;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const observed = expectStatus(
        await api(
          alice,
          "GET",
          `${browserDraft.path}/model-steps/${browserStep.id}`,
        ),
        200,
      );
      if (observed.status === "completed") {
        completedStep = observed;
        break;
      }
      assert.notEqual(
        observed.status,
        "failed",
        observed.error_code ?? "Model step failed",
      );
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    assert.ok(
      completedStep,
      "Authorized author step did not complete through the real provider",
    );
    assert.ok(completedStep.proposal_id);
    await alice
      .locator("details")
      .filter({ hasText: /Suggestion step completed/ })
      .locator("summary")
      .waitFor();
    await alice.reload({ waitUntil: "networkidle" });
    assert.equal(
      await alice.getByRole("combobox", { name: "Saved drafts" }).inputValue(),
      browserDraft.current.id,
    );

    const fillPanel = alice.getByRole("region", {
      name: "Typed document filling",
    });
    await fillPanel
      .getByRole("combobox", { name: "Typed filling template" })
      .selectOption(template.id);
    await fillPanel
      .getByRole("group", { name: "Authorized authors to include" })
      .getByRole("checkbox", { name: authorName })
      .check();
    await fillPanel.getByLabel("finding.count").fill("1");
    await fillPanel.getByRole("button", { name: "Add findings row" }).click();
    await fillPanel
      .getByLabel("findings[0].title")
      .fill("A browser-reviewed finding");
    await fillPanel.getByLabel("findings[0].score").fill("73");
    await fillPanel.getByRole("button", { name: "Save fill plan" }).click();
    await fillPanel.getByText(/Fill plan saved/).waitFor();
    await fillPanel.getByText(/decision.date:.*missing/).waitFor();
    await fillPanel.getByText(/decision.approved:.*missing/).waitFor();
    await fillPanel.getByLabel("decision.date").fill("2026-09-24");
    await fillPanel.getByLabel("decision.approved").selectOption("true");
    await fillPanel
      .getByRole("button", { name: "Save answers and edits" })
      .click();
    await fillPanel
      .getByText("No missing or ambiguous values remain.")
      .waitFor();
    await fillPanel
      .getByRole("button", { name: "Approve reviewed values" })
      .click();
    await fillPanel
      .getByText("Approved values are frozen for this plan.")
      .waitFor();
    await fillPanel
      .getByRole("button", { name: "Fill DOCX from approved values" })
      .click();
    await fillPanel.getByText(/Filled DOCX published as revision/).waitFor();
    const browserFill = expectStatus(
      await api(alice, "GET", `${browserDraft.path}/fill-plans`),
      200,
    ).plans[0];
    assert.equal(browserFill.state, "published");
    assert.equal(browserFill.values["decision.approved"], true);
    assert.equal(
      browserFill.values.findings[0].title,
      "A browser-reviewed finding",
    );
    const publishedFrame = alice.frameLocator('iframe[title^="DOCX revision"]');
    await publishedFrame.getByText("A browser-reviewed finding").waitFor();
    assert.match(
      await publishedFrame.locator("body").innerText(),
      /A browser-reviewed finding/,
    );

    const { path, source, current } = await createDraftWithSource(
      alice,
      suffix,
    );
    const planPath = `${path}/fill-plans`;
    let plan = expectStatus(
      await api(alice, "POST", planPath, {
        headers: {
          "If-Match": current.etag,
          "Idempotency-Key": `typed-plan-${suffix}`,
        },
        json: {
          source_revision_id: source.id,
          template_id: template.id,
          template_version_id: template.active_version_id,
          author_ids: [author.id],
          values: {
            "finding.count": 1,
            findings: [{ title: "A reviewed finding", score: 73 }],
          },
        },
      }),
      201,
    );
    assert.equal(plan.state, "pending");
    assert.equal(plan.values["author.name"], authorName);
    assert.deepEqual(plan.questions.map((question) => question.path).sort(), [
      "decision.approved",
      "decision.date",
    ]);
    assert.deepEqual(plan.author_refs, [
      { id: author.id, version: author.version },
    ]);
    const decisionPath = `${planPath}/${plan.id}`;
    assert.equal(
      (
        await api(alice, "POST", `${decisionPath}/approve`, {
          headers: {
            "If-Match": plan.etag,
            "Idempotency-Key": `typed-early-approval-${suffix}`,
          },
        })
      ).status,
      412,
    );
    const pendingEtag = plan.etag;
    plan = expectStatus(
      await api(alice, "PATCH", decisionPath, {
        headers: {
          "If-Match": pendingEtag,
          "Idempotency-Key": `typed-decision-${suffix}`,
        },
        json: {
          values: {
            ...plan.values,
            "decision.date": "2026-09-24",
            "decision.approved": true,
          },
          provenance: {
            ...plan.provenance,
            "decision.date": { kind: "human_edited" },
            "decision.approved": { kind: "human_edited" },
          },
        },
      }),
      200,
    );
    assert.deepEqual(plan.questions, []);
    assert.equal(
      (
        await api(alice, "PATCH", decisionPath, {
          headers: {
            "If-Match": pendingEtag,
            "Idempotency-Key": `typed-stale-${suffix}`,
          },
          json: { values: plan.values, provenance: plan.provenance },
        })
      ).status,
      412,
    );
    plan = expectStatus(
      await api(alice, "POST", `${decisionPath}/approve`, {
        headers: {
          "If-Match": plan.etag,
          "Idempotency-Key": `typed-approval-${suffix}`,
        },
      }),
      200,
    );
    assert.equal(plan.state, "approved");
    const published = expectStatus(
      await api(alice, "POST", `${decisionPath}/publish`, {
        headers: {
          "If-Match": plan.etag,
          "Idempotency-Key": `typed-publish-${suffix}`,
        },
      }),
      201,
    );
    assert.equal(published.operation, "fill_template");
    assert.equal(published.model_identity, null);
    assert.equal(
      expectStatus(
        await api(alice, "POST", `${decisionPath}/publish`, {
          headers: {
            "If-Match": plan.etag,
            "Idempotency-Key": `typed-publish-${suffix}`,
          },
        }),
        201,
      ).id,
      published.id,
    );
    assert.equal(
      (
        await api(alice, "POST", `${decisionPath}/publish`, {
          headers: {
            "If-Match": plan.etag,
            "Idempotency-Key": `typed-publish-different-${suffix}`,
          },
        })
      ).status,
      412,
    );
    const frozen = JSON.parse(published.typed_fill_snapshot);
    assert.equal(frozen.template_version_id, template.active_version_id);
    assert.equal(frozen.template_docx_sha256, version.docx_sha256);
    assert.equal(frozen.fill_plan_id, plan.id);
    assert.equal(frozen.approved_values["author.name"], authorName);
    assert.equal(frozen.approved_values["decision.date"], "2026-09-24");
    assert.equal(frozen.approved_values["decision.approved"], true);
    assert.equal(frozen.provenance["decision.date"].kind, "human_edited");
    const firstBytes = await exactOfficeBytes(alice, path, published);
    assert.equal(frozen.result_sha256, sha256(firstBytes));
    assert.equal(
      (await api(bob, "GET", `${path}/revisions/${published.id}`)).status,
      404,
    );
    const afterPublish = expectStatus(await api(alice, "GET", path), 200);
    const regenerated = expectStatus(
      await api(
        alice,
        "POST",
        `${path}/revisions/${published.id}/regenerations`,
        {
          headers: {
            "If-Match": afterPublish.etag,
            "Idempotency-Key": `typed-regenerate-${suffix}`,
          },
        },
      ),
      201,
    );
    assert.equal(regenerated.operation, "regenerate_fill");
    assert.equal(regenerated.model_identity, null);
    assert.deepEqual(
      await exactOfficeBytes(alice, path, regenerated),
      firstBytes,
    );
    assert.equal(
      (
        await api(
          alice,
          "POST",
          `${path}/revisions/${published.id}/regenerations`,
          {
            headers: {
              "If-Match": afterPublish.etag,
              "Idempotency-Key": `typed-stale-regenerate-${suffix}`,
            },
          },
        )
      ).status,
      412,
    );

    const second = await createDraftWithSource(alice, `${suffix}-revoked`);
    const revocablePlan = expectStatus(
      await api(alice, "POST", `${second.path}/fill-plans`, {
        headers: {
          "If-Match": second.current.etag,
          "Idempotency-Key": `typed-revocable-${suffix}`,
        },
        json: {
          source_revision_id: second.source.id,
          template_id: template.id,
          template_version_id: template.active_version_id,
          author_ids: [author.id],
          values: {},
        },
      }),
      201,
    );
    const secondAfterPlan = expectStatus(
      await api(alice, "GET", second.path),
      200,
    );
    const revokedPreview = expectStatus(
      await api(alice, "POST", `${second.path}/model-steps/preview`, {
        headers: { "If-Match": secondAfterPlan.etag },
        json: {
          connection_id: connection.id,
          approved_endpoint: providerEndpoint,
          approved_model: providerModel,
          content: "Use selected author facts.",
          author_ids: [author.id],
          max_output_tokens: 8,
        },
      }),
      200,
    );
    assert.deepEqual(revokedPreview.author_refs, revocablePlan.author_refs);
    author = expectStatus(
      await api(admin, "DELETE", `${authorPath}/grants/${aliceUser.id}`, {
        headers: { "If-Match": author.etag },
      }),
      200,
    );
    template = expectStatus(
      await api(admin, "DELETE", `${templatePath}/grants/${aliceUser.id}`, {
        headers: { "If-Match": template.etag },
      }),
      200,
    );
    assert.equal((await api(alice, "GET", authorPath)).status, 404);
    assert.equal((await api(alice, "GET", templatePath)).status, 404);
    assert.equal(
      (await api(alice, "GET", `${versionPath}/content`)).status,
      404,
    );
    assert.equal(
      (await api(alice, "GET", `${second.path}/fill-plans/${revocablePlan.id}`))
        .status,
      412,
    );
    assert.equal(
      (
        await api(
          alice,
          "POST",
          `${second.path}/fill-plans/${revocablePlan.id}/approve`,
          {
            headers: {
              "If-Match": revocablePlan.etag,
              "Idempotency-Key": `typed-revoked-${suffix}`,
            },
          },
        )
      ).status,
      412,
    );
    const revokedDraft = expectStatus(
      await api(alice, "GET", second.path),
      200,
    );
    assert.equal(
      (
        await api(alice, "POST", `${second.path}/model-steps`, {
          headers: {
            "If-Match": revokedDraft.etag,
            "Idempotency-Key": `typed-revoked-model-${suffix}`,
          },
          json: {
            connection_id: connection.id,
            approved_endpoint: "https://e2e-llm:8443/v1",
            approved_model: "composer-e2e-model",
            content: "Use selected author facts.",
            max_output_tokens: 8,
            author_refs: revokedPreview.author_refs,
            author_preview_digest: revokedPreview.preview_digest,
          },
        })
      ).status,
      412,
    );
    assert.equal((await api(admin, "GET", authorPath)).status, 200);
    assert.equal((await api(admin, "GET", templatePath)).status, 200);
    await docxTextEdits(alice, bob, suffix);
    await pptxTextEdits(alice, bob, suffix);
    await modelProposedOfficeEdit(alice, connection, suffix);
    await officePanelJourney(alice, connection, suffix);
    await officeCliJourney(alice, bob, connection, suffix);
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
    await browser.close();
  }
});
