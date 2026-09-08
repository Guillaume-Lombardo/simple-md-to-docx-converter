import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { chromium } from "playwright-core";

const baseURL = "http://localhost:3100";

test("Next conversion workspace reopens a durable result after application restart", async () => {
  const profile = process.env.MARKWEAVE_E2E_PROFILE;
  assert.ok(profile === "standalone" || profile === "distributed");
  const statePath = process.env.MARKWEAVE_E2E_CONVERSION_STATE;
  assert.ok(statePath);
  const state = JSON.parse(await readFile(statePath, "utf8"));
  assert.equal(typeof state.username, "string");
  assert.equal(typeof state.password, "string");
  assert.match(state.job_id, /^[0-9a-f-]{36}$/);
  assert.equal(typeof state.expires_at, "string");
  assert.ok(
    Date.parse(state.expires_at) > Date.now(),
    "fresh recovery checkpoint expired before application restart",
  );

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
    await page.getByRole("textbox", { name: "Username" }).fill(state.username);
    await page.getByLabel("Password").fill(state.password);
    await Promise.all([
      page.waitForURL("**/convert"),
      page.getByRole("button", { name: "Sign in" }).click(),
    ]);
    await page
      .getByRole("button", {
        name: new RegExp(`Conversion ${state.job_id.slice(0, 8)}`),
      })
      .click();
    await page.getByText("Your conversion is ready to download.").waitFor();
    assert.equal(
      await page.getByRole("button", { name: "Download result" }).count(),
      1,
    );
    const recovered = await page.evaluate(async (jobId) => {
      const response = await fetch(`/api/v1/conversions/${jobId}`, {
        cache: "no-store",
        credentials: "same-origin",
      });
      return { status: response.status, body: await response.json() };
    }, state.job_id);
    assert.equal(recovered.status, 200);
    assert.equal(recovered.body.state, "succeeded");
    await context.close();
  } finally {
    await browser.close();
  }
});
