import assert from "node:assert/strict";
import test from "node:test";

import { chromium } from "playwright-core";

import { sessionRequest } from "./browser-helpers.mjs";

const REQUIRED_ENVIRONMENT = [
  "MD_CONVERTER_E2E_BASE_URL",
  "MD_CONVERTER_E2E_PROFILE",
  "MD_CONVERTER_E2E_PROVISIONED_USERNAME",
  "MD_CONVERTER_E2E_PROVISIONED_OLD_PASSWORD",
  "MD_CONVERTER_E2E_PROVISIONED_PASSWORD",
];

test("startup CSV replaces an existing final-image account after restart", async () => {
  for (const name of REQUIRED_ENVIRONMENT) {
    assert.ok(process.env[name], `${name} is required`);
  }
  const baseUrl = process.env.MD_CONVERTER_E2E_BASE_URL;
  const username = process.env.MD_CONVERTER_E2E_PROVISIONED_USERNAME;
  const browser = await chromium.launch({
    executablePath: process.env.MD_CONVERTER_E2E_CHROMIUM
      || "/usr/bin/google-chrome-stable",
    headless: true,
  });
  const context = await browser.newContext({ baseURL: baseUrl, serviceWorkers: "block" });
  const page = await context.newPage();
  try {
    await page.goto("/login", { waitUntil: "networkidle" });
    await page.locator('input[name="username"]').fill(username);
    await page.locator('input[name="password"]').fill(
      process.env.MD_CONVERTER_E2E_PROVISIONED_OLD_PASSWORD,
    );
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.locator(".alert").filter({
      hasText: "The username or password is incorrect.",
    }).waitFor();

    await page.locator('input[name="username"]').fill(username);
    await page.locator('input[name="password"]').fill(
      process.env.MD_CONVERTER_E2E_PROVISIONED_PASSWORD,
    );
    await Promise.all([
      page.waitForURL("**/change-password"),
      page.getByRole("button", { name: "Sign in" }).click(),
    ]);
    const session = await sessionRequest(page, "/api/v1/session", { json: true });
    assert.equal(session.status, 200);
    assert.equal(session.body.username, username);
    assert.equal(session.body.password_change_required, true);
  } finally {
    await context.close();
    await browser.close();
  }
});
