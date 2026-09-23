import assert from "node:assert/strict";
import test from "node:test";

import { chromium } from "playwright-core";

const baseURL = process.env.MARKWEAVE_E2E_BASE_URL || "http://localhost:3100";
const admin = {
  active: true,
  effective_idle_minutes: 60,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "admin",
  username: "Composer administrator",
};
const alice = {
  ...admin,
  id: "00000000-0000-4000-8000-000000000002",
  role: "user",
  username: "Composer Alice",
};

function connection(id, overrides = {}) {
  return {
    allowed_user_ids: [],
    authorized: true,
    client_certificate_present: false,
    credential_present: false,
    enabled: false,
    endpoint: "https://llm.example/v1",
    etag: '"connection-1"',
    id,
    identity_mode: "shared",
    internal_ca_present: false,
    name: "Composer connection",
    permitted_models: ["approved-model"],
    scope: "personal",
    selected_model: null,
    status: "disabled",
    status_message: "Enable the connection to continue setup.",
    ...overrides,
  };
}

async function fulfill(route, body, status = 200, headers = {}) {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers,
    body: JSON.stringify(body),
  });
}

function installRoutes(page, identity, state) {
  page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (path === "/api/v1/session") {
      await fulfill(route, identity, 200, {
        "set-cookie":
          "__Host-md_converter_csrf=composer-e2e-csrf; Path=/; Secure; SameSite=Strict",
      });
      return;
    }
    assert.match(path, /^\/api\/v1\/composer\//);
    if (method !== "GET")
      assert.equal(
        request.headers()["x-csrf-token"],
        "composer-e2e-csrf",
        "Composer mutations must retain the authenticated CSRF boundary",
      );
    const isAdmin = identity.role === "admin";
    const visibleConnections = isAdmin
      ? state.adminConnections
      : state.personalAllowed
        ? state.aliceConnections
        : [];
    if (path === "/api/v1/composer/capabilities" && method === "GET") {
      await fulfill(route, {
        instance_connections_manageable: isAdmin,
        maximum_upload_bytes: 1000000,
        personal_connections_allowed: isAdmin ? false : state.personalAllowed,
        status: !isAdmin && !state.personalAllowed ? "unauthorized" : "ready",
        status_message:
          !isAdmin && !state.personalAllowed
            ? "Personal connections require an administrator grant."
            : null,
      });
      return;
    }
    if (path === "/api/v1/composer/personal-permissions" && method === "GET") {
      assert.equal(isAdmin, true);
      await fulfill(route, {
        limit: Number(url.searchParams.get("limit")),
        offset: Number(url.searchParams.get("offset")),
        permissions: [
          {
            allowed: state.personalAllowed,
            etag: state.permissionEtag,
            user_id: alice.id,
            username: alice.username,
          },
        ],
      });
      return;
    }
    if (
      path === `/api/v1/composer/personal-permissions/${alice.id}` &&
      method === "PUT"
    ) {
      assert.equal(isAdmin, true);
      assert.equal(request.headers()["if-match"], state.permissionEtag);
      assert.deepEqual(request.postDataJSON(), { allowed: true });
      state.personalAllowed = true;
      state.permissionEtag = '"permission-2"';
      await fulfill(route, {
        allowed: true,
        etag: state.permissionEtag,
        user_id: alice.id,
        username: alice.username,
      });
      return;
    }
    if (path === "/api/v1/composer/connections" && method === "GET") {
      await fulfill(route, {
        connections: visibleConnections,
        limit: Number(url.searchParams.get("limit")),
        offset: Number(url.searchParams.get("offset")),
      });
      return;
    }
    if (path === "/api/v1/composer/connections" && method === "POST") {
      const submitted = request.postDataJSON();
      assert.equal(submitted.enabled, false);
      assert.equal(submitted.selected_model, null);
      assert.equal("api_key" in submitted, false);
      const id = isAdmin
        ? "00000000-0000-4000-8000-000000000101"
        : "00000000-0000-4000-8000-000000000102";
      const created = connection(id, {
        allowed_user_ids: submitted.allowed_user_ids,
        endpoint: submitted.endpoint,
        identity_mode: submitted.identity_mode,
        name: submitted.name,
        permitted_models: submitted.permitted_models,
        scope: submitted.scope,
      });
      visibleConnections.push(created);
      await fulfill(route, created, 201, { etag: created.etag });
      return;
    }
    const match = path.match(
      /^\/api\/v1\/composer\/connections\/([0-9a-f-]+)(?:\/(credentials|models|test))?$/,
    );
    assert.ok(match, `Unexpected Composer route ${method} ${path}`);
    const selected = visibleConnections.find((item) => item.id === match[1]);
    assert.ok(
      selected,
      "The caller must only operate on an authorized connection",
    );
    const action = match[2];
    if (action === "credentials" && method === "PUT") {
      assert.equal(request.headers()["if-match"], selected.etag);
      const submitted = request.postDataJSON();
      assert.equal(submitted.api_key, state.secret);
      selected.credential_present = true;
      selected.etag = '"connection-2"';
      await fulfill(route, selected, 200, { etag: selected.etag });
      return;
    }
    if (!action && method === "PATCH") {
      assert.equal(request.headers()["if-match"], selected.etag);
      const submitted = request.postDataJSON();
      Object.assign(selected, submitted);
      selected.etag = `"connection-${state.nextRevision++}"`;
      if (submitted.enabled === true) {
        selected.status = "unconfigured";
        selected.status_message = "Select an approved model.";
      }
      if (submitted.selected_model) {
        selected.status = "ready";
        selected.status_message = null;
      }
      await fulfill(route, selected, 200, { etag: selected.etag });
      return;
    }
    if (action === "models" && method === "GET") {
      assert.equal(selected.enabled, true);
      await fulfill(route, { models: ["approved-model"] });
      return;
    }
    if (action === "test" && method === "POST") {
      assert.equal(selected.enabled, true);
      assert.deepEqual(request.postDataJSON(), {});
      if (state.failNextTest) {
        state.failNextTest = false;
        await fulfill(
          route,
          {
            error: {
              code: "COMPOSER_PROVIDER_UNAVAILABLE",
              message: "The provider is temporarily unavailable.",
            },
          },
          503,
        );
      } else {
        await fulfill(route, { status: "ready", status_message: null });
      }
      return;
    }
    assert.fail(`Unexpected Composer route ${method} ${path}`);
  });
}

test(
  "Composer connection setup preserves grants, safe sequencing, redaction, and outage recovery",
  { timeout: 120_000 },
  async () => {
    const profile = process.env.MARKWEAVE_E2E_PROFILE;
    if (profile !== undefined)
      assert.ok(profile === "standalone" || profile === "distributed");
    const state = {
      adminConnections: [],
      aliceConnections: [],
      failNextTest: true,
      nextRevision: 3,
      permissionEtag: '"permission-1"',
      personalAllowed: false,
      secret: "composer-e2e-write-only-secret",
    };
    const browser = await chromium.launch({
      executablePath:
        process.env.MARKWEAVE_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
      headless: true,
    });
    try {
      const adminContext = await browser.newContext({
        baseURL,
        serviceWorkers: "block",
      });
      const aliceContext = await browser.newContext({
        baseURL,
        serviceWorkers: "block",
      });
      const adminPage = await adminContext.newPage();
      const alicePage = await aliceContext.newPage();
      installRoutes(adminPage, admin, state);
      installRoutes(alicePage, alice, state);

      await alicePage.goto("/composer/connections");
      await alicePage.getByText("Composer status: unauthorized").waitFor();
      await alicePage
        .getByText("No authorized connections are visible to this account.")
        .waitFor();
      assert.equal(
        await alicePage
          .getByRole("heading", { name: "Add a connection" })
          .count(),
        0,
      );

      await adminPage.goto("/composer/connections");
      await adminPage
        .getByRole("heading", { name: "Personal connection permissions" })
        .waitFor();
      await adminPage.getByRole("button", { name: "Grant" }).click();
      await adminPage
        .getByText("Personal connection permission granted.")
        .waitFor();
      await alicePage.reload();
      await alicePage
        .getByRole("heading", { name: "Add a connection" })
        .waitFor();
      assert.deepEqual(await alicePage.getByLabel("Scope").allTextContents(), [
        "Personal",
      ]);

      await adminPage.getByLabel("Scope").selectOption("instance");
      await adminPage.getByLabel("Connection name").fill("Admin endpoint");
      await adminPage.getByLabel("Endpoint URL").fill("https://llm.example/v1");
      await adminPage
        .getByLabel("Permitted models (comma separated)")
        .fill("approved-model");
      await adminPage.getByLabel("API key").fill(state.secret);
      await adminPage
        .getByRole("button", { name: "Create connection" })
        .click();
      await adminPage.getByText(/Connection created disabled\./).waitFor();
      assert.equal(await adminPage.getByLabel("API key").inputValue(), "");
      assert.equal(
        (await adminPage.locator("body").innerText()).includes(state.secret),
        false,
      );
      assert.equal((await adminPage.content()).includes(state.secret), false);

      const card = adminPage.locator("article").filter({
        has: adminPage.getByRole("heading", { name: "Admin endpoint" }),
      });
      assert.equal(
        await card
          .getByRole("button", { name: "Discover models" })
          .isDisabled(),
        true,
      );
      assert.equal(
        await card
          .getByRole("button", { name: "Test connection" })
          .isDisabled(),
        true,
      );
      await card.getByRole("button", { name: "Enable" }).click();
      await card.getByRole("button", { name: "Discover models" }).click();
      await card.getByLabel("Authorized model").selectOption("approved-model");
      await card.getByRole("button", { name: "Save model" }).click();
      await adminPage.getByText("Selected model updated.").waitFor();

      await card.getByRole("button", { name: "Test connection" }).click();
      await adminPage
        .getByText("The provider is temporarily unavailable.")
        .waitFor();
      assert.equal(
        (await adminPage.locator("body").innerText()).includes(state.secret),
        false,
      );
      await card.getByRole("button", { name: "Test connection" }).click();
      await adminPage.getByText("Connection test completed.").waitFor();

      await alicePage.getByLabel("Connection name").fill("Alice endpoint");
      await alicePage.getByLabel("Endpoint URL").fill("https://llm.example/v1");
      await alicePage.getByLabel("API key").fill(state.secret);
      await alicePage
        .getByRole("button", { name: "Create connection" })
        .click();
      await alicePage.getByText(/Connection created disabled\./).waitFor();
      await alicePage
        .getByRole("heading", { name: "Alice endpoint" })
        .waitFor();
      assert.equal(
        (await alicePage.locator("body").innerText()).includes(state.secret),
        false,
      );

      await adminContext.close();
      await aliceContext.close();
    } finally {
      await browser.close();
    }
  },
);
