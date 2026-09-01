import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { test } from "node:test";

const pagePort = 31960;
const probePort = 31961;
let processUnderTest;

async function waitReady() {
  for (let attempt = 0; attempt < 50; attempt += 1) {
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
    env: {
      ...process.env,
      HOSTNAME: "127.0.0.1",
      PORT: String(pagePort),
      PROBE_PORT: String(probePort),
    },
    stdio: "pipe",
  });
  await waitReady();
});
test.after(async () => {
  processUnderTest.kill("SIGTERM");
  await new Promise((resolve) => processUnderTest.once("exit", resolve));
});

test("dynamic HTML receives fresh strict nonce policies", async () => {
  const first = await fetch(`http://127.0.0.1:${pagePort}/convert`);
  const second = await fetch(`http://127.0.0.1:${pagePort}/convert`);
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
  assert.equal(first.headers.get("cache-control"), "no-store");
  assert.equal(first.headers.get("referrer-policy"), "same-origin");
  assert.equal(first.headers.get("x-content-type-options"), "nosniff");
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
