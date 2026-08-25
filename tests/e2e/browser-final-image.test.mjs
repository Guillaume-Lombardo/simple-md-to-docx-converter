import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import test from "node:test";

import { chromium } from "playwright-core";

import {
  assertDownloadedResult,
  configuration,
  discardTrace,
  login,
  retainFailureArtifacts,
  sessionRequest,
  startTrace,
  waitForText,
} from "./browser-helpers.mjs";

const EXPECTED_FONTS = [
  "Aptos", "Aptos Display", "Calibri", "Cambria", "Cambria Math", "Consolas",
  "Courier New", "Times New Roman",
].join(", ");

async function createAccount(page, username, password) {
  const form = page.locator("#create-user-form");
  await form.locator('input[name="username"]').fill(username);
  await form.locator('input[name="password"]').fill(password);
  const responsePromise = page.waitForResponse(
    (response) => response.url().endsWith("/api/v1/admin/users")
      && response.request().method() === "POST",
  );
  await form.getByRole("button", { name: "Create account" }).click();
  const response = await responsePromise;
  assert.equal(response.status(), 201, "administrator could not create a regular user");
  await waitForText(page, "#administration-alert", "Account was created.");
  return response.json();
}

async function createTemplate(page, templateName, templateFixture) {
  const form = page.locator("#create-template-form");
  await form.locator('input[name="name"]').fill(templateName);
  await form.locator('textarea[name="description"]').fill("Final-image browser acceptance");
  await form.locator('input[name="expected_fonts"]').fill(EXPECTED_FONTS);
  await form.locator('input[name="content"]').setInputFiles(templateFixture);
  const responsePromise = page.waitForResponse(
    (response) => response.url().endsWith("/api/v1/templates")
      && response.request().method() === "POST",
  );
  await form.getByRole("button", { name: "Create template" }).click();
  const response = await responsePromise;
  assert.equal(response.status(), 201, "template creation failed");
  await waitForText(page, "#administration-alert", "Template was created.");
  await waitForText(page, "#managed-template-list", templateName);
  return response.json();
}

async function selectTemplate(page, templateName) {
  await page.locator("#template-search").fill(templateName);
  const choice = page.locator("#template-results button").filter({ hasText: templateName });
  await choice.waitFor();
  await choice.click();
  await waitForText(page, "#selected-template", templateName);
}

async function convertAndDownload(page, sourceFixture, output, timeout) {
  await page.locator("#source").setInputFiles(sourceFixture);
  await page.locator(`input[name="output"][value="${output}"]`).check();
  const responsePromise = page.waitForResponse(
    (response) => response.url().endsWith("/api/v1/conversions")
      && response.request().method() === "POST",
  );
  await page.locator("#submit-conversion").click();
  const accepted = await responsePromise;
  assert.equal(accepted.status(), 202, `${output} conversion was not accepted`);
  assert.ok(accepted.headers().location, `${output} response has no Location header`);
  assert.ok(accepted.headers()["retry-after"], `${output} response has no Retry-After header`);
  const job = await accepted.json();
  assert.equal(job.output, output);
  await waitForText(page, "#job-status", "ready to download", timeout);
  const href = await page.locator("#download-result").getAttribute("href");
  assert.ok(href, `${output} result link is missing`);
  const result = await page.context().request.get(href);
  assert.equal(result.status(), 200, `${output} result download failed`);
  assertDownloadedResult(output, result.headers(), await result.body());
  return job;
}

async function setAccountActive(page, username, active) {
  await page.locator("#user-search").fill(username);
  const card = page.locator("#user-list .management-card").filter({ hasText: username });
  await card.waitFor();
  await card.getByRole("button", { name: active ? "Reactivate" : "Deactivate" }).click();
  await waitForText(
    page,
    "#administration-alert",
    `${username} is now ${active ? "active" : "inactive"}.`,
  );
}

test("final rootless image supports the three-identity browser workflow", async () => {
  const settings = await configuration();
  const suffix = `${settings.profile}-${randomUUID().slice(0, 8)}`;
  const identities = {
    alice: { username: `e2e-alice-${suffix}`, password: `Alice-${suffix}-password` },
    bob: { username: `e2e-bob-${suffix}`, password: `Bob-${suffix}-password` },
  };
  const templateName = `E2E ${suffix} report`;
  let browser = null;
  const contexts = [];
  const pages = [];
  const traces = [];
  let step = "launch Chromium";
  try {
    browser = await chromium.launch({
      executablePath: settings.chromiumExecutable,
      headless: true,
    });
    step = "initialize browser contexts";
    for (const name of ["admin", "alice", "bob"]) {
      const context = await browser.newContext({
        baseURL: settings.baseUrl,
        acceptDownloads: false,
        serviceWorkers: "block",
      });
      const page = await context.newPage();
      contexts.push(context);
      pages.push({ name, page });
    }
    const [adminPage, alicePage, bobPage] = pages.map(({ page }) => page);

    step = "administrator login and cookie contract";
    await login(adminPage, settings.baseUrl, settings.adminUsername, settings.adminPassword);
    const sessionCookie = (await contexts[0].cookies(settings.baseUrl))
      .find((cookie) => cookie.name === "md_converter_session");
    assert.ok(sessionCookie, "session cookie is missing");
    assert.equal(sessionCookie.httpOnly, true);
    assert.equal(sessionCookie.secure, true);
    assert.equal(sessionCookie.sameSite, "Lax");

    step = "administrator creates Alice and Bob";
    await adminPage.goto("/templates", { waitUntil: "networkidle" });
    await waitForText(adminPage, "body", "Local accounts");
    await createAccount(adminPage, identities.alice.username, identities.alice.password);
    await createAccount(adminPage, identities.bob.username, identities.bob.password);

    step = "regular user logins";
    await login(alicePage, settings.baseUrl, identities.alice.username, identities.alice.password);
    await login(bobPage, settings.baseUrl, identities.bob.username, identities.bob.password);
    for (let index = 0; index < contexts.length; index += 1) {
      traces.push({ name: pages[index].name, trace: await startTrace(contexts[index]) });
    }

    step = "Alice creates a visible template";
    await alicePage.goto("/templates", { waitUntil: "networkidle" });
    assert.equal(await alicePage.locator("[data-admin-users]").count(), 0);
    const template = await createTemplate(alicePage, templateName, settings.templateFixture);

    step = "Bob sees but cannot manage Alice's template";
    await bobPage.goto("/templates", { waitUntil: "networkidle" });
    const bobTemplate = bobPage.locator("#managed-template-list .management-card")
      .filter({ hasText: templateName });
    await bobTemplate.waitFor();
    await waitForText(bobPage, "#managed-template-list", identities.alice.username);
    assert.equal(await bobTemplate.locator("details").count(), 0);

    step = "Alice converts and downloads DOCX, PDF, and combined results";
    await alicePage.goto("/convert", { waitUntil: "networkidle" });
    await selectTemplate(alicePage, templateName);
    const jobs = [];
    for (const output of ["docx", "pdf", "both"]) {
      jobs.push(await convertAndDownload(
        alicePage,
        settings.sourceFixture,
        output,
        settings.timeoutMilliseconds,
      ));
    }

    step = "cross-owner conversion access is denied";
    const privateJob = jobs[0];
    assert.equal(
      (await sessionRequest(bobPage, `/api/v1/conversions/${privateJob.id}`)).status,
      404,
    );
    assert.equal(
      (await sessionRequest(bobPage, `/api/v1/conversions/${privateJob.id}/result`)).status,
      404,
    );
    assert.equal(
      (await sessionRequest(adminPage, `/api/v1/conversions/${privateJob.id}`)).status,
      200,
    );

    step = "Bob logs out and loses the session";
    assert.equal(
      (await sessionRequest(bobPage, "/api/v1/logout", { method: "POST", mutate: true })).status,
      204,
    );
    assert.equal((await sessionRequest(bobPage, "/api/v1/session")).status, 401);
    await bobPage.goto("/convert", { waitUntil: "networkidle" });
    assert.equal(bobPage.url(), `${settings.baseUrl}/login`);

    step = "administrator deactivation revokes Alice's session";
    await adminPage.goto("/templates", { waitUntil: "networkidle" });
    await setAccountActive(adminPage, identities.alice.username, false);
    assert.equal((await sessionRequest(alicePage, "/api/v1/session")).status, 401);
    await setAccountActive(adminPage, identities.alice.username, true);
    assert.equal((await sessionRequest(alicePage, "/api/v1/session")).status, 401);

    assert.equal(template.owner_id, jobs[0].owner_id);
    step = "discard successful traces";
    await Promise.all(traces.map(({ trace }) => discardTrace(trace)));
  } catch (error) {
    await retainFailureArtifacts({
      artifactRoot: settings.artifactRoot,
      profile: settings.profile,
      step,
      pages,
      traces,
      error,
    });
    throw error;
  } finally {
    await Promise.allSettled(contexts.map((context) => context.close()));
    if (browser !== null) await browser.close();
  }
});
