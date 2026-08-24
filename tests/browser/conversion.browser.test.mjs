import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const puppeteerModule = process.env.MD_CONVERTER_TEST_PUPPETEER || path.join(
  repository,
  "spikes/toolchain/node_modules/puppeteer-core/lib/puppeteer/puppeteer-core.js",
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
      const request = http.get(url, (response) => {
        response.resume();
        resolve();
      });
      request.once("error", (error) => {
        if (Date.now() >= deadline) reject(error);
        else setTimeout(attempt, 50);
      });
    };
    attempt();
  });
}

async function waitForText(page, selector, expected, timeout = 10_000) {
  await page.waitForFunction(
    (selected, text) => document.querySelector(selected)?.textContent.includes(text),
    { timeout },
    selector,
    expected,
  );
}

async function serverState(page) {
  return page.evaluate(async () => (await fetch("/__test/state")).json());
}

test("authenticated conversion workflow works in pinned Chromium", { timeout: 45_000 }, async (context) => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "md-converter-browser-"));
  const source = path.join(temporary, "source.md");
  await writeFile(source, "# Browser acceptance\n");

  const port = await availablePort();
  const baseUrl = `http://localhost:${port}`;
  const python = process.env.MD_CONVERTER_TEST_PYTHON || path.join(repository, ".venv/bin/python");
  const server = spawn(python, [
    "-m", "tests.browser.server", "--port", String(port),
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
    throw new Error(`Browser test server did not start: ${serverOutput}`, { cause: error });
  });

  const puppeteer = (await import(pathToFileURL(puppeteerModule).href)).default;
  const browser = await puppeteer.launch({
    executablePath: process.env.MD_CONVERTER_TEST_CHROMIUM || "/usr/bin/google-chrome-stable",
    headless: "shell",
  });
  context.after(() => browser.close());
  const page = await browser.newPage();
  const scriptResponses = [];
  page.on("response", (response) => {
    if (response.url().endsWith("/static/conversion.js")) scriptResponses.push(response.status());
  });

  const loginResponse = await page.goto(`${baseUrl}/login`, { waitUntil: "networkidle0" });
  assert.match(loginResponse.headers()["content-security-policy"], /script-src 'self'/);
  await page.type('input[name="username"]', "browser-admin");
  await page.type('input[name="password"]', "browser-password");
  // Headless Chrome serializes form origins as `null` in this isolated
  // arbitrary-UID container; provide the exact document origin through CDP.
  await page.setExtraHTTPHeaders({ Origin: baseUrl });
  const [conversionResponse] = await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle0" }),
    page.click('button[type="submit"]'),
  ]);
  const loginDiagnostic = await serverState(page);
  await page.setExtraHTTPHeaders({});
  assert.equal(
    page.url(),
    `${baseUrl}/convert`,
    `login ended with ${conversionResponse.status()} (${JSON.stringify(loginDiagnostic.last_login_origin)}): ${await page.$eval("body", (body) => body.innerText)}`,
  );
  assert.equal(loginDiagnostic.last_login_origin.origin, baseUrl);
  assert.match(conversionResponse.headers()["content-security-policy"], /script-src 'self'/);
  assert.deepEqual(scriptResponses, [200]);
  await waitForText(page, "#selected-template", "Preferred template");
  await waitForText(page, "#selected-template", "Preferred report");

  const cookies = await page.cookies();
  const csrfCookie = cookies.find((cookie) => cookie.name === "__Host-md_converter_csrf");
  const sessionCookie = cookies.find((cookie) => cookie.name === "md_converter_session");
  assert.equal(csrfCookie.secure, true);
  assert.equal(csrfCookie.httpOnly, false);
  assert.equal(sessionCookie.secure, true);
  assert.equal(sessionCookie.httpOnly, true);

  const sourceInput = await page.$("#source");
  await sourceInput.uploadFile(source);

  const cdp = await page.createCDPSession();
  let failFirstAccepted = true;
  await cdp.send("Fetch.enable", {
    patterns: [{ urlPattern: "*/api/v1/conversions", requestStage: "Response" }],
  });
  cdp.on("Fetch.requestPaused", (event) => {
    const fail = failFirstAccepted && event.responseStatusCode === 202;
    if (fail) failFirstAccepted = false;
    const command = fail ? "Fetch.failRequest" : "Fetch.continueRequest";
    const parameters = fail
      ? { requestId: event.requestId, errorReason: "Aborted" }
      : { requestId: event.requestId };
    void cdp.send(command, parameters).catch(() => {});
  });

  await page.click("#submit-conversion");
  await waitForText(page, "#page-alert", "reuse the same request key");
  await page.click("#submit-conversion");
  await waitForText(page, "#job-status", "ready to download");
  const stateAfterSuccess = await serverState(page);
  assert.equal(stateAfterSuccess.idempotency_keys.length, 2);
  assert.equal(stateAfterSuccess.idempotency_keys[0], stateAfterSuccess.idempotency_keys[1]);
  assert.deepEqual(stateAfterSuccess.outputs, ["docx"]);
  const downloadHref = await page.$eval("#download-result", (link) => link.getAttribute("href"));
  const successJobId = downloadHref.split("/").at(-2);
  const successPolls = stateAfterSuccess.poll_times[successJobId];
  assert.equal(successPolls.length, 2);
  assert.ok((successPolls[1] - successPolls[0]) * 1_000 >= 1_400);
  const download = await page.evaluate(async (href) => {
    const response = await fetch(href);
    return {
      status: response.status,
      disposition: response.headers.get("Content-Disposition"),
      cache: response.headers.get("Cache-Control"),
      contentTypeOptions: response.headers.get("X-Content-Type-Options"),
      content: await response.text(),
    };
  }, downloadHref);
  assert.equal(download.status, 200);
  assert.match(download.disposition, /^attachment; filename="conversion-[\da-f-]+\.docx"$/);
  assert.equal(download.cache, "private, no-store");
  assert.equal(download.contentTypeOptions, "nosniff");
  assert.equal(download.content, "browser acceptance result");

  await page.type("#template-search", "Alternate");
  await page.waitForSelector("#template-results button");
  await page.focus("#template-results button");
  await page.keyboard.press("Enter");
  await waitForText(page, "#selected-template", "Alternate brief");
  await page.$eval("#template-search", (input) => {
    input.value = "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.type("#template-search", "error");
  await waitForText(page, "#page-alert", "Template storage is unavailable.");
  assert.equal(await page.$eval("#page-alert", (element) => element.getAttribute("aria-live")), "assertive");
  await waitForText(page, "#selected-template", "Alternate brief");

  await page.evaluate(() => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["# Dropped\n"], "dropped.md", { type: "text/markdown" }));
    const zone = document.querySelector("#drop-zone");
    zone.dispatchEvent(new DragEvent("dragenter", { bubbles: true, dataTransfer: transfer }));
    zone.dispatchEvent(new DragEvent("drop", { bubbles: true, dataTransfer: transfer }));
  });
  assert.equal(await page.$eval("#source", (input) => input.files[0].name), "dropped.md");

  await page.click('input[name="output"][value="pdf"]');
  await page.click("#submit-conversion");
  await page.waitForSelector("#cancel-job:not([hidden])");
  await page.click("#cancel-job");
  await waitForText(page, "#job-status", "Cancellation requested");
  await waitForText(page, "#job-status", "conversion was cancelled");
  const stateAfterCancel = await serverState(page);
  assert.equal(stateAfterCancel.cancelled_ids.length, 1);
  assert.deepEqual(stateAfterCancel.outputs, ["docx", "pdf"]);

  await page.click('input[name="output"][value="both"]');
  await page.click("#submit-conversion");
  await waitForText(page, "#job-status", "expired and its files are no longer available");
  const stateAfterExpiration = await serverState(page);
  assert.deepEqual(stateAfterExpiration.outputs, ["docx", "pdf", "both"]);

  await cdp.send("Network.deleteCookies", {
    name: "__Host-md_converter_csrf",
    url: baseUrl,
  });
  await page.click('input[name="output"][value="docx"]');
  await page.click("#submit-conversion");
  await waitForText(page, "#page-alert", "A valid CSRF token is required.");
  assert.deepEqual((await serverState(page)).outputs, ["docx", "pdf", "both"]);
});
