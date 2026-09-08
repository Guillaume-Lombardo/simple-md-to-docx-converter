import assert from "node:assert/strict";
import test from "node:test";
import { chromium } from "playwright-core";

const baseURL = "http://localhost:3100";

test("server-clock absolute expiry moves the protected shell to one fixed login state", async () => {
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
    const initialSession = page.waitForResponse((response) =>
      response.url().endsWith("/api/v1/session"),
    );
    await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
    assert.equal((await initialSession).status(), 401);
    await page.getByRole("textbox", { name: "Username" }).fill("e2e-admin");
    await page.getByLabel("Password").fill("e2e-admin-password");
    await Promise.all([
      page.waitForURL("**/convert"),
      page.getByRole("button", { name: "Sign in" }).click(),
    ]);

    const protectedResponse = await page.evaluate(async () => {
      const response = await fetch("/api/v1/session", {
        cache: "no-store",
        credentials: "same-origin",
      });
      return response.status;
    });
    assert.equal(protectedResponse, 200);
    const issuedCookies = (await context.cookies()).filter((cookie) =>
      ["md_converter_session", "__Host-md_converter_csrf"].includes(
        cookie.name,
      ),
    );
    assert.equal(issuedCookies.length, 2);

    let logoutRequests = 0;
    page.on("request", (request) => {
      if (request.url().endsWith("/api/v1/logout")) logoutRequests += 1;
    });
    await page.waitForTimeout(2_500);
    // Chromium expires cookies at their server-issued Max-Age. Reinsert those
    // exact browser-issued values only in the Playwright harness so one stale
    // HttpOnly session reaches FastAPI; application JavaScript never reads it.
    await context.addCookies(
      issuedCookies.map((cookie) => ({
        ...cookie,
        expires: Math.floor(Date.now() / 1_000) + 60,
      })),
    );
    const expiredResponse = page.waitForResponse((response) =>
      response.url().endsWith("/api/v1/logout"),
    );
    await Promise.all([
      page.waitForURL("**/login"),
      page.getByRole("button", { name: "Sign out" }).click(),
    ]);
    assert.equal((await expiredResponse).status(), 401);
    await page.getByText("Your session ended. Please sign in again.").waitFor();
    await page.waitForTimeout(250);
    assert.equal(logoutRequests, 1);
    await context.close();
  } finally {
    await browser.close();
  }
});
