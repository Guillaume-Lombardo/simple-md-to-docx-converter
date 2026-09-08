import assert from "node:assert/strict";
import test from "node:test";
import { chromium } from "playwright-core";

const baseURL = "http://localhost:3100";
const terminalStates = new Set(["cancelled", "expired", "failed", "succeeded"]);

async function login(page, username, password) {
  await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
  await page.getByRole("textbox", { name: "Username" }).fill(username);
  await page.getByLabel("Password").fill(password);
  await Promise.all([
    page.waitForURL("**/convert"),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);
  await page.getByRole("heading", { name: "New conversion" }).waitFor();
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
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
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

async function submitApi(page, key, name) {
  return page.evaluate(
    async ({ key, name }) => {
      const csrf = document.cookie
        .split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith("__Host-md_converter_csrf="))
        ?.split("=", 2)[1];
      const form = new FormData();
      form.set(
        "source",
        new File(["# Held admission\n"], name, { type: "text/markdown" }),
      );
      form.set("output", "docx");
      const response = await fetch("/api/v1/conversions", {
        body: form,
        cache: "no-store",
        credentials: "same-origin",
        headers: {
          "Idempotency-Key": key,
          ...(csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {}),
        },
        method: "POST",
      });
      return {
        status: response.status,
        body: await response.json().catch(() => null),
      };
    },
    { key, name },
  );
}

async function prepareSubmission(page, name) {
  await page.goto(`${baseURL}/convert`, { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: "New conversion" }).waitFor();
  await page.getByLabel(/Source file/).setInputFiles({
    name,
    mimeType: "text/markdown",
    buffer: Buffer.from("# Held admission through Next.js\n"),
  });
  await page.getByRole("radio", { name: "DOCX", exact: true }).click();
}

async function submitUi(page) {
  const response = page.waitForResponse(
    (candidate) =>
      candidate.url().endsWith("/api/v1/conversions") &&
      candidate.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Start conversion" }).click();
  const accepted = await response;
  const key = accepted.request().headers()["idempotency-key"];
  assert.equal(typeof key, "string");
  return { response: accepted, key };
}

async function cancelSafely(adminPage, jobId) {
  const cancelled = await api(
    adminPage,
    "DELETE",
    `/api/v1/conversions/${jobId}`,
  );
  assert.ok([200, 409].includes(cancelled.status));
  const observed = await api(adminPage, "GET", `/api/v1/conversions/${jobId}`);
  assert.equal(observed.status, 200);
  assert.ok(terminalStates.has(observed.body.state));
}

test(
  "Next conversion UI enforces deterministic owner quota and global capacity",
  { timeout: 180_000 },
  async () => {
    const profile = process.env.MARKWEAVE_E2E_PROFILE;
    assert.ok(profile === "standalone" || profile === "distributed");
    const suffix = `${profile}-${Date.now()}`;
    const alice = {
      username: `admission-alice-${suffix}`,
      password: `Alice-${suffix}-password`,
    };
    const bob = {
      username: `admission-bob-${suffix}`,
      password: `Bob-${suffix}-password`,
    };
    const browser = await chromium.launch({
      executablePath:
        process.env.MARKWEAVE_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
      headless: true,
    });
    const acceptedJobIds = [];
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
      const aliceRacePage = await aliceContext.newPage();
      const bobPage = await bobContext.newPage();
      let alicePosts = 0;
      let adminPosts = 0;
      for (const page of [alicePage, aliceRacePage]) {
        page.on("request", (request) => {
          if (
            request.method() === "POST" &&
            request.url().endsWith("/api/v1/conversions")
          )
            alicePosts += 1;
        });
      }
      adminPage.on("request", (request) => {
        if (
          request.method() === "POST" &&
          request.url().endsWith("/api/v1/conversions")
        )
          adminPosts += 1;
      });
      await login(adminPage, "e2e-admin", "e2e-admin-password");
      for (const identity of [alice, bob]) {
        const created = await api(adminPage, "POST", "/api/v1/admin/users", {
          username: identity.username,
          password: identity.password,
          password_change_required: false,
        });
        assert.equal(created.status, 201);
      }
      await Promise.all([
        login(alicePage, alice.username, alice.password),
        login(bobPage, bob.username, bob.password),
      ]);

      await prepareSubmission(alicePage, "alice-first.md");
      const aliceFirst = await submitUi(alicePage);
      assert.equal(aliceFirst.response.status(), 202);
      const aliceFirstJob = await aliceFirst.response.json();
      assert.equal(aliceFirstJob.state, "queued");
      acceptedJobIds.push(aliceFirstJob.id);

      await Promise.all([
        prepareSubmission(alicePage, "alice-race-one.md"),
        prepareSubmission(aliceRacePage, "alice-race-two.md"),
      ]);
      const raced = await Promise.all([
        submitUi(alicePage),
        submitUi(aliceRacePage),
      ]);
      assert.deepEqual(
        raced.map(({ response }) => response.status()).sort(),
        [202, 429],
      );
      const admitted = raced.find(({ response }) => response.status() === 202);
      const rejected = raced.find(({ response }) => response.status() === 429);
      assert.ok(admitted);
      assert.ok(rejected);
      const admittedJob = await admitted.response.json();
      assert.equal(admittedJob.state, "queued");
      acceptedJobIds.push(admittedJob.id);
      const rejectedPage =
        raced[0].response.status() === 429 ? alicePage : aliceRacePage;
      await rejectedPage
        .getByRole("alert")
        .getByText("The active conversion quota is exhausted.")
        .waitFor();
      assert.equal(alicePosts, 3);

      const admittedPage =
        raced[0].response.status() === 202 ? alicePage : aliceRacePage;
      await admittedPage
        .getByRole("button", { name: "Cancel conversion" })
        .click();
      await admittedPage.getByText("The conversion was cancelled.").waitFor();
      const retriedQuota = await submitUi(rejectedPage);
      assert.equal(retriedQuota.response.status(), 202);
      assert.notEqual(retriedQuota.key, rejected.key);
      assert.equal(alicePosts, 4);
      const retriedQuotaJob = await retriedQuota.response.json();
      assert.equal(retriedQuotaJob.state, "queued");
      acceptedJobIds.push(retriedQuotaJob.id);

      const bobJob = await submitApi(
        bobPage,
        `bob-${suffix}`,
        "bob-capacity.md",
      );
      assert.equal(bobJob.status, 202);
      assert.equal(bobJob.body.state, "queued");
      acceptedJobIds.push(bobJob.body.id);

      await prepareSubmission(adminPage, "global-capacity.md");
      const capacityRejected = await submitUi(adminPage);
      assert.equal(capacityRejected.response.status(), 503);
      await adminPage
        .getByRole("alert")
        .getByText("The conversion queue is at capacity.")
        .waitFor();
      assert.equal(adminPosts, 1);

      const released = await api(
        bobPage,
        "DELETE",
        `/api/v1/conversions/${bobJob.body.id}`,
      );
      assert.equal(released.status, 200);
      assert.equal(released.body.state, "cancelled");
      const capacityRetried = await submitUi(adminPage);
      assert.equal(capacityRetried.response.status(), 202);
      assert.notEqual(capacityRetried.key, capacityRejected.key);
      assert.equal(adminPosts, 2);
      const capacityRetriedJob = await capacityRetried.response.json();
      assert.equal(capacityRetriedJob.state, "queued");
      acceptedJobIds.push(capacityRetriedJob.id);

      for (const jobId of acceptedJobIds) {
        await cancelSafely(adminPage, jobId);
      }
      await Promise.all([
        adminContext.close(),
        aliceContext.close(),
        bobContext.close(),
      ]);
    } finally {
      await browser.close();
    }
  },
);
