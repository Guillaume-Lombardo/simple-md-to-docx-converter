import assert from "node:assert/strict";
import test from "node:test";
import { chromium } from "playwright-core";

const baseURL = "http://localhost:3100";

test("server expiry during conversion submission moves the workspace to fixed re-login", async () => {
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
      name: "expired-session.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("# This request must not be replayed\n"),
    });
    const issuedCookies = (await context.cookies()).filter((cookie) =>
      ["md_converter_session", "__Host-md_converter_csrf"].includes(
        cookie.name,
      ),
    );
    assert.equal(issuedCookies.length, 2);
    let submissions = 0;
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        request.url().endsWith("/api/v1/conversions")
      )
        submissions += 1;
    });
    await page.waitForTimeout(2_500);
    await context.addCookies(
      issuedCookies.map((cookie) => ({
        ...cookie,
        expires: Math.floor(Date.now() / 1_000) + 60,
      })),
    );
    const rejected = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/v1/conversions"),
    );
    await Promise.all([
      page.waitForURL("**/login"),
      page.getByRole("button", { name: "Start conversion" }).click(),
    ]);
    assert.equal((await rejected).status(), 401);
    await page.getByText("Your session ended. Please sign in again.").waitFor();
    await page.waitForTimeout(250);
    assert.equal(submissions, 1);
    await context.close();
  } finally {
    await browser.close();
  }
});
