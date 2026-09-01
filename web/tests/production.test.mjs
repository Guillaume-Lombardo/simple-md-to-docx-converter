import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { request } from "node:http";
import { dirname, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const pagePort = 31960;
const probePort = 31961;
const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
let processUnderTest;
let processExit;

async function waitReady(cancelled) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (cancelled()) return;
    try {
      if (
        (await fetch(`http://127.0.0.1:${probePort}/_frontend/health/ready`))
          .status === 200
      )
        return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("frontend did not become ready");
}

test.before(async () => {
  processUnderTest = spawn(process.execPath, ["server.mjs"], {
    cwd: webRoot,
    env: {
      ...process.env,
      HOSTNAME: "127.0.0.1",
      MARKWEAVE_FRONTEND_ERROR_TEST: "1",
      PORT: String(pagePort),
      PROBE_PORT: String(probePort),
    },
    stdio: ["ignore", "inherit", "inherit"],
  });
  processExit = new Promise((resolveExit) => {
    processUnderTest.once("exit", (code, signal) =>
      resolveExit({ code, signal }),
    );
    processUnderTest.once("error", (error) => resolveExit({ error }));
  });
  let startupFinished = false;
  const startup = await Promise.race([
    waitReady(() => startupFinished).then(() => ({ ready: true })),
    processExit.then(({ code, error, signal }) => ({
      code,
      error,
      ready: false,
      signal,
    })),
  ]);
  startupFinished = true;
  if (!startup.ready)
    throw new Error(
      `frontend exited before readiness (code=${startup.code}, signal=${startup.signal}, error=${startup.error?.message ?? "none"})`,
    );
});
test.after(async () => {
  if (
    processUnderTest &&
    processUnderTest.exitCode === null &&
    processUnderTest.signalCode === null
  )
    processUnderTest.kill("SIGTERM");
  if (processExit) await processExit;
});

async function assertNonceHtml(
  path,
  status,
  headers = { Accept: "text/html" },
) {
  const options = { headers };
  const first = await fetch(`http://127.0.0.1:${pagePort}${path}`, options);
  const second = await fetch(`http://127.0.0.1:${pagePort}${path}`, options);
  assert.equal(first.status, status);
  assert.match(first.headers.get("content-type"), /^text\/html/);
  const firstCsp = first.headers.get("content-security-policy");
  const secondCsp = second.headers.get("content-security-policy");
  assert.match(firstCsp, /script-src 'nonce-([^']+)' 'strict-dynamic'/);
  assert.notEqual(firstCsp, secondCsp);
  assert.ok(
    !firstCsp.includes("unsafe-inline") && !firstCsp.includes("unsafe-eval"),
  );
  const nonce = firstCsp.match(/script-src 'nonce-([^']+)'/)?.[1];
  assert.ok(firstCsp.includes(`style-src 'self' 'nonce-${nonce}'`));
  const html = await first.text();
  for (const tag of html.matchAll(/<(script|style)\b([^>]*)>/g))
    assert.equal(tag[2].match(/\bnonce=["']([^"']+)["']/)?.[1], nonce);
  assert.doesNotMatch(html, /\sstyle=/i);
  assert.match(first.headers.get("cache-control"), /(?:^|,\s*)no-store(?:,|$)/);
  assert.equal(first.headers.get("referrer-policy"), "same-origin");
  assert.equal(first.headers.get("x-content-type-options"), "nosniff");
  return html;
}

test("all dynamic and generated error HTML receives fresh nonce policies", async () => {
  await assertNonceHtml("/convert", 200);
  await assertNonceHtml("/missing", 404);
  await assertNonceHtml("/missing.js", 404);
  await assertNonceHtml("/favicon.ico", 404);
  const error = await assertNonceHtml("/foundation-error", 500);
  assert.doesNotMatch(error, /foundation error canary/i);
  await assertNonceHtml("/convert", 200, { Accept: "*/*" });
  await assertNonceHtml("/convert", 200, { Accept: "application/xhtml+xml" });
  await assertNonceHtml("/convert", 200, { Accept: "TEXT/HTML" });

  const absentAccept = await new Promise((resolve, reject) => {
    const call = request(
      { host: "127.0.0.1", path: "/convert", port: pagePort },
      (response) => {
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => (body += chunk));
        response.on("end", () => resolve({ body, headers: response.headers }));
      },
    );
    call.on("error", reject);
    call.end();
  });
  const absentCsp = absentAccept.headers["content-security-policy"];
  assert.match(absentCsp, /script-src 'nonce-([^']+)' 'strict-dynamic'/);
  const absentNonce = absentCsp.match(/script-src 'nonce-([^']+)'/)?.[1];
  for (const tag of absentAccept.body.matchAll(/<(script|style)\b([^>]*)>/g))
    assert.equal(tag[2].match(/\bnonce=["']([^"']+)["']/)?.[1], absentNonce);

  await assertNonceHtml("/convert", 200, {
    Accept: "text/html",
    Purpose: "prefetch",
  });
  for (const headers of [
    { Accept: "text/html", RSC: "1" },
    { Accept: "text/html", "Next-Router-Prefetch": "1" },
    { Accept: "text/html", "Next-Router-Segment-Prefetch": "1" },
  ])
    await assertNonceHtml("/foundation-response?kind=html", 200, headers);
});

test("component, non-HTML, and content-free responses receive no CSP", async () => {
  const component = await fetch(`http://127.0.0.1:${pagePort}/convert`, {
    headers: { Accept: "text/x-component", RSC: "1" },
  });
  assert.match(component.headers.get("content-type"), /^text\/x-component/);
  assert.equal(component.headers.get("content-security-policy"), null);

  const json = await fetch(
    `http://127.0.0.1:${pagePort}/foundation-response?kind=json`,
    { headers: { Accept: "text/html" } },
  );
  assert.equal(json.headers.get("content-type"), "application/json");
  assert.equal(json.headers.get("content-security-policy"), null);

  const empty = await fetch(
    `http://127.0.0.1:${pagePort}/foundation-response?kind=empty`,
    { headers: { Accept: "text/html" } },
  );
  assert.equal(empty.status, 204);
  assert.equal(await empty.text(), "");
  assert.equal(empty.headers.get("content-security-policy"), null);

  const head = await fetch(`http://127.0.0.1:${pagePort}/convert`, {
    method: "HEAD",
    headers: { Accept: "text/html" },
  });
  assert.equal(await head.text(), "");
  assert.equal(head.headers.get("content-security-policy"), null);
});

test("internal probes are isolated on the probe listener", async () => {
  assert.equal(
    (await fetch(`http://127.0.0.1:${probePort}/_frontend/health/live`)).status,
    200,
  );
  assert.equal(
    (await fetch(`http://127.0.0.1:${pagePort}/_frontend/health/live`)).status,
    404,
  );
});

test("hashed assets are immutable and receive no nonce CSP", async () => {
  const html = await (
    await fetch(`http://127.0.0.1:${pagePort}/convert`)
  ).text();
  const asset = html.match(
    /(?:src|href)="([^"?]*\/_next\/static\/[^"?]+\.(?:css|js))/,
  )?.[1];
  assert.ok(asset);
  const response = await fetch(`http://127.0.0.1:${pagePort}${asset}`);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-security-policy"), null);
  assert.match(response.headers.get("cache-control"), /immutable/);
});
