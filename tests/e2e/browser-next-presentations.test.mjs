import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import test from "node:test";
import { chromium } from "playwright-core";

const baseURL = process.env.MARKWEAVE_PPTX_E2E_URL || "http://localhost:3100";

test(
  "2pptx generates native slides with default and selected PowerPoint templates",
  { timeout: 180000 },
  async () => {
    const browser = await chromium.launch({
      executablePath:
        process.env.MARKWEAVE_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
      chromiumSandbox: true,
      headless: true,
    });
    try {
      const context = await browser.newContext({
        baseURL,
        serviceWorkers: "block",
      });
      const page = await context.newPage();
      await page.goto("/login");
      await page
        .getByLabel("Username")
        .fill(process.env.MARKWEAVE_PPTX_E2E_USER || "e2e-admin");
      await page
        .getByLabel("Password", { exact: true })
        .fill(process.env.MARKWEAVE_PPTX_E2E_PASSWORD || "e2e-admin-password");
      await page.getByRole("button", { name: "Sign in" }).click();
      await page.waitForURL("**/convert");
      await page.getByRole("link", { name: "2pptx", exact: true }).click();
      await page.waitForURL("**/presentations");
      const suffix = await page
        .getByText("eave", { exact: true })
        .boundingBox();
      const version = await page
        .getByLabel(/^Version \d+\.\d+\.\d+$/, { exact: true })
        .boundingBox();
      assert.ok(suffix && version);
      assert.ok(
        Math.abs(
          suffix.x + suffix.width / 2 - (version.x + version.width / 2),
        ) < 1,
      );
      assert.ok(version.y <= suffix.y + suffix.height);

      await page
        .getByRole("heading", { name: "Create a PowerPoint presentation" })
        .waitFor();
      assert.equal(await page.locator('input[name="source"]').inputValue(), "");
      await page.getByLabel("Source file").setInputFiles({
        name: "slides.md",
        mimeType: "text/markdown",
        buffer: Buffer.from(
          "---\nmarp: true\n---\n# First\n\nEditable content\n\n<!-- Presenter note -->\n\n---\n\n# Second\n\n- Editable bullet\n",
        ),
      });
      await page.getByText("Slide structure", { exact: true }).click();
      await page.getByRole("button", { name: "Preview slide outline" }).click();
      await page.getByRole("heading", { name: "Slide outline" }).waitFor();
      assert.equal(await page.getByText("First", { exact: true }).count(), 1);
      const submitted = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/conversions") &&
          response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Start conversion" }).click();
      const accepted = await submitted;
      assert.equal(accepted.status(), 202);
      const job = await accepted.json();
      assert.equal(job.template_mode, "pandoc-default");
      assert.equal(job.output, "pptx");
      await page
        .getByText("Your conversion is ready to download.")
        .waitFor({ timeout: 60000 });
      const downloadEvent = page.waitForEvent("download");
      await page.getByRole("button", { name: "Download result" }).click();
      const download = await downloadEvent;
      assert.equal(download.suggestedFilename(), "slides.pptx");
      execFileSync("python", [
        "-c",
        "import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); assert b'Editable content' in z.read('ppt/slides/slide1.xml'); assert b'Editable bullet' in z.read('ppt/slides/slide2.xml'); assert b'Presenter note' in z.read('ppt/notesSlides/notesSlide1.xml')",
        await download.path(),
      ]);
      const reference = await context.request.get(
        "/api/v1/presentation-reference",
      );
      assert.equal(reference.status(), 200);
      await page.getByRole("link", { name: "templates", exact: true }).click();
      await page
        .getByRole("combobox", { name: /^Template format/ })
        .selectOption("pptx");
      const name = `Presentation ${Date.now()}`;
      await page.getByLabel("Name", { exact: true }).fill(name);
      await page
        .getByLabel("Description", { exact: true })
        .fill("Native PowerPoint reference");
      await page.getByLabel("PPTX file").setInputFiles({
        name: "reference.pptx",
        mimeType:
          "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        buffer: await reference.body(),
      });
      await page
        .getByRole("button", { name: "Create template", exact: true })
        .click();
      await page
        .getByText("Template created.", { exact: true })
        .waitFor({ timeout: 60000 });
      await page.getByRole("link", { name: "2pptx", exact: true }).click();
      await page.getByLabel("Search active templates").fill(name);
      await page.getByRole("button", { name: new RegExp(name) }).click();
      await page.getByLabel("Source file").setInputFiles({
        name: "selected.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("## Selected template\n\nNative slide\n"),
      });
      const selectedResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/conversions") &&
          response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Start conversion" }).click();
      const selected = await (await selectedResponse).json();
      assert.ok(selected.template_id);
      assert.ok(selected.template_version_id);
      await page
        .getByText("Your conversion is ready to download.")
        .waitFor({ timeout: 60000 });
      await page.getByRole("link", { name: "2docx", exact: true }).click();
      await page
        .getByRole("heading", { name: "Convert Markdown", exact: true })
        .waitFor();
      assert.equal(
        await page
          .getByRole("button", {
            name: /selected.md/,
          })
          .count(),
        0,
      );
      await context.close();
    } finally {
      await browser.close();
    }
  },
);
