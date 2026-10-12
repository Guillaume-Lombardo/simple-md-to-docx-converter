import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { chromium } from "playwright-core";

// T104: the 2docx and 2pptx workspaces package Markdown with its local images
// in the browser; the unchanged API and final-image worker validate the result.
const baseURL = "http://localhost:3100";
const missing = "Markdown image is missing or unapproved.";
const escaping = "Markdown image path escapes the archive.";
const uploadLimit = "The selected file exceeds the configured upload limit.";
// A valid 1x1 RGBA PNG.
const pixel = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

function markdownFile(name, text) {
  return { name, mimeType: "text/markdown", buffer: Buffer.from(text) };
}

function imageFile(name, buffer = pixel) {
  return { name, mimeType: "image/png", buffer };
}

function assertArchiveMedia(path, prefix) {
  execFileSync("python", [
    "-c",
    "import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); media=[n for n in z.namelist() if n.startswith(sys.argv[2])]; assert len(media)==1, media; assert z.read(media[0]).startswith(b'\\x89PNG')",
    path,
    prefix,
  ]);
}

async function signIn(context) {
  const page = await context.newPage();
  await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
  await page.getByRole("textbox", { name: "Username" }).fill("e2e-admin");
  await page.getByLabel("Password").fill("e2e-admin-password");
  await Promise.all([
    page.waitForURL("**/convert"),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);
  await page.getByRole("heading", { name: "New conversion" }).waitFor();
  return page;
}

async function convertAndDownload(page, expectedName, mediaPrefix) {
  const accepted = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/conversions") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Start conversion" }).click();
  const response = await accepted;
  assert.equal(response.status(), 202);
  const job = await response.json();
  assert.equal(job.source_filename, expectedName.replace(/\.[^.]+$/, ".zip"));
  await page
    .getByText("Your conversion is ready to download.")
    .waitFor({ timeout: 180_000 });
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download result" }).click();
  const download = await downloadEvent;
  assert.equal(download.suggestedFilename(), expectedName);
  assertArchiveMedia(await download.path(), mediaPrefix);
}

async function expectRefusedBeforeUpload(page, files, message, start = false) {
  let uploads = 0;
  const count = (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/v1/conversions"
    )
      uploads += 1;
  };
  page.on("request", count);
  await page.getByLabel(/Source file/).setInputFiles(files);
  if (start) {
    await page.getByText(/^Packaged /).waitFor();
    await page.getByRole("button", { name: "Start conversion" }).click();
  }
  await page.getByRole("alert").filter({ hasText: message }).waitFor();
  page.off("request", count);
  assert.equal(uploads, 0);
}

test(
  "2docx and 2pptx package local images from files and folders",
  { timeout: 600_000 },
  async () => {
    const profile = process.env.MARKWEAVE_E2E_PROFILE;
    assert.ok(profile === "standalone" || profile === "distributed");
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
      const page = await signIn(context);
      await page.getByRole("radio", { name: "DOCX", exact: true }).click();

      // Primary path: a selected folder keeps the image's relative path.
      const folder = mkdtempSync(join(tmpdir(), "t104-folder-"));
      mkdirSync(join(folder, "img"));
      writeFileSync(
        join(folder, "report.md"),
        `# Local images ${profile}\n\n![Pixel](img/pixel.png)\n`,
      );
      writeFileSync(join(folder, "img", "pixel.png"), pixel);
      writeFileSync(join(folder, "img", "unused.png"), pixel);
      writeFileSync(join(folder, "notes.txt"), "not packaged");
      await page.getByLabel(/Markdown folder/).setInputFiles(folder);
      await page
        .getByText("Packaged report.md with 1 local image as report.zip.")
        .waitFor();
      await convertAndDownload(page, "report.docx", "word/media/");

      // Missing and escaping references fail before upload with the service's
      // stable messages; the CLI final-image workflow proves the service parity.
      await expectRefusedBeforeUpload(
        page,
        [
          markdownFile("missing.md", "![m](absent.png)"),
          imageFile("other.png"),
        ],
        missing,
      );
      await expectRefusedBeforeUpload(
        page,
        [
          markdownFile("escape.md", "![t](../pixel.png)"),
          imageFile("pixel.png"),
        ],
        escaping,
      );
      // An oversize package is refused against the authoritative upload limit.
      await expectRefusedBeforeUpload(
        page,
        [
          markdownFile("large.md", "![l](large.png)"),
          imageFile(
            "large.png",
            Buffer.concat([pixel, Buffer.alloc(1_000_001)]),
          ),
        ],
        uploadLimit,
        true,
      );

      // 2pptx: a flat multi-file selection matches the image by its name.
      await page.getByRole("link", { name: "2pptx", exact: true }).click();
      await page.waitForURL("**/presentations");
      await page
        .getByRole("heading", { name: "Create a PowerPoint presentation" })
        .waitFor();
      await page
        .getByLabel(/Source file/)
        .setInputFiles([
          markdownFile(
            "slides.md",
            "## Slide with image\n\n![Pixel](assets/pixel.png)\n",
          ),
          imageFile("pixel.png"),
        ]);
      await page
        .getByText("Packaged slides.md with 1 local image as slides.zip.")
        .waitFor();
      await convertAndDownload(page, "slides.pptx", "ppt/media/");
      await expectRefusedBeforeUpload(
        page,
        [markdownFile("missing.md", "![m](gone.png)"), imageFile("pixel.png")],
        missing,
      );
      await context.close();
    } finally {
      await browser.close();
    }
  },
);
