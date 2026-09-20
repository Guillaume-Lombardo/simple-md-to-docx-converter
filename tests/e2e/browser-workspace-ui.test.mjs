import assert from "node:assert/strict";
import test from "node:test";
import { chromium } from "playwright-core";

// Exercise actual served UI assets; synthetic API responses isolate presentation behavior.
const baseURL = process.env.MARKWEAVE_UI_E2E_URL || "http://localhost:3100";
const user = {
  active: true,
  effective_idle_minutes: 60,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "admin",
  username: "UI acceptance",
};
const common = {
  attempt: 0,
  cancel_requested: false,
  component_versions: [],
  correlation_id: "ui-test",
  created_at: "2026-09-20T10:00:00Z",
  updated_at: "2026-09-20T10:00:00Z",
  error_code: null,
  error_message: null,
  expires_at: null,
  owner_id: user.id,
  state: "succeeded",
  step: "complete",
};
const forward = {
  ...common,
  id: "00000000-0000-4000-8000-000000000201",
  output: "docx",
  progress: 100,
  source_filename: "Quarterly report.md",
  template_id: null,
  template_version_id: null,
  template_mode: "pandoc-default",
};
const presentation = {
  ...forward,
  id: "00000000-0000-4000-8000-000000000202",
  output: "pptx",
  source_filename: "Board presentation.md",
};
const reverse = {
  ...common,
  id: "00000000-0000-4000-8000-000000000203",
  detected_format: "docx",
  result_mode: "markdown",
  result_size: 10,
  source_stem: "Meeting notes",
  source_extension: ".docx",
  source_family: "word",
};
const capabilities = {
  schema_version: 1,
  maximum_upload_bytes: 1000000,
  admission: {
    csv_policy: "bounded text",
    extension_is_hint: true,
    mismatch_policy: "reject",
    scanner_order: "scan first",
    undetected_policy: "reject",
  },
  execution: { hosted_fallback: false, local: true, ocr: false },
  format_families: [
    {
      content_detection: "signature",
      detected_formats: ["docx"],
      extensions: [".docx"],
      family: "word",
      selected_parser_format: null,
    },
  ],
  pdf: {
    contract: "text extraction only",
    document_model_available: false,
    embedded_assets_available: false,
    image_preservation: false,
    mixed_or_image_only_pages: "reject as needs_ocr",
    warning: "Images are not preserved",
  },
  result_package_modes: ["markdown"],
};

test(
  "all workspaces show selected files, named history, primary downloads and collapsed slide options",
  { timeout: 60000 },
  async () => {
    const browser = await chromium.launch({
      executablePath:
        process.env.MARKWEAVE_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
      chromiumSandbox: true,
    });
    try {
      for (const width of [1280, 480]) {
        for (const [route, job, name, filename] of [
          ["/convert", forward, forward.source_filename, "picked.md"],
          [
            "/presentations",
            presentation,
            presentation.source_filename,
            "picked.md",
          ],
          ["/revert", reverse, "Meeting notes.docx", "picked.docx"],
        ]) {
          let ready = route === "/revert";
          const context = await browser.newContext({
            baseURL,
            viewport: { width, height: 1000 },
          });
          const page = await context.newPage();
          await page.route("**/api/v1/**", async (request) => {
            assert.equal(request.request().method(), "GET");
            const path = new URL(request.request().url()).pathname;
            let body;
            if (path === "/api/v1/session") body = user;
            else if (path === "/api/v1/conversion-options")
              body = {
                conversion_upload_max_bytes: 1000000,
                resolved_template: null,
                selection_source: "pandoc_default",
                template_version_id: null,
              };
            else if (path === "/api/v1/reversions/capabilities")
              body = capabilities;
            else if (path === "/api/v1/conversions")
              body = {
                items: [forward, presentation],
                total: 2,
                offset: 0,
                limit: 10,
              };
            else if (path === "/api/v1/reversions")
              body = { items: [reverse], total: 1, offset: 0, limit: 10 };
            else if (path === "/api/v1/templates")
              body = { items: [], total: 0, offset: 0, limit: 20 };
            else if (path.endsWith(job.id))
              body = ready
                ? job
                : {
                    ...job,
                    state: "running",
                    step: "publishing",
                    progress: 100,
                  };
            else throw new Error(`Unexpected API route ${path}`);
            await request.fulfill({
              status: 200,
              contentType: "application/json",
              body: JSON.stringify(body),
            });
          });
          await page.goto(route);
          const icon = page.locator('link[rel="icon"]');
          assert.equal(await icon.getAttribute("href"), "/markweave-icon.svg");
          const iconResponse = await page.request.get("/markweave-icon.svg");
          assert.equal(iconResponse.status(), 200);
          assert.match(
            iconResponse.headers()["content-type"],
            /image\/svg\+xml/,
          );
          const input = page.locator('input[name="source"]');
          await input.waitFor({ state: "attached" });
          await input.setInputFiles({
            name: filename,
            mimeType: "application/octet-stream",
            buffer: Buffer.from("Synthetic content"),
          });
          await page
            .getByText(`Selected ${filename} (17 bytes).`, { exact: true })
            .waitFor();
          await page.getByText("Change file", { exact: true }).waitFor();
          const message = page.getByText(`Selected ${filename} (17 bytes).`, {
            exact: true,
          });
          const action = page.getByText("Change file", { exact: true });
          const messageBounds = await message.boundingBox();
          const actionBounds = await action.boundingBox();
          assert.ok(messageBounds.y + messageBounds.height <= actionBounds.y);
          assert.ok(
            (await message.evaluate((node) =>
              Number.parseFloat(getComputedStyle(node).fontSize),
            )) >
              (await action.evaluate((node) =>
                Number.parseFloat(getComputedStyle(node).fontSize),
              )),
          );

          assert.equal(
            await input.evaluate((node) => getComputedStyle(node).width),
            "1px",
          );
          const drop = await page.evaluateHandle((filename) => {
            const dt = new DataTransfer();
            dt.items.add(new File(["Dropped"], filename));
            return dt;
          }, `dropped-${filename}`);
          await input
            .locator("..")
            .dispatchEvent("drop", { dataTransfer: drop });
          await page
            .getByText(`Selected dropped-${filename} (7 bytes).`, {
              exact: true,
            })
            .waitFor();
          await drop.dispose();
          if (route === "/presentations") {
            const details = page.locator("details");
            assert.equal(await details.evaluate((node) => node.open), false);
            await page.getByText("Slide structure", { exact: true }).click();
            await page
              .getByLabel("Markdown format")
              .waitFor({ state: "visible" });
            await page.getByText("Slide structure", { exact: true }).click();
            assert.equal(await details.evaluate((node) => node.open), false);
          }
          await page.reload();
          const history = page.getByRole("button", {
            name: `${name} · succeeded`,
            exact: true,
          });
          await history.waitFor();
          assert.equal(await history.getAttribute("title"), job.id);
          await history.click();
          if (!ready) {
            const progress = page.getByRole("progressbar");
            await progress.waitFor();
            assert.equal(await progress.getAttribute("value"), "100");
            assert.equal(
              await page
                .getByRole("button", { name: "Download result" })
                .count(),
              0,
            );
            const before = await page.locator("#recent-heading").boundingBox();
            ready = true;
            await page.getByTitle(job.id, { exact: true }).click();
            await page
              .getByRole("button", { name: "Download result" })
              .waitFor();
            const after = await page.locator("#recent-heading").boundingBox();
            assert.equal(
              after.y,
              before.y,
              "Completion must not move the history column",
            );
          }
          const download = page.getByRole("button", {
            name: "Download result",
            exact: true,
          });
          await download.waitFor();
          const start = page.getByRole("button", {
            name: "Start conversion",
            exact: true,
          });
          assert.ok(
            (await download.getAttribute("class")).includes("primary-button"),
          );
          assert.equal(await page.getByRole("progressbar").count(), 0);
          assert.equal(
            await download.evaluate(
              (node) => getComputedStyle(node).backgroundColor,
            ),
            await start.evaluate(
              (node) => getComputedStyle(node).backgroundColor,
            ),
          );
          await context.close();
        }
      }
    } finally {
      await browser.close();
    }
  },
);
