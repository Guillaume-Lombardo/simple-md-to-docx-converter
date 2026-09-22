/** Test native Office DOM previews in an opaque-origin sandboxed frame. */
import { randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core");
const root = resolve(import.meta.dirname, "../..");
const vendor = import.meta.dirname;
if (!process.argv[2]) throw new Error("Pass the generated fixture directory");
const generated = resolve(process.argv[2]);
const pptxBuild = await build({
  entryPoints: [
    resolve(
      vendor,
      "node_modules/@aiden0z/pptx-renderer/dist/aiden0z-pptx-renderer.browser.es.js",
    ),
  ],
  bundle: true,
  format: "iife",
  globalName: "ComposerPptx",
  platform: "browser",
  write: false,
});
const pptxScript = pptxBuild.outputFiles[0].contents;
const routes = new Map([
  [
    "/parent.mjs",
    [resolve(vendor, "isolated_parent_client.mjs"), "text/javascript"],
  ],
  [
    "/frame-client.js",
    [resolve(vendor, "isolated_frame_client.mjs"), "text/javascript"],
  ],
  [
    "/vendor/jszip.js",
    [
      resolve(vendor, "node_modules/jszip/dist/jszip.min.js"),
      "text/javascript",
    ],
  ],
  [
    "/vendor/docx.js",
    [
      resolve(vendor, "node_modules/docx-preview/dist/docx-preview.min.js"),
      "text/javascript",
    ],
  ],
  [
    "/fixture/docx",
    [
      resolve(root, "spikes/anydoc/corpus/docx/text.docx"),
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
  ],
  [
    "/fixture/pptx",
    [
      resolve(root, "spikes/anydoc/corpus/pptx/pres.pptx"),
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ],
  ],
  [
    "/fixture/long.docx",
    [
      resolve(generated, "long.docx"),
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
  ],
  [
    "/fixture/long.pptx",
    [
      resolve(generated, "long.pptx"),
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ],
  ],
]);

function parentPolicy(nonce) {
  return [
    "default-src 'none'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    `script-src 'nonce-${nonce}' 'strict-dynamic'`,
    `style-src 'self' 'nonce-${nonce}'`,
    "connect-src 'self'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "manifest-src 'self'",
    "worker-src 'none'",
    "frame-src 'self'",
  ].join("; ");
}

function framePolicy(nonce) {
  return [
    "default-src 'none'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-ancestors 'self'",
    "form-action 'none'",
    `script-src 'nonce-${nonce}' 'strict-dynamic'`,
    "style-src 'unsafe-inline'",
    "connect-src 'none'",
    "img-src data: blob:",
    "font-src data: blob:",
    "worker-src 'none'",
  ].join("; ");
}

const server = createServer(async (request, response) => {
  try {
    const nonce = randomBytes(18).toString("base64");
    if (request.url === "/") {
      response.writeHead(200, {
        "Content-Type": "text/html",
        "Content-Security-Policy": parentPolicy(nonce),
      });
      response.end(
        `<iframe id="preview" sandbox="allow-scripts" src="/frame"></iframe><iframe id="spoof" sandbox="allow-scripts" src="/spoof-frame"></iframe><script nonce="${nonce}" type="module" src="/parent.mjs"></script>`,
      );
      return;
    }
    if (request.url === "/spoof-frame") {
      response.writeHead(200, {
        "Content-Type": "text/html",
        "Content-Security-Policy": `default-src 'none'; script-src 'nonce-${nonce}'; frame-ancestors 'self'`,
      });
      response.end(
        `<script nonce="${nonce}">parent.postMessage({type:'ready'},'*'); parent.postMessage({type:'result',token:'forged',revision:2},'*');</script>`,
      );
      return;
    }
    if (request.url === "/frame") {
      response.writeHead(200, {
        "Content-Type": "text/html",
        "Content-Security-Policy": framePolicy(nonce),
      });
      response.end(
        `<div id="docx"></div><div id="pptx"></div><div id="docx-long"></div><div id="pptx-long"></div><script nonce="${nonce}" src="/vendor/jszip.js"></script><script nonce="${nonce}" src="/vendor/docx.js"></script><script nonce="${nonce}" src="/vendor/pptx.classic.js"></script><script nonce="${nonce}" src="/frame-client.js"></script>`,
      );
      return;
    }
    if (request.url === "/vendor/pptx.classic.js") {
      response.writeHead(200, {
        "Content-Type": "text/javascript",
        "Cache-Control": "no-store",
      });
      response.end(pptxScript);
      return;
    }
    const route = routes.get(request.url ?? "");
    if (!route) {
      response.writeHead(404);
      response.end();
      return;
    }
    const [path, type] = route;
    const headers = { "Content-Type": type, "Cache-Control": "no-store" };
    const body = await readFile(path);
    response.writeHead(200, headers);
    response.end(body);
  } catch (error) {
    response.writeHead(500);
    response.end(String(error));
  }
});

await new Promise((done) => server.listen(0, "127.0.0.1", done));
const browser = await chromium.launch({
  headless: true,
  args: ["--enable-precise-memory-info"],
});
try {
  const page = await browser.newPage({
    viewport: { width: 1400, height: 900 },
  });
  const violations = [];
  const errors = [];
  const externalRequests = [];
  await page.addInitScript(() => {
    window.__violations = [];
    document.addEventListener("securitypolicyviolation", (event) => {
      window.__violations.push({
        directive: event.effectiveDirective,
        blocked: event.blockedURI,
      });
    });
  });
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  const port = server.address().port;
  const origin = `http://127.0.0.1:${port}`;
  await page.route("**/*", (route) => {
    if (route.request().url().startsWith(`${origin}/`)) return route.continue();
    externalRequests.push(route.request().url());
    return route.abort();
  });
  await page.goto(`${origin}/`, { waitUntil: "load" });
  try {
    await page.waitForFunction(
      () => window.__isolatedProbe !== undefined,
      null,
      { timeout: 10_000 },
    );
  } catch (error) {
    errors.push(String(error));
  }
  const frame = page.frame({ url: `${origin}/frame` });
  violations.push(...(await page.evaluate(() => window.__violations)));
  if (frame)
    violations.push(...(await frame.evaluate(() => window.__violations)));
  const cdp = await page.context().newCDPSession(page);
  const domCounters = await cdp.send("Memory.getDOMCounters");
  console.log(
    JSON.stringify(
      {
        result: await page.evaluate(() => window.__isolatedProbe ?? null),
        frame_url: frame?.url() ?? null,
        dom_counters: domCounters,
        violations,
        errors,
        external_requests: externalRequests,
      },
      null,
      2,
    ),
  );
} finally {
  await browser.close();
  await new Promise((done) => server.close(done));
}
