import assert from "node:assert/strict";
import test from "node:test";
import { chromium } from "playwright-core";

const baseURL = "http://localhost:3100";

async function login(page) {
  await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
  const retry = page.getByRole("button", { name: "Try again" });
  if (await retry.isVisible()) await retry.click();
  await page.getByRole("textbox", { name: "Username" }).fill("e2e-admin");
  await page.getByLabel("Password").fill("e2e-admin-password");
  await Promise.all([
    page.waitForURL("**/convert"),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);
}

test(
  "Next Revert workspace derives admission and cancellation from FastAPI",
  { timeout: 600_000 },
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
      await login(page);
      const capabilitiesResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/reversions/capabilities") &&
          response.request().method() === "GET",
      );
      await page.getByRole("link", { name: "Revert, Experimental" }).click();
      await page.waitForURL("**/revert");
      assert.equal((await capabilitiesResponse).status(), 200);
      await page
        .getByRole("heading", { name: "New conversion to Markdown" })
        .waitFor();
      await page.getByText(/Extensions are a selection hint/).waitFor();
      await page.getByText(/OCR is not available/).waitFor();

      const input = page.getByLabel(/Source document/);
      const acceptedExtensions = (await input.getAttribute("accept")) ?? "";
      assert.match(acceptedExtensions, /\.rtf(?:,|$)/);
      await page.getByRole("button", { name: "Start conversion" }).click();
      await page
        .getByRole("alert")
        .getByText(/Choose a supported document/)
        .waitFor();

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
        await page
          .getByRole("button", { name: "Download result" })
          .count(),
        0,
      );
      await context.close();
    } finally {
      await browser.close();
    }
  },
);
