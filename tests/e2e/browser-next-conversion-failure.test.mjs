import assert from "node:assert/strict";
import test from "node:test";
import { chromium } from "playwright-core";

const baseURL = "http://localhost:3100";

function oversizedPdfMarkdown() {
  const paragraph = "Bounded final-image PDF validation text. ".repeat(8);
  return `# PDF output limit\n\n${Array.from(
    { length: 1_000 },
    (_, index) => `${index}. ${paragraph}`,
  ).join("\n\n")}\n`;
}

test(
  "a fresh Next workspace polls an authoritative terminal conversion failure",
  { timeout: 240_000 },
  async () => {
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
      await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
      await page.getByRole("textbox", { name: "Username" }).fill("e2e-admin");
      await page.getByLabel("Password").fill("e2e-admin-password");
      await Promise.all([
        page.waitForURL("**/convert"),
        page.getByRole("button", { name: "Sign in" }).click(),
      ]);
      await page.getByRole("heading", { name: "New conversion" }).waitFor();
      await page.getByLabel(/Source file/).setInputFiles({
        name: "pdf-limit.md",
        mimeType: "text/markdown",
        buffer: Buffer.from(oversizedPdfMarkdown()),
      });
      await page.getByRole("radio", { name: "PDF", exact: true }).click();

      const accepted = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/conversions") &&
          response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Start conversion" }).click();
      const acceptedResponse = await accepted;
      assert.equal(acceptedResponse.status(), 202);
      const acceptedJob = await acceptedResponse.json();
      assert.match(acceptedJob.id, /^[0-9a-f-]{36}$/);

      const firstPoll = await page.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v1/conversions/${acceptedJob.id}`) &&
          response.request().method() === "GET",
        { timeout: 30_000 },
      );
      assert.equal(firstPoll.status(), 200);
      await page
        .getByText(/PDF.*configured limits/)
        .waitFor({ timeout: 180_000 });
      assert.equal(
        await page.getByRole("button", { name: "Download result" }).count(),
        0,
      );
      await context.close();
    } finally {
      await browser.close();
    }
  },
);
