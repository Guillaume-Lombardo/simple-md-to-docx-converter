import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import test from "node:test";

import { chromium } from "playwright-core";
import { cookieValue } from "./browser-next-admin-helpers.mjs";

const baseURL = "http://localhost:3100";
const csrfCookieName = "__Host-md_converter_csrf";
const templateFixture = "/evidence/browser-template.docx";
const expectedFonts = [
  "Aptos",
  "Aptos Display",
  "Calibri",
  "Cambria",
  "Cambria Math",
  "Consolas",
  "Courier New",
  "Times New Roman",
];

async function login(context, page, username, password) {
  await context.clearCookies();
  await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
  await page.getByRole("textbox", { name: "Username" }).fill(username);
  await page.getByLabel("Password").fill(password);
  await Promise.all([
    page.waitForURL("**/convert"),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);
}

async function api(page, method, path, body, headers = {}) {
  const csrf = cookieValue(
    await page.evaluate(() => document.cookie),
    csrfCookieName,
  );
  return page.evaluate(
    async ({ method, path, body, headers, csrf }) => {
      const response = await fetch(path, {
        method,
        cache: "no-store",
        credentials: "same-origin",
        headers: {
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          ...(csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {}),
          ...headers,
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      return {
        body: await response.json().catch(() => null),
        etag: response.headers.get("etag"),
        status: response.status,
      };
    },
    { method, path, body, headers, csrf },
  );
}

function templateCard(page, name) {
  return page
    .getByRole("list", { name: "Visible templates" })
    .locator("li")
    .filter({ has: page.getByRole("heading", { name, exact: true }) });
}

function userCard(page, username) {
  return page
    .getByRole("list", { name: "Local accounts" })
    .locator("li")
    .filter({
      has: page.getByRole("heading", { name: username, exact: true }),
    });
}

function requiredPositiveInteger(name) {
  const value = Number(process.env[name]);
  assert.ok(Number.isSafeInteger(value) && value > 0, `${name} is required`);
  return value;
}

function alternateDuration(policy, role, excluded = []) {
  const bounds = policy[`${role}_idle_minutes_bounds`];
  const maximum = Math.min(
    bounds.maximum_minutes,
    Math.floor(policy.absolute_lifetime_seconds / 60),
  );
  for (
    let value = bounds.minimum_minutes;
    value <= maximum;
    value += policy.idle_minutes_granularity
  ) {
    if (!excluded.includes(value)) return value;
  }
  assert.fail(`No alternate ${role} policy duration is available`);
}

async function manage(page, name) {
  await templateCard(page, name)
    .getByRole("button", { name: "Manage" })
    .click();
  await page.getByRole("heading", { name: `Manage ${name}` }).waitFor();
}

async function replace(page, name, fonts) {
  await manage(page, name);
  await page
    .getByRole("textbox", { name: /Replacement expected fonts/ })
    .fill(fonts);
  await page.getByLabel("Replacement DOCX").setInputFiles(templateFixture);
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/templates/") &&
      response.url().endsWith("/content") &&
      response.request().method() === "PUT",
    { timeout: 120_000 },
  );
  await page.getByRole("button", { name: "Replace content" }).click();
  assert.equal((await responsePromise).status(), 201);
  await page.getByText("Template content replaced.").waitFor();
}

async function assertRenderedTemplateDownload(page, button, filenamePattern) {
  const downloadPromise = page.waitForEvent("download");
  await button.click();
  const download = await downloadPromise;
  assert.match(download.suggestedFilename(), filenamePattern);
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  assert.deepEqual(
    [...Buffer.concat(chunks).subarray(0, 4)],
    [0x50, 0x4b, 0x03, 0x04],
  );
}

test(
  "Next administration preserves template and account authority in the final image",
  { timeout: 360_000 },
  async () => {
    const profile = process.env.MARKWEAVE_E2E_PROFILE;
    assert.ok(profile === "standalone" || profile === "distributed");
    const suffix = `${profile}-${randomUUID().slice(0, 8)}`;
    const templateName = `Next administration ${suffix}`;
    const renamedTemplate = `${templateName} renamed`;
    const alice = {
      username: `next-admin-alice-${suffix}`,
      password: `Alice-${suffix}-password`,
    };
    const bob = {
      username: `next-admin-bob-${suffix}`,
      password: `Bob-${suffix}-password`,
    };
    const browser = await chromium.launch({
      executablePath:
        process.env.MARKWEAVE_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
      headless: true,
    });
    const contexts = [];
    let adminPage;
    let originalPolicy;
    try {
      for (const _name of ["admin", "alice", "bob"])
        contexts.push(
          await browser.newContext({ baseURL, serviceWorkers: "block" }),
        );
      const [adminContext, aliceContext, bobContext] = contexts;
      adminPage = await adminContext.newPage();
      const alicePage = await aliceContext.newPage();
      const bobPage = await bobContext.newPage();

      await login(adminContext, adminPage, "e2e-admin", "e2e-admin-password");

      const checkpointUser = requiredPositiveInteger(
        "MARKWEAVE_E2E_CHECKPOINT_USER_IDLE_MINUTES",
      );
      const checkpointAdmin = requiredPositiveInteger(
        "MARKWEAVE_E2E_CHECKPOINT_ADMIN_IDLE_MINUTES",
      );
      const checkpointRevision = requiredPositiveInteger(
        "MARKWEAVE_E2E_CHECKPOINT_POLICY_REVISION",
      );
      originalPolicy = await api(
        adminPage,
        "GET",
        "/api/v1/admin/session-policy",
      );
      assert.equal(originalPolicy.status, 200);
      assert.ok(originalPolicy.etag);
      assert.equal(originalPolicy.body.user_idle_minutes, checkpointUser);
      assert.equal(originalPolicy.body.admin_idle_minutes, checkpointAdmin);
      assert.equal(originalPolicy.body.revision, checkpointRevision);

      await adminPage.getByRole("link", { name: "Session policy" }).click();
      await adminPage.waitForURL("**/session-policy");
      await adminPage
        .getByRole("heading", { name: "Session policy", exact: true })
        .waitFor();
      assert.equal(
        await adminPage
          .getByRole("textbox", { name: "User inactivity duration (minutes)" })
          .inputValue(),
        String(checkpointUser),
      );
      assert.equal(
        await adminPage
          .getByRole("textbox", {
            name: "Administrator inactivity duration (minutes)",
          })
          .inputValue(),
        String(checkpointAdmin),
      );
      await adminPage
        .getByText(`${checkpointRevision}`, { exact: true })
        .waitFor();
      await adminPage
        .getByText(`${originalPolicy.body.absolute_lifetime_seconds} seconds`)
        .waitFor();

      let policyPuts = 0;
      const countPolicyPuts = (request) => {
        if (
          request.url().endsWith("/api/v1/admin/session-policy") &&
          request.method() === "PUT"
        )
          policyPuts += 1;
      };
      const externalUser = alternateDuration(originalPolicy.body, "user", [
        checkpointUser,
      ]);
      const externalAdmin = alternateDuration(originalPolicy.body, "admin", [
        checkpointAdmin,
      ]);
      const externallyChanged = await api(
        adminPage,
        "PUT",
        "/api/v1/admin/session-policy",
        {
          admin_idle_minutes: externalAdmin,
          user_idle_minutes: externalUser,
        },
        { "If-Match": originalPolicy.etag },
      );
      assert.equal(externallyChanged.status, 200);
      adminPage.on("request", countPolicyPuts);
      const desiredUser = alternateDuration(originalPolicy.body, "user", [
        checkpointUser,
        externalUser,
      ]);
      const desiredAdmin = alternateDuration(originalPolicy.body, "admin", [
        checkpointAdmin,
        externalAdmin,
      ]);
      await adminPage
        .getByRole("textbox", { name: "User inactivity duration (minutes)" })
        .fill(String(desiredUser));
      await adminPage
        .getByRole("textbox", {
          name: "Administrator inactivity duration (minutes)",
        })
        .fill(String(desiredAdmin));
      const stalePolicy = adminPage.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/admin/session-policy") &&
          response.request().method() === "PUT" &&
          response.status() === 412,
      );
      await adminPage
        .getByRole("button", { name: "Save session policy" })
        .click();
      await stalePolicy;
      await adminPage.getByText(/changed on the server/).waitFor();
      assert.equal(
        await adminPage
          .getByRole("textbox", { name: "User inactivity duration (minutes)" })
          .inputValue(),
        String(externalUser),
      );
      await adminPage
        .getByRole("textbox", { name: "User inactivity duration (minutes)" })
        .fill(String(desiredUser));
      await adminPage
        .getByRole("textbox", {
          name: "Administrator inactivity duration (minutes)",
        })
        .fill(String(desiredAdmin));
      const savedPolicy = adminPage.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/admin/session-policy") &&
          response.request().method() === "PUT" &&
          response.status() === 200,
      );
      const authoritativeRefresh = adminPage.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/admin/session-policy") &&
          response.request().method() === "GET" &&
          response.status() === 200,
      );
      await adminPage
        .getByRole("button", { name: "Save session policy" })
        .click();
      const savedPolicyResponse = await savedPolicy;
      assert.deepEqual(await savedPolicyResponse.request().postDataJSON(), {
        admin_idle_minutes: desiredAdmin,
        user_idle_minutes: desiredUser,
      });
      const refreshedPolicy = await (await authoritativeRefresh).json();
      assert.equal(refreshedPolicy.user_idle_minutes, desiredUser);
      assert.equal(refreshedPolicy.admin_idle_minutes, desiredAdmin);
      await adminPage.getByText("Session policy updated.").waitFor();
      assert.equal(
        await adminPage
          .getByRole("textbox", { name: "User inactivity duration (minutes)" })
          .inputValue(),
        String(desiredUser),
      );
      assert.equal(
        policyPuts,
        2,
        "a stale or successful policy PUT was replayed",
      );
      const authoritativePolicy = await api(
        adminPage,
        "GET",
        "/api/v1/admin/session-policy",
      );
      assert.equal(authoritativePolicy.body.user_idle_minutes, desiredUser);
      assert.equal(authoritativePolicy.body.admin_idle_minutes, desiredAdmin);
      assert.ok(authoritativePolicy.body.revision > checkpointRevision);
      const restoredPolicy = await api(
        adminPage,
        "PUT",
        "/api/v1/admin/session-policy",
        {
          admin_idle_minutes: checkpointAdmin,
          user_idle_minutes: checkpointUser,
        },
        { "If-Match": authoritativePolicy.etag },
      );
      assert.equal(restoredPolicy.status, 200);
      adminPage.off("request", countPolicyPuts);

      await adminPage.getByRole("link", { name: "Users" }).click();
      await adminPage.waitForURL("**/users");
      await adminPage
        .getByRole("heading", { name: "Local accounts" })
        .waitFor();

      await adminPage
        .getByRole("textbox", { name: "Username" })
        .fill(alice.username);
      await adminPage.getByLabel("Temporary password").fill(alice.password);
      const aliceResponse = adminPage.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/admin/users") &&
          response.request().method() === "POST",
      );
      let creationRequests = 0;
      const countCreation = (request) => {
        if (
          request.url().endsWith("/api/v1/admin/users") &&
          request.method() === "POST"
        )
          creationRequests += 1;
      };
      adminPage.on("request", countCreation);
      await adminPage
        .getByRole("button", { name: "Create account" })
        .evaluate((button) => {
          const form = button.closest("form");
          form.dispatchEvent(
            new Event("submit", { bubbles: true, cancelable: true }),
          );
          form.dispatchEvent(
            new Event("submit", { bubbles: true, cancelable: true }),
          );
        });
      assert.equal((await aliceResponse).status(), 201);
      await adminPage.getByText("Account created.").waitFor();
      assert.equal(
        creationRequests,
        1,
        "duplicate account submission escaped the UI guard",
      );
      adminPage.off("request", countCreation);

      await adminPage
        .getByRole("textbox", { name: "Username" })
        .fill(bob.username);
      await adminPage.getByLabel("Temporary password").fill(bob.password);
      await Promise.all([
        adminPage.waitForResponse(
          (response) =>
            response.url().endsWith("/api/v1/admin/users") &&
            response.request().method() === "POST" &&
            response.status() === 201,
        ),
        adminPage.getByRole("button", { name: "Create account" }).click(),
      ]);
      await adminPage.getByText("Account created.").waitFor();

      const userListRequests = [];
      const countUserLists = (request) => {
        if (
          request.url().endsWith("/api/v1/admin/users") &&
          request.method() === "GET"
        )
          userListRequests.push(request.url());
      };
      adminPage.on("request", countUserLists);
      await adminPage
        .getByRole("textbox", { name: "Search by username" })
        .fill(alice.username);
      await userCard(adminPage, alice.username).waitFor();
      assert.equal(await userCard(adminPage, bob.username).count(), 0);
      assert.equal(
        userListRequests.length,
        0,
        "local account search contacted the API",
      );
      adminPage.off("request", countUserLists);

      await login(aliceContext, alicePage, alice.username, alice.password);
      await login(bobContext, bobPage, bob.username, bob.password);
      let forbiddenUserLists = 0;
      const countForbiddenUserLists = (request) => {
        if (
          request.url().endsWith("/api/v1/admin/users") &&
          request.method() === "GET"
        )
          forbiddenUserLists += 1;
      };
      alicePage.on("request", countForbiddenUserLists);
      await alicePage.goto(`${baseURL}/users`, { waitUntil: "networkidle" });
      await alicePage.getByText("Administrator access is required.").waitFor();
      assert.equal(forbiddenUserLists, 0);
      alicePage.off("request", countForbiddenUserLists);
      let forbiddenPolicyGets = 0;
      const countForbiddenPolicyGets = (request) => {
        if (
          request.url().endsWith("/api/v1/admin/session-policy") &&
          request.method() === "GET"
        )
          forbiddenPolicyGets += 1;
      };
      alicePage.on("request", countForbiddenPolicyGets);
      await alicePage.goto(`${baseURL}/session-policy`, {
        waitUntil: "networkidle",
      });
      await alicePage.getByText("Administrator access is required.").waitFor();
      assert.equal(forbiddenPolicyGets, 0);
      assert.equal(
        (await api(alicePage, "GET", "/api/v1/admin/session-policy")).status,
        403,
      );
      alicePage.off("request", countForbiddenPolicyGets);
      await alicePage.getByRole("link", { name: "Templates" }).click();
      await alicePage.waitForURL("**/templates");
      await alicePage.getByRole("textbox", { name: "Name" }).fill(templateName);
      await alicePage
        .getByRole("textbox", { name: "Description" })
        .fill("Owner body");
      await alicePage
        .getByRole("textbox", { name: /Expected fonts/ })
        .fill(` ${expectedFonts.join(", ")} `);
      await alicePage.getByLabel("DOCX file").setInputFiles(templateFixture);
      const createdResponse = alicePage.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/templates") &&
          response.request().method() === "POST",
        { timeout: 120_000 },
      );
      await alicePage.getByRole("button", { name: "Create template" }).click();
      const created = await createdResponse;
      assert.equal(created.status(), 201);
      const template = await created.json();
      await alicePage.getByText("Template created.").waitFor();
      const createdVersions = await api(
        alicePage,
        "GET",
        `/api/v1/templates/${template.id}/versions`,
      );
      assert.equal(createdVersions.status, 200);
      assert.deepEqual(createdVersions.body[0].declared_fonts, expectedFonts);
      const download = await alicePage.evaluate(async (templateId) => {
        const response = await fetch(`/api/v1/templates/${templateId}/content`);
        return {
          bytes: [...new Uint8Array(await response.arrayBuffer()).slice(0, 4)],
          cache: response.headers.get("cache-control"),
          disposition: response.headers.get("content-disposition"),
          status: response.status,
        };
      }, template.id);
      assert.equal(download.status, 200);
      assert.deepEqual(download.bytes, [0x50, 0x4b, 0x03, 0x04]);
      assert.equal(download.cache, "private, no-store");
      assert.match(download.disposition, /^attachment;/i);
      await assertRenderedTemplateDownload(
        alicePage,
        templateCard(alicePage, templateName).getByRole("button", {
          name: "Download current DOCX",
        }),
        new RegExp(`^template-${template.id}-v1\\.docx$`),
      );

      await bobPage.getByRole("link", { name: "Templates" }).click();
      await bobPage.waitForURL("**/templates");
      const bobView = templateCard(bobPage, templateName);
      await bobView.waitFor();
      await bobView
        .getByText(`Owner: ${alice.username}`, { exact: false })
        .waitFor();
      assert.equal(
        await bobView.getByRole("button", { name: "Manage" }).count(),
        0,
      );
      assert.equal(
        (
          await api(
            bobPage,
            "PUT",
            `/api/v1/templates/${template.id}/system-fallback`,
          )
        ).status,
        403,
      );

      await manage(alicePage, templateName);
      const snapshot = await api(
        alicePage,
        "GET",
        `/api/v1/templates/${template.id}`,
      );
      assert.equal(snapshot.status, 200);
      assert.ok(snapshot.etag);
      assert.equal(
        (
          await api(
            alicePage,
            "PATCH",
            `/api/v1/templates/${template.id}`,
            { description: "Concurrent body", name: templateName },
            { "If-Match": snapshot.etag },
          )
        ).status,
        200,
      );
      await alicePage
        .getByRole("textbox", { name: "Template name" })
        .fill(renamedTemplate);
      let metadataPatches = 0;
      const countMetadataPatches = (request) => {
        if (
          request.url().endsWith(`/api/v1/templates/${template.id}`) &&
          request.method() === "PATCH"
        )
          metadataPatches += 1;
      };
      alicePage.on("request", countMetadataPatches);
      const staleResponse = alicePage.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v1/templates/${template.id}`) &&
          response.request().method() === "PATCH" &&
          response.status() === 412,
      );
      await alicePage.getByRole("button", { name: "Save details" }).click();
      await staleResponse;
      await alicePage.getByText(/changed on the server/).waitFor();
      await alicePage.getByRole("textbox", { name: "Template name" }).waitFor();
      assert.equal(
        await alicePage
          .getByRole("textbox", { name: "Template name" })
          .inputValue(),
        templateName,
      );
      assert.equal(
        await alicePage
          .getByRole("textbox", { name: "Template description" })
          .inputValue(),
        "Concurrent body",
      );
      assert.equal(
        metadataPatches,
        1,
        "stale metadata was automatically replayed",
      );
      await alicePage
        .getByRole("textbox", { name: "Template name" })
        .fill(renamedTemplate);
      await alicePage.getByRole("button", { name: "Save details" }).click();
      await alicePage.getByText("Template details updated.").waitFor();
      assert.equal(metadataPatches, 2);
      alicePage.off("request", countMetadataPatches);

      await replace(alicePage, renamedTemplate, expectedFonts.join(", "));
      let versions = await api(
        alicePage,
        "GET",
        `/api/v1/templates/${template.id}/versions`,
      );
      assert.deepEqual(versions.body[0].declared_fonts, expectedFonts);
      await replace(alicePage, renamedTemplate, "   ");
      versions = await api(
        alicePage,
        "GET",
        `/api/v1/templates/${template.id}/versions`,
      );
      assert.deepEqual(versions.body[0].declared_fonts, []);

      await manage(alicePage, renamedTemplate);
      const oldest = versions.body.at(-1);
      await assertRenderedTemplateDownload(
        alicePage,
        alicePage.getByRole("button", {
          name: `Download version ${oldest.number}`,
        }),
        new RegExp(`^template-${template.id}-v${oldest.number}\\.docx$`),
      );
      const restoreResponse = alicePage.waitForResponse(
        (response) =>
          response.url().endsWith(`/versions/${oldest.id}/restore`) &&
          response.request().method() === "POST",
        { timeout: 120_000 },
      );
      await alicePage
        .getByRole("button", { name: `Restore version ${oldest.number}` })
        .click();
      assert.equal((await restoreResponse).status(), 201);
      await alicePage
        .getByText(`Version ${oldest.number} restored as a new version.`)
        .waitFor();
      await templateCard(alicePage, renamedTemplate)
        .getByRole("button", { name: /Make preferred|Preferred/ })
        .click();
      await alicePage.getByText("Preferred template updated.").waitFor();
      await alicePage
        .getByRole("button", { name: "Clear preferred template" })
        .click();
      await alicePage.getByText("Preferred template cleared.").waitFor();

      await login(adminContext, adminPage, "e2e-admin", "e2e-admin-password");
      await adminPage.goto(`${baseURL}/templates`, {
        waitUntil: "networkidle",
      });
      const initialContext = await api(
        adminPage,
        "GET",
        "/api/v1/template-context",
      );
      assert.equal(initialContext.status, 200);
      assert.ok(initialContext.body.system_fallback_template_id);
      const candidate = templateCard(adminPage, renamedTemplate);
      await candidate.waitFor();
      await candidate
        .getByRole("button", { name: "Set system fallback" })
        .click();
      await adminPage.getByText("System fallback updated.").waitFor();
      await manage(adminPage, renamedTemplate);
      await adminPage.getByRole("button", { name: "Archive template" }).click();
      let confirmation = adminPage.getByRole("dialog", {
        name: "Archive template?",
      });
      const archiveResponse = adminPage.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v1/templates/${template.id}/archive`) &&
          response.request().method() === "POST",
      );
      await confirmation.getByRole("button", { name: "Confirm" }).click();
      assert.equal((await archiveResponse).status(), 200);
      await adminPage.getByText("Template archived.").waitFor();
      await manage(adminPage, renamedTemplate);
      await adminPage
        .getByRole("button", { name: "Delete template permanently" })
        .click();
      confirmation = adminPage.getByRole("dialog", {
        name: "Delete template permanently?",
      });
      const guardedDelete = adminPage.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v1/templates/${template.id}`) &&
          response.request().method() === "DELETE",
      );
      await confirmation.getByRole("button", { name: "Confirm" }).click();
      assert.equal((await guardedDelete).status(), 409);
      await adminPage
        .getByText("The requested value already exists.")
        .waitFor();
      assert.equal(
        (
          await api(
            adminPage,
            "PUT",
            `/api/v1/templates/${initialContext.body.system_fallback_template_id}/system-fallback`,
          )
        ).status,
        204,
      );
      const deleteResponse = adminPage.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v1/templates/${template.id}`) &&
          response.request().method() === "DELETE",
      );
      await confirmation.getByRole("button", { name: "Confirm" }).click();
      assert.equal((await deleteResponse).status(), 204);
      await adminPage.getByText("Template deleted.").waitFor();

      await adminPage.goto(`${baseURL}/users`, { waitUntil: "networkidle" });
      await adminPage
        .getByRole("textbox", { name: "Search by username" })
        .fill(alice.username);
      await userCard(adminPage, alice.username)
        .getByRole("button", { name: `Reset password for ${alice.username}` })
        .click();
      const resetDialog = adminPage.getByRole("dialog", {
        name: `Reset password for ${alice.username}`,
      });
      await resetDialog
        .getByLabel("New temporary password")
        .fill(`${alice.password}-reset`);
      await resetDialog.getByRole("checkbox").check();
      await resetDialog.getByRole("button", { name: "Reset password" }).click();
      await adminPage
        .getByText("Password reset and sessions revoked.")
        .waitFor();
      let expiredDownloads = 0;
      const countExpiredDownloads = (request) => {
        if (
          request.url().endsWith(`/api/v1/templates/${template.id}/content`) &&
          request.method() === "GET"
        )
          expiredDownloads += 1;
      };
      alicePage.on("request", countExpiredDownloads);
      const expiredDownload = alicePage.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v1/templates/${template.id}/content`) &&
          response.status() === 401,
      );
      await templateCard(alicePage, renamedTemplate)
        .getByRole("button", { name: "Download current DOCX" })
        .click();
      await expiredDownload;
      await alicePage.waitForURL("**/login");
      await alicePage
        .getByText("Your session ended. Please sign in again.")
        .waitFor();
      assert.equal(expiredDownloads, 1, "expired download was replayed");
      alicePage.off("request", countExpiredDownloads);

      await adminPage
        .getByRole("textbox", { name: "Search by username" })
        .fill(bob.username);
      await userCard(adminPage, bob.username)
        .getByRole("button", { name: `Deactivate ${bob.username}` })
        .click();
      await adminPage
        .getByText("Account deactivated and sessions revoked.")
        .waitFor();
      await bobPage.reload({ waitUntil: "networkidle" });
      await bobPage.waitForURL("**/login");
      await bobPage
        .getByText("Your session ended. Please sign in again.")
        .waitFor();
    } finally {
      if (adminPage && originalPolicy?.etag) {
        try {
          const current = await api(
            adminPage,
            "GET",
            "/api/v1/admin/session-policy",
          );
          if (
            current.status === 200 &&
            (current.body.user_idle_minutes !==
              originalPolicy.body.user_idle_minutes ||
              current.body.admin_idle_minutes !==
                originalPolicy.body.admin_idle_minutes)
          )
            await api(
              adminPage,
              "PUT",
              "/api/v1/admin/session-policy",
              {
                admin_idle_minutes: originalPolicy.body.admin_idle_minutes,
                user_idle_minutes: originalPolicy.body.user_idle_minutes,
              },
              { "If-Match": current.etag },
            );
        } catch {
          console.warn("Policy rollback could not be completed.");
        }
      }
      await Promise.allSettled(contexts.map((context) => context.close()));
      await browser.close();
    }
  },
);
