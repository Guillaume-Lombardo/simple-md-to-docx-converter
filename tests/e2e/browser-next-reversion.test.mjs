import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright-core";

const baseURL = "http://localhost:3100";

async function login(
  page,
  username = "e2e-admin",
  password = "e2e-admin-password",
) {
  await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
  const retry = page.getByRole("button", { name: "Try again" });
  if (await retry.isVisible()) await retry.click();
  await page.getByRole("textbox", { name: "Username" }).fill(username);
  await page.getByLabel("Password").fill(password);
  await Promise.all([
    page.waitForURL("**/convert"),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);
}

async function inspectRecoveredResult(page, context, profile) {
  const statePath = process.env.MARKWEAVE_E2E_REVERSE_RECOVERY_STATE;
  assert.ok(statePath);
  const state = JSON.parse(await readFile(statePath, "utf8"));
  assert.equal(state.schema, "t73-reverse-lifecycle-v1");
  assert.equal(state.profile, profile);
  assert.equal(state.scenario, "broker-restart");
  assert.equal(state.owner, `t73-lifecycle-${profile}-broker-restart`);
  assert.match(state.recovery_job_id, /^[0-9a-f-]{36}$/);
  const receiptPath = process.env.MARKWEAVE_E2E_REVERSE_RESULT_RECEIPT;
  assert.ok(receiptPath);
  const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
  assert.deepEqual(Object.keys(receipt).sort(), [
    "profile",
    "recovery_job_id",
    "scenario",
    "schema",
    "sha256",
  ]);
  assert.equal(receipt.schema, "t73-reverse-lifecycle-result-v1");
  assert.equal(receipt.profile, profile);
  assert.equal(receipt.scenario, state.scenario);
  assert.equal(receipt.recovery_job_id, state.recovery_job_id);
  assert.match(receipt.sha256, /^[0-9a-f]{64}$/);
  await login(page, state.owner, "T73-lifecycle-fixture-password");
  await page.getByRole("link", { name: "2md, Experimental" }).click();
  await page
    .getByRole("heading", { name: "New conversion to Markdown" })
    .waitFor();
  await page.locator(`button[title="${state.recovery_job_id}"]`).click();
  const downloadButton = page.getByRole("button", { name: "Download result" });
  await downloadButton.waitFor();
  const jobResponse = await context.request.get(
    `/api/v1/reversions/${state.recovery_job_id}`,
  );
  assert.equal(jobResponse.status(), 200);
  const job = await jobResponse.json();
  assert.equal(job.state, "succeeded");
  assert.ok(job.attempt >= 2);
  const result = await context.request.get(
    `/api/v1/reversions/${state.recovery_job_id}/result`,
  );
  assert.equal(result.status(), 200);
  assert.match(result.headers()["cache-control"], /private/);
  assert.match(result.headers()["cache-control"], /no-store/);
  const downloaded = page.waitForEvent("download");
  await downloadButton.click();
  const download = await downloaded;
  assert.match(download.suggestedFilename(), /\.zip$/);
  const downloadPath = await download.path();
  assert.ok(downloadPath);
  const bytes = await readFile(downloadPath);
  const inspectedBytes = await result.body();
  for (const candidate of [bytes, inspectedBytes]) {
    assert.ok(
      createHash("sha256").update(candidate).digest("hex") === receipt.sha256,
      "Recovered result differs from the package inspected during frontend outage",
    );
  }
  assert.ok(
    bytes.equals(inspectedBytes),
    "Browser result differs from the API package inspected by lifecycle verification",
  );
  assert.ok(bytes.subarray(0, 4).equals(Buffer.from([80, 75, 3, 4])));
}

async function openReversionWorkspace(page) {
  const capabilities = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/reversions/capabilities") &&
      response.request().method() === "GET",
  );
  await page.getByRole("link", { name: "2md, Experimental" }).click();
  await page.waitForURL("**/revert");
  assert.equal((await capabilities).status(), 200);
  await page
    .getByRole("heading", { name: "New conversion to Markdown" })
    .waitFor();
}

async function exerciseStructuredPptx(page) {
  const source = process.env.MARKWEAVE_E2E_STRUCTURED_PPTX_SOURCE;
  assert.ok(source);
  await openReversionWorkspace(page);
  const input = page.getByLabel(/Source document/);
  await input.setInputFiles(source);
  await page.getByText("Selected markweave-t83-edited.pptx").waitFor();
  await page
    .getByRole("button", { name: "Open selected source in Composer" })
    .click();
  await page.waitForURL(/\/composer\?draft=[0-9a-f-]{36}$/);
  const documentUrl = await page.evaluate(
    () => performance.getEntriesByType("navigation")[0]?.name,
  );
  assert.equal(new URL(documentUrl).pathname, "/composer");
  await page.getByRole("heading", { name: "Composer", exact: true }).waitFor();
  await page.getByRole("button", { name: "Capture source revision" }).click();
  await page.getByText(/Original source captured/).waitFor();
  await page.getByText("PPTX preview").waitFor();
  const nativePreview = page.frameLocator('iframe[title^="PPTX revision"]');
  await nativePreview.getByText("Slide 1 of 2").waitFor();
  await nativePreview.getByRole("button", { name: "Next slide" }).click();
  await nativePreview.getByText("Slide 2 of 2").waitFor();
  await page.getByRole("link", { name: "2md, Experimental" }).click();
  await page.waitForURL("**/revert");
  await page.getByText("Selected markweave-t83-edited.pptx").waitFor();
  await page.reload({ waitUntil: "networkidle" });
  await page.getByText("Selected markweave-t83-edited.pptx").waitFor();
  const extraction = page.getByRole("group", { name: "PowerPoint extraction" });
  const anydoc = extraction.getByRole("radio", {
    name: "Standard document extraction",
  });
  assert.equal(await anydoc.isChecked(), true);
  await extraction.getByRole("radio", { name: "Marp Markdown" }).check();
  const notes = extraction.getByRole("checkbox", {
    name: "Include presenter notes",
  });
  const images = extraction.getByRole("checkbox", { name: "Include images" });
  assert.equal(await notes.isChecked(), true);
  assert.equal(await images.isChecked(), true);
  const accepted = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/reversions") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Start conversion" }).click();
  const response = await accepted;
  assert.equal(response.status(), 202);
  const job = await response.json();
  assert.match(job.id, /^[0-9a-f-]{36}$/);
  assert.deepEqual(job.options, {
    extraction: "marp",
    include_notes: true,
    include_images: true,
  });
  await page.locator(`button[title="${job.id}"]`).click();
  const button = page.getByRole("button", { name: "Download result" });
  await button.waitFor({ timeout: 60_000 });
  const downloaded = page.waitForEvent("download");
  await button.click();
  const download = await downloaded;
  assert.match(download.suggestedFilename(), /\.zip$/);
  const path = await download.path();
  assert.ok(path);
  execFileSync(
    "python",
    [
      "-c",
      String.raw`import json,sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); assert z.namelist()==['document.md','assets/image-0001.png','manifest.json']; m=z.read('document.md'); assert m.startswith(b'---\nmarp: true\n---\n\n'); assert b'T83 edited first slide' in m and b'T83 edited second slide' in m and b'<!--\nT83 edited presenter note\n-->' in m and b'![Edited pixels](assets/image-0001.png)' in m; assert json.loads(z.read('manifest.json'))['extractor']=='markweave-pptx-v1'`,
      path,
    ],
    { timeout: 10_000 },
  );
  await page
    .getByRole("button", { name: "Open Markdown result in Composer" })
    .click();
  await page.waitForURL(/\/composer\?draft=[0-9a-f-]{36}$/);
  await page.getByRole("heading", { name: "Composer", exact: true }).waitFor();
  await page.getByText("application/zip", { exact: false }).waitFor();
  await page.getByRole("link", { name: "2md, Experimental" }).click();
  await page.waitForURL("**/revert");
  await page.getByText("Selected markweave-t83-edited.pptx").waitFor();
}

test(
  "Next Revert workspace uses live capabilities and the real reverse lifecycle",
  { timeout: 600_000 },
  async () => {
    const phase = process.env.MARKWEAVE_E2E_REVERSE_PHASE ?? "primary";
    assert.ok(
      ["primary", "structured", "admission", "recovered"].includes(phase),
    );
    const profile = process.env.MARKWEAVE_E2E_PROFILE;
    assert.ok(profile === "standalone" || profile === "distributed");
    const browser = await chromium.launch({
      executablePath:
        process.env.MARKWEAVE_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
      headless: true,
    });
    try {
      const context = await browser.newContext({
        baseURL,
        serviceWorkers: "block",
      });
      const page = await context.newPage();
      if (phase === "recovered") {
        await inspectRecoveredResult(page, context, profile);
        await context.close();
        return;
      }
      await login(page);
      if (phase === "structured") {
        await exerciseStructuredPptx(page);
        await context.close();
        return;
      }
      await page.getByLabel(/Source file/).setInputFiles({
        buffer: Buffer.from("# Forward conversion source"),
        mimeType: "text/markdown",
        name: "forward-only.md",
      });
      await page.getByText(/Selected forward-only.md/).waitFor();
      const reverseSubmissions = [];
      const forwardSubmissions = [];
      page.on("request", (request) => {
        if (
          request.method() === "POST" &&
          new URL(request.url()).pathname === "/api/v1/conversions"
        )
          forwardSubmissions.push(request);
        if (
          request.method() === "POST" &&
          new URL(request.url()).pathname === "/api/v1/reversions"
        )
          reverseSubmissions.push(request);
      });
      const capabilitiesResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/reversions/capabilities") &&
          response.request().method() === "GET",
      );
      await page.getByRole("link", { name: "2md, Experimental" }).click();
      await page.waitForURL("**/revert");
      const capabilityResponse = await capabilitiesResponse;
      assert.equal(capabilityResponse.status(), 200);
      const capabilities = await capabilityResponse.json();
      await page
        .getByRole("heading", { name: "New conversion to Markdown" })
        .waitFor();
      await page.getByText(/Extensions are a selection hint/).waitFor();
      await page.getByText(/OCR is not available/).waitFor();

      const input = page.getByLabel(/Source document/);
      assert.equal(await input.evaluate((element) => element.files.length), 0);
      assert.equal(await page.getByText(/Selected forward-only.md/).count(), 0);
      assert.deepEqual(
        (
          await page.getByRole("main").getByRole("alert").allTextContents()
        ).filter((text) => text.trim()),
        [],
      );
      assert.equal(reverseSubmissions.length, 0);
      const acceptedExtensions = (await input.getAttribute("accept")) ?? "";
      assert.deepEqual(acceptedExtensions.split(","), [
        ...new Set(
          capabilities.format_families.flatMap((family) => family.extensions),
        ),
      ]);
      await page
        .getByText(
          `Choose or drop exactly one supported document (maximum ${capabilities.maximum_upload_bytes} bytes).`,
          { exact: true },
        )
        .waitFor();
      await page.getByRole("button", { name: "Start conversion" }).click();
      await page
        .getByRole("alert")
        .getByText(/Choose a supported document/)
        .waitFor();
      assert.equal(reverseSubmissions.length, 0);

      if (phase === "admission") {
        assert.equal(capabilities.maximum_upload_bytes, 1024);
        await input.setInputFiles({
          buffer: Buffer.alloc(capabilities.maximum_upload_bytes + 1, 65),
          mimeType: "application/rtf",
          name: "oversized.rtf",
        });
        await page.getByRole("button", { name: "Start conversion" }).click();
        await page
          .getByRole("alert")
          .getByText(/upload limit/i)
          .waitFor();
        assert.equal(reverseSubmissions.length, 0);
      }

      await input.setInputFiles({
        buffer: Buffer.from("{\\rtf1\\ansi Browser reverse conversion}"),
        mimeType: "application/rtf",
        name: "browser-source.rtf",
      });
      const accepted = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/reversions") &&
          response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Start conversion" }).click();
      const acceptedResponse = await accepted;
      assert.equal(acceptedResponse.status(), 202);
      assert.ok(acceptedResponse.request().headers()["idempotency-key"]);
      if (phase === "admission") {
        await page.getByText("Your document is queued.").waitFor();

        const cancelled = page.waitForResponse(
          (response) =>
            /\/api\/v1\/reversions\/[0-9a-f-]+$/.test(response.url()) &&
            response.request().method() === "DELETE",
        );
        await page.getByRole("button", { name: "Cancel conversion" }).click();
        assert.equal((await cancelled).status(), 200);
        await page.getByText(/conversion was cancelled/i).waitFor();
        assert.equal(
          await page.getByRole("button", { name: "Download result" }).count(),
          0,
        );
      } else {
        const button = page.getByRole("button", { name: "Download result" });
        await button.waitFor({ timeout: 60_000 });
        const downloaded = page.waitForEvent("download");
        await button.click();
        const download = await downloaded;
        assert.match(download.suggestedFilename(), /\.md$/);
        const path = await download.path();
        assert.ok(path);
        assert.match(
          await readFile(path, "utf8"),
          /Browser reverse conversion/,
        );
      }
      await page.getByRole("link", { name: "2docx", exact: true }).click();
      await page
        .getByRole("heading", { name: "New conversion", exact: true })
        .waitFor();
      assert.equal(
        await page
          .getByLabel(/Source file/)
          .evaluate((element) =>
            Array.from(element.files).some(
              (file) => file.name === "browser-source.rtf",
            ),
          ),
        false,
      );
      assert.equal(
        await page.getByText(/Selected browser-source.rtf/).count(),
        0,
      );
      assert.equal(forwardSubmissions.length, 0);
      assert.equal(reverseSubmissions.length, 1);
      assert.deepEqual(
        (
          await page.getByRole("main").getByRole("alert").allTextContents()
        ).filter((text) => text.trim()),
        [],
      );
      if (phase === "primary") {
        for (const fault of ["unavailable", "future-schema"]) {
          await page.route(
            "**/api/v1/reversions/capabilities",
            async (route) => {
              if (fault === "unavailable") {
                await route.fulfill({
                  status: 503,
                  contentType: "application/json",
                  body: "{}",
                });
              } else {
                const response = await route.fetch();
                assert.equal(response.status(), 200);
                const body = await response.json();
                body.schema_version = 2;
                await route.fulfill({ response, json: body });
              }
            },
          );
          await page.goto(`${baseURL}/revert`);
          await page
            .getByRole("heading", { name: "Revert is unavailable" })
            .waitFor();
          assert.equal(
            await page
              .getByRole("button", { name: "Start conversion" })
              .count(),
            0,
          );
          assert.equal(await page.getByLabel(/Source document/).count(), 0);
          assert.equal(reverseSubmissions.length, 1);
          await page.unroute("**/api/v1/reversions/capabilities");
        }
      }
      await context.close();
    } finally {
      await browser.close();
    }
  },
);
