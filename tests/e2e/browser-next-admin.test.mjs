import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import test from "node:test";

import { chromium } from "playwright-core";

const baseURL = "http://localhost:3100";
const templateFixture = "/evidence/browser-template.docx";

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
  return page.evaluate(
    async ({ method, path, body, headers }) => {
      const csrf = document.cookie
        .split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith("__Host-md_converter_csrf="))
        ?.split("=", 2)[1];
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
    { method, path, body, headers },
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
    try {
      for (const _name of ["admin", "alice", "bob"])
        contexts.push(
          await browser.newContext({ baseURL, serviceWorkers: "block" }),
        );
      const [adminContext, aliceContext, bobContext] = contexts;
      const adminPage = await adminContext.newPage();
      const alicePage = await aliceContext.newPage();
      const bobPage = await bobContext.newPage();

      await login(adminContext, adminPage, "e2e-admin", "e2e-admin-password");
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
      await alicePage
        .getByText("Administrator access is required.")
        .waitFor();
      assert.equal(forbiddenUserLists, 0);
      alicePage.off("request", countForbiddenUserLists);
      await alicePage.getByRole("link", { name: "Templates" }).click();
      await alicePage.waitForURL("**/templates");
      await alicePage.getByRole("textbox", { name: "Name" }).fill(templateName);
      await alicePage
        .getByRole("textbox", { name: "Description" })
        .fill("Owner body");
      await alicePage
        .getByRole("textbox", { name: /Expected fonts/ })
        .fill(" Carlito, Caladea ");
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
      assert.deepEqual(createdVersions.body[0].declared_fonts, [
        "Carlito",
        "Caladea",
      ]);
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
      const staleResponse = alicePage.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v1/templates/${template.id}`) &&
          response.request().method() === "PATCH" &&
          response.status() === 412,
      );
      await alicePage.getByRole("button", { name: "Save details" }).click();
      await staleResponse;
      await alicePage.getByText(/changed on the server/).waitFor();
      await alicePage
        .getByRole("textbox", { name: "Template name" })
        .fill(renamedTemplate);
      await alicePage.getByRole("button", { name: "Save details" }).click();
      await alicePage.getByText("Template details updated.").waitFor();

      await replace(alicePage, renamedTemplate, "Carlito, Caladea");
      let versions = await api(
        alicePage,
        "GET",
        `/api/v1/templates/${template.id}/versions`,
      );
      assert.deepEqual(versions.body[0].declared_fonts, ["Carlito", "Caladea"]);
      await replace(alicePage, renamedTemplate, "   ");
      versions = await api(
        alicePage,
        "GET",
        `/api/v1/templates/${template.id}/versions`,
      );
      assert.deepEqual(versions.body[0].declared_fonts, []);

      await manage(alicePage, renamedTemplate);
      const oldest = versions.body.at(-1);
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
      await alicePage.reload({ waitUntil: "networkidle" });
      await alicePage.waitForURL("**/login");
      await alicePage
        .getByText("Your session ended. Please sign in again.")
        .waitFor();

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
      await Promise.allSettled(contexts.map((context) => context.close()));
      await browser.close();
    }
  },
);
