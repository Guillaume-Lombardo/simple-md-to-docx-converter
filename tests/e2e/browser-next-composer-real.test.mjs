import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import https from "node:https";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";

import { chromium } from "playwright-core";

const baseURL = process.env.MARKWEAVE_E2E_BASE_URL || "http://localhost:3100";
const secret = "composer-e2e-write-only-secret";
const endpoint = "https://e2e-llm:8443/v1";
const mutualTlsEndpoint = "https://e2e-llm:8444/v1";
const model = "composer-e2e-model";
const slowModel = "composer-e2e-slow-model";
const infectedSource =
  "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*";

async function login(page, username, password) {
  await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
  await page.getByRole("textbox", { name: "Username" }).fill(username);
  await page.getByLabel("Password").fill(password);
  await Promise.all([
    page.waitForURL("**/convert"),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);
}

async function api(page, method, path, options = {}) {
  const result = await page.evaluate(
    async ({ method, path, options }) => {
      const csrf = document.cookie
        .split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith("__Host-md_converter_csrf="))
        ?.split("=", 2)[1];
      const headers = { ...options.headers };
      let body;
      if (options.upload) {
        const form = new FormData();
        form.set(
          "source",
          new File([options.upload.content], options.upload.filename, {
            type: "text/markdown",
          }),
        );
        form.set("title", options.upload.title);
        body = form;
      } else if (options.json !== undefined) {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(options.json);
      }
      if (method !== "GET" && csrf)
        headers["X-CSRF-Token"] = decodeURIComponent(csrf);
      const response = await fetch(path, {
        method,
        headers,
        body,
        cache: "no-store",
        credentials: "same-origin",
      });
      const text = await response.text();
      let json;
      try {
        json = JSON.parse(text);
      } catch {
        json = null;
      }
      return {
        status: response.status,
        headers: Object.fromEntries(response.headers.entries()),
        text,
        json,
      };
    },
    { method, path, options },
  );
  assert.equal(
    result.text.includes(secret),
    false,
    "A response exposed the key",
  );
  return result;
}

function exactStatus(result, status) {
  assert.equal(result.status, status, result.text);
  return result.json;
}

async function createUser(adminPage, name) {
  const password = `Composer-${name}-password`;
  const user = exactStatus(
    await api(adminPage, "POST", "/api/v1/admin/users", {
      json: {
        username: name,
        password,
        password_change_required: false,
      },
    }),
    201,
  );
  return { ...user, password };
}

async function createConnection(
  page,
  name,
  scope,
  ca,
  { allowedUserIds = [], clientCertificate, clientKey } = {},
) {
  await page.goto(`${baseURL}/composer/connections`, {
    waitUntil: "networkidle",
  });
  const createForm = page.locator("form").filter({
    has: page.getByRole("heading", { name: "Add a connection" }),
  });
  await createForm.waitFor();
  assert.equal(await createForm.count(), 1);
  await createForm.getByLabel("Scope").selectOption(scope);
  await createForm.getByLabel("Connection name").fill(name);
  await createForm
    .getByLabel("Endpoint URL")
    .fill(clientCertificate ? mutualTlsEndpoint : endpoint);
  await createForm.getByLabel("Permitted models (comma separated)").fill(model);
  if (scope === "instance")
    await createForm
      .getByLabel("Allowed user IDs (comma separated)")
      .fill(allowedUserIds.join(", "));
  if (clientCertificate) {
    await createForm
      .getByLabel("Client certificate (PEM)")
      .fill(clientCertificate);
    await createForm.getByLabel("Client private key (PEM)").fill(clientKey);
  } else {
    await createForm.getByLabel("API key").fill(secret);
  }
  await createForm.getByLabel("Internal CA bundle (PEM)").fill(ca);
  const [createdResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/composer/connections") &&
        response.request().method() === "POST" &&
        response.status() === 201,
    ),
    createForm.getByRole("button", { name: "Create connection" }).click(),
  ]);
  assert.deepEqual(
    createdResponse.request().postDataJSON().allowed_user_ids,
    allowedUserIds,
  );
  assert.deepEqual(
    (await createdResponse.json()).allowed_user_ids,
    allowedUserIds,
  );
  await page.getByText(/Connection created disabled\./).waitFor();
  assert.equal(await createForm.getByLabel("API key").inputValue(), "");
  assert.equal(
    await createForm.getByLabel("Client certificate (PEM)").inputValue(),
    "",
  );
  assert.equal(
    await createForm.getByLabel("Client private key (PEM)").inputValue(),
    "",
  );
  assert.equal(
    await createForm.getByLabel("Internal CA bundle (PEM)").inputValue(),
    "",
  );
  assert.equal((await page.content()).includes(secret), false);
  if (clientKey)
    assert.equal((await page.content()).includes(clientKey), false);
  const cards = page.locator("article").filter({
    has: page.getByRole("heading", { name }),
  });
  assert.equal(await cards.count(), 1);
  const card = cards.first();
  assert.equal(
    await card.getByRole("button", { name: "Discover models" }).isDisabled(),
    true,
  );
  assert.equal(
    await card.getByRole("button", { name: "Test connection" }).isDisabled(),
    true,
  );
  await card.getByRole("button", { name: "Enable" }).click();
  await page.getByText("Connection enabled.").waitFor();
  await card.getByRole("button", { name: "Discover models" }).click();
  await card.getByLabel("Authorized model").selectOption(model);
  await card.getByRole("button", { name: "Save model" }).click();
  await page.getByText("Selected model updated.").waitFor();
  const list = exactStatus(
    await api(page, "GET", "/api/v1/composer/connections"),
    200,
  );
  const connection = list.connections.find((item) => item.name === name);
  assert.ok(connection);
  assert.equal(connection.credential_present, !clientCertificate);
  assert.equal(
    connection.client_certificate_present,
    Boolean(clientCertificate),
  );
  assert.equal(connection.internal_ca_present, true);
  assert.equal(connection.selected_model, model);
  if (scope === "instance")
    assert.deepEqual(connection.allowed_user_ids, allowedUserIds);
  assert.equal(JSON.stringify(connection).includes(secret), false);
  assert.equal(JSON.stringify(connection).includes(ca), false);
  if (clientKey)
    assert.equal(JSON.stringify(connection).includes(clientKey), false);
  return { card, connection };
}

async function testConnection(page, card, connectionId, status) {
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response
          .url()
          .endsWith(`/api/v1/composer/connections/${connectionId}/test`) &&
        response.status() === status,
    ),
    card.getByRole("button", { name: "Test connection" }).click(),
  ]);
  const state = exactStatus(
    await api(page, "GET", "/api/v1/composer/capabilities"),
    200,
  );
  assert.equal(state.status, status === 200 ? "ready" : "outage");
}

async function providerSlowCallStarted(ca) {
  const deadline = Date.now() + 2_000;
  while (Date.now() < deadline) {
    const started = await new Promise((resolve, reject) => {
      const request = https.get(
        {
          hostname: "e2e-llm",
          port: 8443,
          path: "/e2e/status",
          ca,
          timeout: 1_000,
        },
        (response) => {
          let body = "";
          response.setEncoding("utf8");
          response.on("data", (chunk) => {
            body += chunk;
          });
          response.on("end", () => {
            if (response.statusCode !== 200)
              reject(new Error("Provider control endpoint failed"));
            else resolve(JSON.parse(body).slow_started === true);
          });
        },
      );
      request.on("timeout", () =>
        request.destroy(new Error("Provider control endpoint timed out")),
      );
      request.on("error", reject);
    });
    if (started) return;
    await delay(20);
  }
  assert.fail("The provider did not enter the deliberate in-flight call");
}

test("Composer uses real final-image routing, TLS egress and durable owner revisions", async () => {
  const profile = process.env.MARKWEAVE_E2E_PROFILE;
  assert.ok(profile === "standalone" || profile === "distributed");
  const statePath = process.env.MARKWEAVE_E2E_COMPOSER_STATE;
  assert.ok(statePath?.startsWith("/browser-session/composer-"));
  const ca = await readFile("/run/composer-e2e-ca.crt", "utf8");
  const clientCertificate = await readFile(
    "/run/composer-e2e-client.crt",
    "utf8",
  );
  const clientKey = await readFile("/run/composer-e2e-client.key", "utf8");
  const suffix = `${profile}-${Date.now()}`;
  const source = `# Composer ${suffix}\n\nVerified source.\n`;
  const browser = await chromium.launch({
    executablePath:
      process.env.MARKWEAVE_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
    headless: true,
  });
  const contexts = await Promise.all(
    [0, 1, 2].map(() =>
      browser.newContext({ baseURL, serviceWorkers: "block" }),
    ),
  );
  const [adminPage, alicePage, bobPage] = await Promise.all(
    contexts.map((context) => context.newPage()),
  );
  try {
    await login(adminPage, "e2e-admin", "e2e-admin-password");
    const adminIdentity = exactStatus(
      await api(adminPage, "GET", "/api/v1/session"),
      200,
    );
    const alice = await createUser(adminPage, `composer-alice-${suffix}`);
    const bob = await createUser(adminPage, `composer-bob-${suffix}`);
    await login(alicePage, alice.username, alice.password);
    await login(bobPage, bob.username, bob.password);

    await alicePage.goto(`${baseURL}/composer/connections`);
    await alicePage.getByText("Composer status: unconfigured").waitFor();
    assert.equal(
      await alicePage
        .getByRole("heading", { name: "Add a connection" })
        .count(),
      0,
    );
    const denied = exactStatus(
      await api(
        adminPage,
        "GET",
        `/api/v1/composer/personal-permissions/${alice.id}`,
      ),
      200,
    );
    assert.equal(denied.allowed, false);

    const adminConnection = await createConnection(
      adminPage,
      `Composer admin ${suffix}`,
      "instance",
      ca,
      { allowedUserIds: [adminIdentity.id] },
    );
    await alicePage.reload({ waitUntil: "networkidle" });
    await alicePage.getByText("Composer status: unauthorized").waitFor();
    assert.equal(
      (
        await api(
          alicePage,
          "GET",
          `/api/v1/composer/connections/${adminConnection.connection.id}`,
        )
      ).status,
      404,
    );
    assert.equal(
      (
        await api(
          bobPage,
          "GET",
          `/api/v1/composer/connections/${adminConnection.connection.id}`,
        )
      ).status,
      404,
    );
    await testConnection(
      adminPage,
      adminConnection.card,
      adminConnection.connection.id,
      503,
    );
    await testConnection(
      adminPage,
      adminConnection.card,
      adminConnection.connection.id,
      200,
    );

    const beforeInfected = exactStatus(
      await api(adminPage, "GET", "/api/v1/composer/drafts"),
      200,
    ).drafts.length;
    const infected = await api(adminPage, "POST", "/api/v1/composer/drafts", {
      upload: {
        content: infectedSource,
        filename: "infected.md",
        title: "Infected",
      },
    });
    assert.equal(infected.status, 422);
    assert.equal(infected.json.error.code, "UPLOAD_MALWARE_DETECTED");
    assert.equal(
      exactStatus(await api(adminPage, "GET", "/api/v1/composer/drafts"), 200)
        .drafts.length,
      beforeInfected,
    );
    assert.equal(
      exactStatus(
        await api(bobPage, "GET", "/api/v1/composer/connections"),
        200,
      ).connections.length,
      0,
    );
    assert.ok(
      [403, 404].includes(
        (
          await api(
            bobPage,
            "GET",
            `/api/v1/composer/connections/${adminConnection.connection.id}`,
          )
        ).status,
      ),
    );

    const draft = exactStatus(
      await api(adminPage, "POST", "/api/v1/composer/drafts", {
        upload: {
          content: source,
          filename: "composer-source.md",
          title: `Composer draft ${suffix}`,
        },
      }),
      201,
    );
    const draftPath = `/api/v1/composer/drafts/${draft.id}`;
    const first = exactStatus(
      await api(adminPage, "POST", `${draftPath}/revisions/from-source`, {
        headers: {
          "If-Match": draft.etag,
          "Idempotency-Key": `source-${suffix}`,
        },
      }),
      201,
    );
    assert.equal(first.number, 1);
    assert.equal(first.artifacts.length, 2);
    const downloadPath = `${draftPath}/revisions/${first.id}/artifacts/download`;
    const download = await api(adminPage, "GET", downloadPath);
    assert.equal(download.status, 200);
    assert.equal(download.text, source);
    assert.equal(download.headers["cache-control"], "private, no-store");
    assert.equal(download.headers["x-content-type-options"], "nosniff");
    assert.match(download.headers["content-disposition"], /^attachment;/);
    assert.equal((await api(alicePage, "GET", draftPath)).status, 404);
    assert.equal((await api(bobPage, "GET", downloadPath)).status, 404);

    const current = exactStatus(await api(adminPage, "GET", draftPath), 200);
    const changed = exactStatus(
      await api(adminPage, "PUT", draftPath, {
        headers: { "If-Match": current.etag },
        json: { title: current.title, content: "Human correction" },
      }),
      200,
    );
    const restored = exactStatus(
      await api(
        adminPage,
        "POST",
        `${draftPath}/revisions/${first.id}/restore`,
        {
          headers: {
            "If-Match": changed.etag,
            "Idempotency-Key": `restore-${suffix}`,
          },
        },
      ),
      201,
    );
    assert.equal(restored.restored_from_revision_id, first.id);
    assert.equal(restored.number, 2);
    assert.equal(
      (
        await api(
          adminPage,
          "GET",
          `${draftPath}/revisions/${restored.id}/artifacts/download`,
        )
      ).text,
      source,
    );

    await testConnection(
      adminPage,
      adminConnection.card,
      adminConnection.connection.id,
      503,
    );
    assert.equal((await api(adminPage, "GET", draftPath)).status, 200);
    assert.equal((await api(adminPage, "GET", downloadPath)).text, source);
    assert.equal(
      (
        await api(adminPage, "POST", "/api/v1/composer/drafts", {
          upload: {
            content: source,
            filename: "during-outage.md",
            title: "Unavailable",
          },
        })
      ).status,
      503,
    );

    const grant = exactStatus(
      await api(
        adminPage,
        "PUT",
        `/api/v1/composer/personal-permissions/${alice.id}`,
        {
          headers: { "If-Match": denied.etag },
          json: { allowed: true },
        },
      ),
      200,
    );
    const personal = await createConnection(
      alicePage,
      `Composer Alice ${suffix}`,
      "personal",
      ca,
      { clientCertificate, clientKey },
    );
    await testConnection(alicePage, personal.card, personal.connection.id, 200);

    const instance = exactStatus(
      await api(adminPage, "POST", "/api/v1/composer/connections", {
        json: {
          scope: "instance",
          identity_mode: "individual",
          name: `Individual instance ${suffix}`,
          endpoint,
          permitted_models: [model, slowModel],
          allowed_user_ids: [alice.id, bob.id],
          selected_model: null,
          enabled: false,
        },
      }),
      201,
    );
    const instancePath = `/api/v1/composer/connections/${instance.id}`;
    const enabledInstance = exactStatus(
      await api(adminPage, "PATCH", instancePath, {
        headers: { "If-Match": instance.etag },
        json: { selected_model: model, enabled: true },
      }),
      200,
    );
    assert.equal(
      exactStatus(await api(alicePage, "GET", instancePath), 200)
        .credential_present,
      false,
    );
    assert.equal(
      exactStatus(await api(bobPage, "GET", instancePath), 200)
        .credential_present,
      false,
    );
    const aliceCredential = exactStatus(
      await api(alicePage, "PUT", `${instancePath}/credentials`, {
        headers: { "If-Match": enabledInstance.etag },
        json: { api_key: secret, internal_ca: ca },
      }),
      200,
    );
    assert.equal(aliceCredential.credential_present, true);
    assert.equal(
      exactStatus(await api(bobPage, "GET", instancePath), 200)
        .credential_present,
      false,
    );
    exactStatus(
      await api(alicePage, "POST", `${instancePath}/test`, { json: {} }),
      200,
    );
    assert.equal(
      (await api(bobPage, "POST", `${instancePath}/test`, { json: {} })).status,
      422,
    );
    const bobCredential = exactStatus(
      await api(bobPage, "PUT", `${instancePath}/credentials`, {
        headers: { "If-Match": aliceCredential.etag },
        json: { api_key: "individual-invalid-key", internal_ca: ca },
      }),
      200,
    );
    assert.equal(bobCredential.credential_present, true);
    assert.equal(
      (await api(bobPage, "POST", `${instancePath}/test`, { json: {} })).status,
      503,
    );
    exactStatus(
      await api(alicePage, "POST", `${instancePath}/test`, { json: {} }),
      200,
    );
    const removedBob = exactStatus(
      await api(adminPage, "PATCH", instancePath, {
        headers: { "If-Match": bobCredential.etag },
        json: { allowed_user_ids: [alice.id] },
      }),
      200,
    );
    assert.deepEqual(removedBob.allowed_user_ids, [alice.id]);
    assert.equal((await api(bobPage, "GET", instancePath)).status, 404);
    assert.equal(
      (await api(bobPage, "POST", `${instancePath}/test`, { json: {} })).status,
      403,
    );
    const staleCall = api(alicePage, "POST", `${instancePath}/test`, {
      json: { model: slowModel },
    });
    await providerSlowCallStarted(ca);
    exactStatus(
      await api(adminPage, "PATCH", instancePath, {
        headers: { "If-Match": removedBob.etag },
        json: { allowed_user_ids: [] },
      }),
      200,
    );
    assert.equal((await staleCall).status, 412);
    assert.equal(
      (await api(alicePage, "POST", `${instancePath}/test`, { json: {} }))
        .status,
      403,
    );

    const adminPath = `/api/v1/composer/connections/${adminConnection.connection.id}`;
    const adminRecord = exactStatus(
      await api(adminPage, "GET", adminPath),
      200,
    );
    const malformed = await api(adminPage, "PUT", `${adminPath}/credentials`, {
      headers: { "If-Match": adminRecord.etag },
      json: { api_key: { nested: secret } },
    });
    assert.equal(malformed.status, 422);
    assert.equal(malformed.text.includes(secret), false);
    const competingWrites = await Promise.all([
      api(adminPage, "PUT", `${adminPath}/credentials`, {
        headers: { "If-Match": adminRecord.etag },
        json: { api_key: "rotated-invalid-one" },
      }),
      api(adminPage, "PUT", `${adminPath}/credentials`, {
        headers: { "If-Match": adminRecord.etag },
        json: { api_key: "rotated-invalid-two" },
      }),
    ]);
    assert.deepEqual(
      competingWrites.map((item) => item.status).sort(),
      [200, 412],
    );
    const afterRace = exactStatus(await api(adminPage, "GET", adminPath), 200);
    assert.equal(
      (await api(adminPage, "POST", `${adminPath}/test`, { json: {} })).status,
      503,
    );
    const rotated = exactStatus(
      await api(adminPage, "PUT", `${adminPath}/credentials`, {
        headers: { "If-Match": afterRace.etag },
        json: { api_key: secret },
      }),
      200,
    );
    exactStatus(
      await api(adminPage, "POST", `${adminPath}/test`, { json: {} }),
      200,
    );
    const revoked = exactStatus(
      await api(adminPage, "PUT", `${adminPath}/credentials`, {
        headers: { "If-Match": rotated.etag },
        json: { revoke: true },
      }),
      200,
    );
    assert.equal(revoked.credential_present, false);
    assert.equal(
      (await api(adminPage, "POST", `${adminPath}/test`, { json: {} })).status,
      422,
    );
    assert.equal((await api(adminPage, "GET", downloadPath)).text, source);

    exactStatus(
      await api(
        adminPage,
        "PUT",
        `/api/v1/composer/personal-permissions/${alice.id}`,
        {
          headers: { "If-Match": grant.etag },
          json: { allowed: false },
        },
      ),
      200,
    );
    assert.equal(
      (
        await api(
          alicePage,
          "POST",
          `/api/v1/composer/connections/${personal.connection.id}/test`,
          {
            json: {},
          },
        )
      ).status,
      403,
    );
    assert.equal(
      exactStatus(
        await api(alicePage, "GET", "/api/v1/composer/capabilities"),
        200,
      ).status,
      "unauthorized",
    );
    assert.equal(
      (
        await api(
          bobPage,
          "GET",
          `/api/v1/composer/connections/${personal.connection.id}`,
        )
      ).status,
      404,
    );
    assert.equal(
      (
        await api(
          adminPage,
          "GET",
          `/api/v1/composer/connections/${personal.connection.id}`,
        )
      ).status,
      404,
    );
    assert.equal((await adminPage.content()).includes(secret), false);
    assert.equal((await alicePage.content()).includes(secret), false);
    assert.equal((await alicePage.content()).includes(clientKey), false);
    const backupConnection = exactStatus(
      await api(adminPage, "POST", "/api/v1/composer/connections", {
        json: {
          scope: "instance",
          identity_mode: "shared",
          name: `Backup continuity ${suffix}`,
          endpoint,
          permitted_models: [model, slowModel],
          allowed_user_ids: [adminIdentity.id],
          selected_model: null,
          enabled: false,
        },
      }),
      201,
    );
    const backupPath = `/api/v1/composer/connections/${backupConnection.id}`;
    const backupCredential = exactStatus(
      await api(adminPage, "PUT", `${backupPath}/credentials`, {
        headers: { "If-Match": backupConnection.etag },
        json: { api_key: secret, internal_ca: ca },
      }),
      200,
    );
    const readyBackup = exactStatus(
      await api(adminPage, "PATCH", backupPath, {
        headers: { "If-Match": backupCredential.etag },
        json: { enabled: true, selected_model: model },
      }),
      200,
    );
    assert.equal(readyBackup.credential_present, true);
    exactStatus(
      await api(adminPage, "POST", `${backupPath}/test`, { json: {} }),
      200,
    );
    await writeFile(
      statePath,
      `${JSON.stringify({ draftId: draft.id, revisionId: first.id, restoredId: restored.id, connectionId: adminConnection.connection.id, backupConnectionId: backupConnection.id, source })}\n`,
      { mode: 0o600 },
    );
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
    await browser.close();
  }
});
