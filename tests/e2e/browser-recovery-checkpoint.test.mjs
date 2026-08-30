import assert from "node:assert/strict";
import { access } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright-core";

import {
  discardTrace,
  login,
  retainFailureArtifacts,
  startTrace,
} from "./browser-helpers.mjs";

const baseUrl = process.env.MARKWEAVE_E2E_BASE_URL;
const recoveryState = process.env.MARKWEAVE_E2E_RECOVERY_STATE;
const username = process.env.MARKWEAVE_E2E_ADMIN_USERNAME;
const password = process.env.MARKWEAVE_E2E_ADMIN_PASSWORD;
const profile = process.env.MARKWEAVE_E2E_PROFILE;
const artifactRoot = process.env.MARKWEAVE_E2E_ARTIFACT_DIR;

test("authenticated browser state is checkpointed before forced restart", {
  skip: baseUrl && recoveryState && username && password && profile && artifactRoot
    ? false : "recovery settings unavailable",
}, async () => {
  assert.ok(
    baseUrl && recoveryState && username && password && profile && artifactRoot,
    "recovery settings are required",
  );
  assert.equal(path.isAbsolute(recoveryState), true, "recovery state path must be absolute");

  let browser = null;
  let context = null;
  let page = null;
  let trace = null;
  let step = "launch Chromium for recovery checkpoint";
  try {
    browser = await chromium.launch({
      executablePath: process.env.MARKWEAVE_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
      headless: true,
    });
    context = await browser.newContext({ baseURL: baseUrl, serviceWorkers: "block" });
    page = await context.newPage();
    step = "login before recovery checkpoint";
    await login(page, baseUrl, username, password);
    trace = await startTrace(context);
    step = "write authenticated recovery checkpoint";
    await context.storageState({ path: recoveryState });
    await access(recoveryState);
    await discardTrace(trace);
  } catch (error) {
    await retainFailureArtifacts({
      artifactRoot,
      profile,
      step,
      pages: [{ name: "recovery-checkpoint", page }],
      traces: trace ? [{ name: "recovery-checkpoint", trace }] : [],
      error,
    });
    throw error;
  } finally {
    if (context !== null) await context.close();
    if (browser !== null) await browser.close();
  }
});
