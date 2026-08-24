import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const puppeteerModule = process.env.MD_CONVERTER_TEST_PUPPETEER || path.join(
  repository, "spikes/toolchain/node_modules/puppeteer-core/lib/puppeteer/puppeteer-core.js",
);

function availablePort() {
  return new Promise((resolve, reject) => {
    const listener = net.createServer();
    listener.once("error", reject);
    listener.listen(0, "127.0.0.1", () => {
      const address = listener.address();
      listener.close(() => resolve(address.port));
    });
  });
}

function waitForHttp(url) {
  const deadline = Date.now() + 10_000;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const request = http.get(url, (response) => { response.resume(); resolve(); });
      request.once("error", (error) => Date.now() >= deadline ? reject(error) : setTimeout(attempt, 50));
    };
    attempt();
  });
}

async function waitForText(page, selector, expected) {
  await page.waitForFunction(
    (selected, text) => document.querySelector(selected)?.textContent.includes(text),
    { timeout: 15_000 }, selector, expected,
  );
}

async function login(page, baseUrl, username, password) {
  await page.goto(`${baseUrl}/login`, { waitUntil: "networkidle0" });
  await page.type('input[name="username"]', username);
  await page.type('input[name="password"]', password);
  await page.setExtraHTTPHeaders({ Origin: baseUrl });
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle0" }),
    page.click('button[type="submit"]'),
  ]);
  await page.setExtraHTTPHeaders({});
  assert.equal(page.url(), `${baseUrl}/convert`);
}

async function clearSession(page) {
  const client = await page.createCDPSession();
  await client.send("Network.clearBrowserCookies");
}

test("owner and administrator workflows work in pinned Chromium", { timeout: 90_000 }, async (context) => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "md-converter-admin-browser-"));
  const port = await availablePort();
  const baseUrl = `http://localhost:${port}`;
  const python = process.env.MD_CONVERTER_TEST_PYTHON || path.join(repository, ".venv/bin/python");
  const docxResult = spawnSync(python, ["-c", "import base64; from tests.unit.test_template_validation import _docx; print(base64.b64encode(_docx()).decode())"], { cwd: repository, encoding: "utf8" });
  assert.equal(docxResult.status, 0, docxResult.stderr);
  const templateFile = path.join(temporary, "template.docx");
  const invalidTemplateFile = path.join(temporary, "invalid.docx");
  await writeFile(templateFile, Buffer.from(docxResult.stdout.trim(), "base64"));
  await writeFile(invalidTemplateFile, "not a DOCX archive");
  const server = spawn(python, [
    "-m", "tests.browser.administration_server", "--port", String(port),
    "--data", path.join(temporary, "data"),
  ], { cwd: repository, stdio: ["ignore", "pipe", "pipe"] });
  let serverOutput = "";
  server.stdout.on("data", (chunk) => { serverOutput += chunk; });
  server.stderr.on("data", (chunk) => { serverOutput += chunk; });
  context.after(async () => {
    server.kill("SIGTERM");
    await Promise.race([
      new Promise((resolve) => server.once("exit", resolve)),
      new Promise((resolve) => setTimeout(resolve, 3_000)),
    ]);
    await rm(temporary, { recursive: true, force: true });
  });
  await waitForHttp(`${baseUrl}/health/live`).catch((error) => {
    throw new Error(`Administration server did not start: ${serverOutput}`, { cause: error });
  });

  const puppeteer = (await import(pathToFileURL(puppeteerModule).href)).default;
  const browser = await puppeteer.launch({
    executablePath: process.env.MD_CONVERTER_TEST_CHROMIUM || "/usr/bin/google-chrome-stable",
    headless: "shell",
  });
  context.after(() => browser.close());
  const page = await browser.newPage();

  await login(page, baseUrl, "browser-admin", "browser-password");
  const adminPage = await page.goto(`${baseUrl}/templates`, { waitUntil: "networkidle0" });
  assert.match(adminPage.headers()["content-security-policy"], /script-src 'self'/);
  await waitForText(page, "body", "Local accounts");
  for (const username of ["Alice", "Bob"]) {
    await page.type('#create-user-form input[name="username"]', username);
    await page.type('#create-user-form input[name="password"]', `${username.toLowerCase()}-password`);
    await page.click('#create-user-form button[type="submit"]');
    await waitForText(page, "#administration-alert", "Account was created.");
  }
  await page.type("#user-search", "Alice");
  await waitForText(page, "#user-list", "Alice");
  await page.$eval("#user-list .management-card .danger", (button) => button.click());
  await waitForText(page, "#administration-alert", "Alice is now inactive.");
  await page.$eval("#user-list .management-card button", (button) => button.click());
  await waitForText(page, "#administration-alert", "Alice is now active.");

  await clearSession(page);
  await login(page, baseUrl, "alice", "alice-password");
  await page.goto(`${baseUrl}/templates`, { waitUntil: "networkidle0" });
  assert.doesNotMatch(await page.$eval("body", (body) => body.innerText), /Local accounts/);
  await page.type('#create-template-form input[name="name"]', "Invalid report");
  await page.type('#create-template-form textarea[name="description"]', "Rejected safely");
  await page.type('#create-template-form input[name="expected_fonts"]', "Calibri");
  await (await page.$('#create-template-form input[name="content"]')).uploadFile(invalidTemplateFile);
  await page.click('#create-template-form button[type="submit"]');
  await waitForText(page, "#administration-alert", "Word template package is invalid.");
  await page.$eval("#create-template-form", (form) => form.reset());
  await page.type('#create-template-form input[name="name"]', "Alice report");
  await page.type('#create-template-form textarea[name="description"]', "Owned by Alice");
  await page.type('#create-template-form input[name="expected_fonts"]', "Calibri, Cambria, Courier New");
  await (await page.$('#create-template-form input[name="content"]')).uploadFile(templateFile);
  await page.$eval("#create-template-form", (form) => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  await waitForText(page, "#administration-alert", "Template was created.");
  await waitForText(page, "#managed-template-list", "Alice report");
  const concurrencyFailures = await page.evaluate(async () => {
    const listing = await (await fetch("/api/v1/templates?limit=100")).json();
    const item = listing.items[0];
    const csrf = decodeURIComponent(document.cookie.split(";").map((part) => part.trim())
      .find((part) => part.startsWith("__Host-md_converter_csrf=")).split("=")[1]);
    const headers = {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
      "If-Match": `"template-${item.id}-${item.revision}"`,
    };
    const missingCsrf = await fetch(`/api/v1/templates/${item.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "If-Match": headers["If-Match"] },
      body: JSON.stringify({ name: "Missing CSRF", description: "Rejected" }),
    });
    const first = await fetch(`/api/v1/templates/${item.id}`, {
      method: "PATCH", headers,
      body: JSON.stringify({ name: "Alice concurrent report", description: "First writer" }),
    });
    const stale = await fetch(`/api/v1/templates/${item.id}`, {
      method: "PATCH", headers,
      body: JSON.stringify({ name: "Lost update", description: "Stale writer" }),
    });
    return {
      total: listing.total,
      missing: { status: missingCsrf.status, body: await missingCsrf.json() },
      first: first.status,
      stale: { status: stale.status, body: await stale.json() },
    };
  });
  assert.equal(concurrencyFailures.total, 1);
  assert.equal(concurrencyFailures.missing.status, 403);
  assert.equal(concurrencyFailures.missing.body.error.code, "CSRF_REQUIRED");
  assert.equal(concurrencyFailures.first, 200);
  assert.equal(concurrencyFailures.stale.status, 412);
  assert.equal(concurrencyFailures.stale.body.error.code, "TEMPLATE_PRECONDITION_FAILED");
  await page.reload({ waitUntil: "networkidle0" });
  await waitForText(page, "#managed-template-list", "Alice concurrent report");
  await page.click("#managed-template-list details summary");
  await page.$eval("#managed-template-list details form", (form) => {
    form.elements.name.value = "Alice renamed report";
    form.elements.description.value = "Updated description";
  });
  await page.click('#managed-template-list details form button[type="submit"]');
  await waitForText(page, "#administration-alert", "Template details were saved.");
  await waitForText(page, "#managed-template-list", "Alice renamed report");
  await page.click("#managed-template-list details summary");
  const replacementFile = await page.$('#managed-template-list details form:nth-of-type(2) input[name="content"]');
  await replacementFile.uploadFile(templateFile);
  await page.type('#managed-template-list details form:nth-of-type(2) input[name="expected_fonts"]', "Calibri, Cambria, Courier New");
  await page.click('#managed-template-list details form:nth-of-type(2) button[type="submit"]');
  await waitForText(page, "#administration-alert", "Template content was replaced.");
  await page.waitForFunction(() => {
    const details = document.querySelector("#managed-template-list details");
    return details && !details.open;
  });
  await page.click("#managed-template-list details summary");
  await page.$$eval("#managed-template-list details button", (buttons) => {
    buttons.find((button) => button.textContent === "Load version history").click();
  });
  await waitForText(page, ".version-list", "Version 2");
  await page.$$eval("#managed-template-list details button", (buttons) => {
    buttons.find((button) => button.textContent === "Restore").click();
  });
  await waitForText(page, "#administration-alert", "restored as a new version");
  await page.waitForFunction(() => {
    const details = document.querySelector("#managed-template-list details");
    return details && !details.open;
  });
  const currentDownload = await page.evaluate(async () => {
    const link = document.querySelector("#managed-template-list a");
    const response = await fetch(link.href);
    return {
      status: response.status,
      disposition: response.headers.get("Content-Disposition"),
      size: (await response.arrayBuffer()).byteLength,
    };
  });
  assert.equal(currentDownload.status, 200);
  assert.match(currentDownload.disposition, /attachment/);
  assert.ok(currentDownload.size > 0);
  const aliceSession = (await page.cookies()).find((cookie) => cookie.name === "md_converter_session");

  await clearSession(page);
  await login(page, baseUrl, "bob", "bob-password");
  await page.goto(`${baseUrl}/templates`, { waitUntil: "networkidle0" });
  await waitForText(page, "#managed-template-list", "Alice renamed report");
  await waitForText(page, "#managed-template-list", "Alice");
  assert.equal(await page.$("#managed-template-list details"), null);
  const forbidden = await page.evaluate(async () => {
    const listing = await (await fetch("/api/v1/templates?limit=100")).json();
    const item = listing.items[0];
    const csrf = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("__Host-md_converter_csrf="))?.split("=")[1];
    const response = await fetch(`/api/v1/templates/${item.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": decodeURIComponent(csrf), "If-Match": `"template-${item.id}-${item.revision}"` },
      body: JSON.stringify({ name: "Forbidden", description: "Forbidden" }),
    });
    return { status: response.status, body: await response.json() };
  });
  assert.equal(forbidden.status, 403);
  assert.equal(forbidden.body.error.code, "FORBIDDEN");

  await clearSession(page);
  await login(page, baseUrl, "browser-admin", "browser-password");
  await page.goto(`${baseUrl}/templates`, { waitUntil: "networkidle0" });
  await waitForText(page, "#managed-template-list", "Alice renamed report");
  assert.ok(await page.$("#managed-template-list details"));
  await page.type("#user-search", "Alice");
  await page.type('#user-list input[name="password"]', "alice-new-password");
  await page.click('#user-list form button[type="submit"]');
  await waitForText(page, "#administration-alert", "Password reset completed for Alice.");
  await page.$$eval("#managed-template-list button", (buttons) => {
    buttons.find((button) => button.textContent === "Make preferred").click();
  });
  await waitForText(page, "#administration-alert", "preferred template");
  page.once("dialog", (dialog) => dialog.accept());
  await page.$$eval("#managed-template-list button", (buttons) => {
    buttons.find((button) => button.textContent === "Archive").click();
  });
  await waitForText(page, "#administration-alert", "Template was archived.");
  await page.waitForFunction(() => [...document.querySelectorAll("#managed-template-list button")]
    .some((button) => button.textContent === "Delete"));
  page.once("dialog", (dialog) => dialog.accept());
  await page.$$eval("#managed-template-list button", (buttons) => {
    buttons.find((button) => button.textContent === "Delete").click();
  });
  await waitForText(page, "#administration-alert", "has changed or the operation is not allowed.");
  await page.$$eval("#managed-template-list button", (buttons) => {
    buttons.find((button) => button.textContent === "Clear preferred").click();
  });
  await waitForText(page, "#administration-alert", "preferred template was cleared");

  await clearSession(page);
  await page.setCookie({ ...aliceSession, url: baseUrl });
  await page.goto(`${baseUrl}/templates`, { waitUntil: "networkidle0" });
  assert.equal(page.url(), `${baseUrl}/login`);
  await clearSession(page);
  await login(page, baseUrl, "alice", "alice-new-password");
  await page.goto(`${baseUrl}/templates`, { waitUntil: "networkidle0" });
  await waitForText(page, "#managed-template-list", "Alice renamed report");
  page.once("dialog", (dialog) => dialog.accept());
  await page.$$eval("#managed-template-list button", (buttons) => {
    buttons.find((button) => button.textContent === "Delete").click();
  });
  await waitForText(page, "#administration-alert", "Template was deleted.");
  await waitForText(page, "#managed-template-list", "No templates match");
});
