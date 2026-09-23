import assert from "node:assert/strict";
import { stat } from "node:fs/promises";
import test from "node:test";
import { chromium } from "playwright-core";

test("isolated browser runner loads the production same-origin login page", async () => {
  const baseURL = process.env.MARKWEAVE_E2E_BASE_URL;
  assert.equal(baseURL, "http://localhost:3100");
  const browser = await chromium.launch({
    executablePath: "/usr/bin/google-chrome-stable",
    headless: true,
  });
  try {
    const page = await browser.newPage();
    const response = await page.goto(`${baseURL}/login`, {
      waitUntil: "networkidle",
    });
    assert.equal(response?.status(), 200);
    assert.equal(new URL(page.url()).pathname, "/login");
    assert.equal(await page.title(), "Markweave");
    assert.ok(response?.headers()["content-security-policy"]);
    assert.equal(
      await page.evaluate(() =>
        fetch("/api/v1/session", { credentials: "same-origin" }).then(
          (result) => result.status,
        ),
      ),
      401,
    );
    const screenshot = "/browser-artifacts/browser-runner-smoke.png";
    await page.screenshot({ path: screenshot, fullPage: true });
    assert.ok((await stat(screenshot)).size > 0);
  } finally {
    await browser.close();
  }
});
