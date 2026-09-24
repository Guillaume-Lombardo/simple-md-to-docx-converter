import assert from "node:assert/strict";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";

import { chromium } from "playwright-core";

const baseURL = process.env.MARKWEAVE_E2E_BASE_URL || "http://localhost:3100";
const mediaType = {
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  pdf: "application/pdf",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
};

async function login(page) {
  await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
  await page.getByRole("textbox", { name: "Username" }).fill("e2e-admin");
  await page.getByLabel("Password").fill("e2e-admin-password");
  await Promise.all([
    page.waitForURL("**/convert"),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);
}

async function request(page, method, path, options = {}) {
  const response = await page.evaluate(
    async ({ method, path, options }) => {
      const csrf = document.cookie
        .split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith("__Host-md_converter_csrf="))
        ?.split("=", 2)[1];
      const headers = {};
      if (method !== "GET" && csrf)
        headers["X-CSRF-Token"] = decodeURIComponent(csrf);
      if (options.etag) headers["If-Match"] = options.etag;
      if (options.key) headers["Idempotency-Key"] = options.key;
      let body;
      if (options.source) {
        const form = new FormData();
        form.set(
          "source",
          new File([options.source], options.filename, {
            type: "text/markdown",
          }),
        );
        body = form;
      } else if (options.json) {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(options.json);
      }
      const result = await fetch(path, {
        method,
        body,
        headers,
        credentials: "same-origin",
        cache: "no-store",
      });
      const text = await result.text();
      let json;
      try {
        json = JSON.parse(text);
      } catch {
        json = null;
      }
      return { status: result.status, text, json };
    },
    { method, path, options },
  );
  assert.ok(
    response.status >= 200 && response.status < 300,
    `${method} ${path}: HTTP ${response.status} ${response.text}`,
  );
  return response.json;
}

async function createGeneratedRevisions(page) {
  const filename = `pairing-${crypto.randomUUID()}.md`;
  const draft = await request(page, "POST", "/api/v1/composer/drafts", {
    filename,
    source: "# Paired preview\n\nOne stable paragraph.\n",
  });
  const path = `/api/v1/composer/drafts/${draft.id}`;
  const generated = { docx: [], pdf: [], pptx: [] };
  for (const output of ["docx", "docx", "pdf", "pptx"]) {
    const current = await request(page, "GET", path);
    const source = await request(page, "POST", `${path}/revisions/from-draft`, {
      etag: current.etag,
      key: crypto.randomUUID(),
    });
    const updated = await request(page, "GET", path);
    const job = await request(
      page,
      "POST",
      `${path}/revisions/${source.id}/generations`,
      {
        etag: updated.etag,
        key: crypto.randomUUID(),
        json: {
          output,
          template_id: null,
          template_version_id: null,
          presentation_dialect: output === "pptx" ? "auto" : null,
          slide_level: output === "pptx" ? 2 : null,
        },
      },
    );
    let result;
    const deadline = Date.now() + 90_000;
    while (Date.now() < deadline) {
      const status = await request(
        page,
        "GET",
        `${path}/generations/${job.id}`,
      );
      if (status.result_revision_id) {
        result = await request(
          page,
          "GET",
          `${path}/revisions/${status.result_revision_id}`,
        );
        break;
      }
      if (status.status === "succeeded" && status.publishable) {
        const fresh = await request(page, "GET", path);
        result = await request(
          page,
          "POST",
          `${path}/generations/${job.id}/publish`,
          { etag: fresh.etag, key: crypto.randomUUID() },
        );
        break;
      }
      assert.ok(
        !["failed", "cancelled", "expired"].includes(status.status),
        `Generation ${job.id} ended as ${status.status}`,
      );
      await delay(200);
    }
    assert.ok(result, `${output} generation did not finish`);
    assert.ok(
      result.artifacts.some(
        (artifact) =>
          artifact.kind === "preview" &&
          artifact.media_type === mediaType[output],
      ),
      `${output} result has no exact preview artifact`,
    );
    generated[output].push(result.id);
  }
  return { draftId: draft.id, generated };
}

function artifact(draftId, revisionId, kind) {
  return `/api/v1/composer/drafts/${draftId}/revisions/${revisionId}/artifacts/${kind}`;
}

async function assertDisplayed(page, draftId, selectedId, visibleId, format) {
  await page
    .getByText(new RegExp(`Selected revision \\d+ · ${selectedId}`))
    .waitFor();
  await page
    .getByText(
      new RegExp(`Displaying ${format.toUpperCase()} revision ${visibleId}`),
    )
    .waitFor();
  await page
    .getByText(`${format.toUpperCase()} revision ${visibleId} preview`, {
      exact: true,
    })
    .waitFor();
  const visibleDownloads = page
    .locator("a:visible, button:visible")
    .filter({ hasText: /Download.*revision/i });
  assert.equal(await visibleDownloads.count(), 1);
  assert.equal(
    await visibleDownloads.first().evaluate((element) => element.tagName),
    "BUTTON",
  );
  assert.equal(
    (await visibleDownloads.first().textContent()).trim(),
    "Download this revision",
  );
  assert.equal(await visibleDownloads.first().getAttribute("href"), null);
  assert.equal(
    await page
      .locator(`a[href="${artifact(draftId, visibleId, "download")}"]`)
      .count(),
    0,
  );
}

async function assertPairedDownload(page, draftId, revisionId, format) {
  const path = artifact(draftId, revisionId, "download");
  const [response, browserDownload] = await Promise.all([
    page.waitForResponse(
      (candidate) =>
        new URL(candidate.url()).pathname === path &&
        candidate.request().method() === "GET",
    ),
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Download this revision" }).click(),
  ]);
  assert.equal(response.status(), 200);
  assert.equal(
    browserDownload.suggestedFilename(),
    `revision-${revisionId}.${format}`,
  );
}

async function holdArtifact(page, path, fail = false) {
  let startedResolve;
  let releaseResolve;
  const started = new Promise((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error(`The preview never requested ${path}`)),
      15_000,
    );
    startedResolve = () => {
      clearTimeout(timeout);
      resolve();
    };
  });
  const released = new Promise((resolve) => {
    releaseResolve = resolve;
  });
  const match = (url) => url.pathname === path;
  const handler = async (route) => {
    startedResolve();
    await released;
    if (fail)
      await route.fulfill({
        status: 200,
        contentType: "text/plain",
        body: "wrong artifact type",
      });
    else await route.continue();
  };
  await page.route(match, handler);
  return {
    started,
    release: () => releaseResolve(),
    remove: () => page.unroute(match, handler),
  };
}

test(
  "whole Composer workspace keeps selected and displayed revisions paired across formats and failures",
  { timeout: 600_000 },
  async () => {
    assert.ok(
      ["standalone", "distributed"].includes(process.env.MARKWEAVE_E2E_PROFILE),
    );
    const browser = await chromium.launch({
      executablePath:
        process.env.MARKWEAVE_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
      headless: true,
    });
    const context = await browser.newContext({
      baseURL,
      serviceWorkers: "block",
    });
    const page = await context.newPage();
    const externalRequests = new Set();
    await page.route("**/*", (route) => {
      const url = route.request().url();
      if (new URL(url).origin === new URL(baseURL).origin)
        return route.continue();
      externalRequests.add(url);
      return route.abort();
    });
    try {
      await login(page);
      const { draftId, generated } = await createGeneratedRevisions(page);
      const [docxA, docxB] = generated.docx;
      const [pdf] = generated.pdf;
      const [pptx] = generated.pptx;
      await page.goto(`${baseURL}/composer?draft=${draftId}`);
      const history = page.getByRole("combobox", { name: "Revision history" });
      await history.selectOption(docxA);
      await assertDisplayed(page, draftId, docxA, docxA, "docx");

      const sameFormat = await holdArtifact(
        page,
        artifact(draftId, docxB, "preview"),
      );
      await history.selectOption(docxB);
      await sameFormat.started;
      await assertDisplayed(page, draftId, docxB, docxA, "docx");
      await assertPairedDownload(page, draftId, docxA, "docx");
      await page.getByText(/Preparing revision/).waitFor();
      sameFormat.release();
      await assertDisplayed(page, draftId, docxB, docxB, "docx");
      await assertPairedDownload(page, draftId, docxB, "docx");
      await sameFormat.remove();

      const toPdf = await holdArtifact(page, artifact(draftId, pdf, "preview"));
      await history.selectOption(pdf);
      await toPdf.started;
      await assertDisplayed(page, draftId, pdf, docxB, "docx");
      toPdf.release();
      await page.getByLabel("PDF page 1", { exact: true }).waitFor();
      await assertDisplayed(page, draftId, pdf, pdf, "pdf");
      await assertPairedDownload(page, draftId, pdf, "pdf");
      await toPdf.remove();

      const toDocx = await holdArtifact(
        page,
        artifact(draftId, docxA, "preview"),
      );
      await history.selectOption(docxA);
      await toDocx.started;
      await assertDisplayed(page, draftId, docxA, pdf, "pdf");
      toDocx.release();
      await assertDisplayed(page, draftId, docxA, docxA, "docx");
      await toDocx.remove();

      const brokenPptx = await holdArtifact(
        page,
        artifact(draftId, pptx, "preview"),
        true,
      );
      await history.selectOption(pptx);
      await brokenPptx.started;
      await assertDisplayed(page, draftId, pptx, docxA, "docx");
      await assertPairedDownload(page, draftId, docxA, "docx");
      brokenPptx.release();
      await page
        .getByRole("alert")
        .filter({ hasText: /file type/ })
        .waitFor();
      await assertDisplayed(page, draftId, pptx, docxA, "docx");
      await brokenPptx.remove();

      await history.selectOption(docxA);
      await assertDisplayed(page, draftId, docxA, docxA, "docx");
      const toPptx = await holdArtifact(
        page,
        artifact(draftId, pptx, "preview"),
      );
      await history.selectOption(pptx);
      await toPptx.started;
      await assertDisplayed(page, draftId, pptx, docxA, "docx");
      toPptx.release();
      await assertDisplayed(page, draftId, pptx, pptx, "pptx");
      await assertPairedDownload(page, draftId, pptx, "pptx");
      await toPptx.remove();

      await history.selectOption(pdf);
      await assertDisplayed(page, draftId, pdf, pdf, "pdf");
      await history.selectOption(docxB);
      await assertDisplayed(page, draftId, docxB, docxB, "docx");
      await request(page, "POST", "/api/v1/logout");
      const unauthorizedDownload = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname ===
            artifact(draftId, docxB, "download") && response.status() === 401,
      );
      await page
        .getByRole("button", { name: "Download this revision" })
        .click();
      await unauthorizedDownload;
      await page.waitForURL("**/login");
      assert.equal(
        await page.getByRole("heading", { name: "Composer" }).count(),
        0,
      );
      assert.deepEqual([...externalRequests], []);
    } finally {
      await context.close();
      await browser.close();
    }
  },
);
