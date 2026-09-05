import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import http from "node:http";
import test from "node:test";

import { chromium } from "playwright-core";

const baseURL = "http://localhost:3100";
const admissionTimeoutMs = 25_000;

function openDedicatedRequest(admissionId) {
  let admissionError;
  const request = http.request(
    `${baseURL}/hold`,
    { agent: false },
    (response) => {
      response.resume();
      admissionError = new Error(
        `Frontend hold request ${admissionId} returned before saturation`,
      );
    },
  );
  request.once("error", () => {
    admissionError = new Error(
      `Frontend hold request ${admissionId} failed before saturation`,
    );
  });
  request.end();
  return {
    get admissionError() {
      return admissionError;
    },
    request,
  };
}

async function waitForSaturation(requests) {
  const deadline = Date.now() + admissionTimeoutMs;
  while (Date.now() < deadline) {
    const failed = requests.find(
      ({ admissionError }) => admissionError !== undefined,
    );
    if (failed) throw failed.admissionError;
    if (existsSync("/evidence/frontend-saturated")) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error("Timed out waiting for frontend saturation");
}

async function waitFor(path) {
  await assert.doesNotReject(async () => {
    for (let attempt = 0; attempt < 200; attempt += 1) {
      if (existsSync(path)) return;
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    throw new Error(`Timed out waiting for ${path}`);
  });
}

test("frontend outage preserves direct FastAPI authority through the router", async () => {
  if (process.env.MARKWEAVE_E2E_RUNTIME_FAILURE !== "frontend-outage") return;
  const login = await fetch(`${baseURL}/api/v1/login`, {
    body: JSON.stringify({
      username: "e2e-admin",
      password: "e2e-admin-password",
    }),
    headers: {
      "Content-Type": "application/json",
      Origin: baseURL,
    },
    method: "POST",
  });
  assert.equal(login.status, 200);
  const cookies = login.headers
    .getSetCookie()
    .map((value) => value.split(";", 1)[0])
    .join("; ");
  const session = await fetch(`${baseURL}/api/v1/session`, {
    headers: { Cookie: cookies },
  });
  assert.equal(session.status, 200);
  assert.equal((await session.json()).username, "e2e-admin");
  const ready = await fetch(`${baseURL}/health/ready`);
  assert.equal(ready.status, 200);
  assert.equal((await ready.json()).status, "ready");
  const page = await fetch(`${baseURL}/login`);
  assert.equal(page.status, 502);
  assert.equal(await page.text(), "");
});

test("backend outage renders a bounded safe UI without mutation replay", async () => {
  if (process.env.MARKWEAVE_E2E_RUNTIME_FAILURE !== "backend-outage") return;
  const browser = await chromium.launch({
    executablePath:
      process.env.MARKWEAVE_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
    headless: true,
  });
  const context = await browser.newContext({
    baseURL,
    serviceWorkers: "block",
  });
  const page = await context.newPage();
  const apiRequests = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/"))
      apiRequests.push(
        `${request.method()} ${new URL(request.url()).pathname}`,
      );
  });
  try {
    await page.goto("/login", { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "Sign in" }).waitFor();
    await page
      .getByRole("alert")
      .filter({ hasText: "Markweave is unavailable. Try again shortly." })
      .waitFor();
    const secondSession = page.waitForResponse((response) =>
      response.url().endsWith("/api/v1/session"),
    );
    await page.getByRole("button", { name: "Try again" }).click();
    assert.equal((await secondSession).status(), 502);
    assert.deepEqual(apiRequests, [
      "GET /api/v1/session",
      "GET /api/v1/session",
    ]);
  } finally {
    await context.close();
    await browser.close();
  }
});

test("production route exposes exact saturation and draining failures", async () => {
  if (process.env.MARKWEAVE_E2E_RUNTIME_FAILURE !== "admission") return;
  // Each request owns a socket. Only the fixture's admission owner writes the
  // saturation evidence after accepting all 128, so the test cannot confuse a
  // router TCP connection or a proxy-generated response with admission.
  const held = Array.from({ length: 128 }, (_, admissionId) =>
    openDedicatedRequest(admissionId),
  );
  try {
    await waitForSaturation(held);
    assert.equal(existsSync("/evidence/frontend-saturated"), true);
    const saturated = await fetch(`${baseURL}/overflow`);
    assert.equal(saturated.status, 503);
    assert.equal(await saturated.text(), "");
    assert.equal(saturated.headers.get("content-security-policy"), null);
    await writeFile("/evidence/frontend-request-drain", "true\n", {
      mode: 0o600,
    });
    await waitFor("/evidence/frontend-draining");
    const draining = await fetch(`${baseURL}/draining`);
    assert.equal(draining.status, 503);
    assert.equal(await draining.text(), "");
    assert.equal(draining.headers.get("content-security-policy"), null);
  } finally {
    held.forEach(({ request }) => request.destroy());
    await writeFile("/evidence/frontend-release", "true\n", {
      mode: 0o600,
    }).catch(() => undefined);
  }
});
