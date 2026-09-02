import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import test from "node:test";

import { chromium } from "playwright-core";

const baseURL = "http://localhost:3100";

test(
  "Next conversion workspace prepares a fresh durable result for restart",
  { timeout: 240_000 },
  async () => {
    const profile = process.env.MARKWEAVE_E2E_PROFILE;
    assert.ok(profile === "standalone" || profile === "distributed");
    const statePath = process.env.MARKWEAVE_E2E_CONVERSION_STATE;
    assert.ok(statePath);
    const identity = JSON.parse(await readFile(statePath, "utf8"));
    assert.equal(typeof identity.username, "string");
    assert.equal(typeof identity.password, "string");

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
      await page
        .getByRole("textbox", { name: "Username", exact: true })
        .fill(identity.username);
      await page
        .getByLabel("Password", { exact: true })
        .fill(identity.password);
      await Promise.all([
        page.waitForURL("**/convert"),
        page.getByRole("button", { name: "Sign in", exact: true }).click(),
      ]);
      await page
        .getByRole("heading", { name: "New conversion", exact: true })
        .waitFor();
      await page
        .getByRole("button", { name: "Use Pandoc default", exact: true })
        .click();
      await page.getByLabel(/Source file/).setInputFiles({
        name: "restart-recovery.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("# Recover this conversion after restart\n"),
      });
      await page.getByRole("radio", { name: "DOCX", exact: true }).click();
      const accepted = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/conversions") &&
          response.request().method() === "POST" &&
          response.status() === 202,
      );
      await page
        .getByRole("button", { name: "Start conversion", exact: true })
        .click();
      const job = await (await accepted).json();
      assert.match(job.id, /^[0-9a-f-]{36}$/);
      await page
        .getByText("Your conversion is ready to download.")
        .waitFor({ timeout: 180_000 });
      const authoritative = await page.evaluate(async (jobId) => {
        const response = await fetch(`/api/v1/conversions/${jobId}`, {
          cache: "no-store",
          credentials: "same-origin",
        });
        return { status: response.status, body: await response.json() };
      }, job.id);
      assert.equal(authoritative.status, 200);
      assert.equal(authoritative.body.state, "succeeded");
      assert.equal(typeof authoritative.body.expires_at, "string");
      assert.ok(Date.parse(authoritative.body.expires_at) > Date.now());
      await writeFile(
        statePath,
        `${JSON.stringify({
          ...identity,
          expires_at: authoritative.body.expires_at,
          job_id: job.id,
        })}\n`,
        { encoding: "utf8", mode: 0o600 },
      );
      await context.close();
    } finally {
      await browser.close();
    }
  },
);
