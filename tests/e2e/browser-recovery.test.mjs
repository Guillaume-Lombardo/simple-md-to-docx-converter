import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright-core";

import {
  discardTrace,
  retainFailureArtifacts,
  sessionRequest,
  startTrace,
  waitForText,
} from "./browser-helpers.mjs";

const baseUrl = process.env.MD_CONVERTER_E2E_BASE_URL;
const recoveryState = process.env.MD_CONVERTER_E2E_RECOVERY_STATE;
const profile = process.env.MD_CONVERTER_E2E_PROFILE;
const artifactRoot = process.env.MD_CONVERTER_E2E_ARTIFACT_DIR;

test("authenticated browser state remains valid after forced restart", {
  skip: baseUrl && recoveryState && profile && artifactRoot
    ? false : "recovery settings unavailable",
}, async () => {
  assert.ok(baseUrl && recoveryState && profile && artifactRoot, "recovery settings are required");
  assert.equal(path.isAbsolute(recoveryState), true, "recovery state path must be absolute");

  let browser = null;
  let context = null;
  let page = null;
  let trace = null;
  let step = "launch Chromium after forced restart";
  try {
    browser = await chromium.launch({
      executablePath: process.env.MD_CONVERTER_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
      headless: true,
    });
    context = await browser.newContext({
      baseURL: baseUrl,
      storageState: recoveryState,
      serviceWorkers: "block",
    });
    page = await context.newPage();
    trace = await startTrace(context);
    step = "verify authenticated state after forced restart";
    await page.goto("/templates", { waitUntil: "networkidle" });
    assert.equal(page.url(), `${baseUrl}/templates`);
    await waitForText(page, "body", "Local accounts");
    assert.equal((await sessionRequest(page, "/api/v1/session")).status, 200);
    assert.equal(
      (await sessionRequest(page, "/api/v1/logout", { method: "POST", mutate: true })).status,
      204,
    );
    assert.equal((await sessionRequest(page, "/api/v1/session")).status, 401);
    await discardTrace(trace);
  } catch (error) {
    await retainFailureArtifacts({
      artifactRoot,
      profile,
      step,
      pages: [{ name: "recovery-verification", page }],
      traces: trace ? [{ name: "recovery-verification", trace }] : [],
      error,
    });
    throw error;
  } finally {
    if (context !== null) await context.close();
    if (browser !== null) await browser.close();
  }
});
