import assert from "node:assert/strict";
import test from "node:test";
import { chromium } from "playwright-core";

const baseURL = "http://localhost:3100";

async function login(page, username, password, expected = "/convert") {
  const sessionResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/session"),
  );
  await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
  assert.equal((await sessionResponse).status(), 401);
  const retry = page.getByRole("button", { name: "Try again" });
  if (await retry.isVisible()) await retry.click();
  await page.getByRole("textbox", { name: "Username" }).fill(username);
  await page.getByLabel("Password").fill(password);
  await Promise.all([
    page.waitForURL(`**${expected}`),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);
}

async function api(page, method, path, body) {
  return page.evaluate(
    async ({ method, path, body }) => {
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
          "Content-Type": "application/json",
          ...(csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {}),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      return {
        status: response.status,
        body: await response.json().catch(() => null),
      };
    },
    { method, path, body },
  );
}

async function assertDisabledNavigationItem(page, name) {
  const item = page.getByText(name, { exact: true });
  await assert.doesNotReject(() => item.waitFor());
  assert.equal(await item.getAttribute("aria-disabled"), "true");
  assert.equal(await page.getByRole("link", { name, exact: true }).count(), 0);
}

test("Next authentication shell uses FastAPI authority for three identities", async () => {
  const profile = process.env.MARKWEAVE_E2E_PROFILE;
  assert.ok(profile === "standalone" || profile === "distributed");
  const suffix = `${profile}-${Date.now()}`;
  const alice = {
    username: `next-alice-${suffix}`,
    password: `Alice-${suffix}-password`,
  };
  const bob = {
    username: `next-bob-${suffix}`,
    password: `Bob-${suffix}-password`,
  };
  const renewed = `Renewed-${suffix}-password`;
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
    const bobContext = await browser.newContext({
      baseURL,
      serviceWorkers: "block",
    });
    const adminPage = await adminContext.newPage();
    const alicePage = await aliceContext.newPage();
    const bobPage = await bobContext.newPage();

    await login(adminPage, "e2e-admin", "e2e-admin-password");
    await assertDisabledNavigationItem(adminPage, "Users");
    const adminSession = await api(adminPage, "GET", "/api/v1/session");
    assert.equal(adminSession.status, 200);
    assert.ok(Number.isInteger(adminSession.body.effective_idle_minutes));
    await assert.doesNotReject(() =>
      adminPage
        .getByText(
          new RegExp(
            `${adminSession.body.effective_idle_minutes} minutes of inactivity`,
          ),
        )
        .waitFor(),
    );

    for (const [identity, passwordChangeRequired] of [
      [alice, false],
      [bob, true],
    ]) {
      const created = await api(adminPage, "POST", "/api/v1/admin/users", {
        username: identity.username,
        password: identity.password,
        password_change_required: passwordChangeRequired,
      });
      assert.equal(created.status, 201);
    }

    await login(alicePage, alice.username, alice.password);
    const aliceSession = await api(alicePage, "GET", "/api/v1/session");
    assert.equal(aliceSession.status, 200);
    await assert.doesNotReject(() =>
      alicePage
        .getByText(
          new RegExp(
            `${aliceSession.body.effective_idle_minutes} minutes of inactivity`,
          ),
        )
        .waitFor(),
    );
    assert.equal(
      await alicePage.getByText("Users", { exact: true }).count(),
      0,
    );

    await login(bobPage, bob.username, bob.password, "/change-password");
    assert.equal(
      (await api(bobPage, "GET", "/api/v1/conversions")).status,
      403,
    );
    await bobPage.getByLabel("New password", { exact: true }).fill(renewed);
    await bobPage.getByLabel("Confirm new password").fill(renewed);
    await Promise.all([
      bobPage.waitForURL("**/login"),
      bobPage.getByRole("button", { name: "Change password" }).click(),
    ]);
    await login(bobPage, bob.username, renewed);

    const users = await api(adminPage, "GET", "/api/v1/admin/users");
    const aliceRecord = users.body.find(
      (candidate) => candidate.username === alice.username,
    );
    assert.ok(aliceRecord);
    assert.equal(
      (
        await api(
          adminPage,
          "PATCH",
          `/api/v1/admin/users/${aliceRecord.id}/active`,
          { active: false },
        )
      ).status,
      200,
    );
    await Promise.all([
      alicePage.waitForURL("**/login"),
      alicePage.getByRole("button", { name: "Sign out" }).click(),
    ]);
    await assert.doesNotReject(() =>
      alicePage
        .getByText("Your session ended. Please sign in again.")
        .waitFor(),
    );

    for (const context of [adminContext, aliceContext, bobContext])
      await context.close();
  } finally {
    await browser.close();
  }
});
