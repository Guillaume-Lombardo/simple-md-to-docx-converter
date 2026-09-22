/** Compare the existing strict CSP with renderer behavior in real Chromium. */
import { randomBytes } from "node:crypto";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { resolve } from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core");
const root = resolve(import.meta.dirname, "../..");
const vendor = import.meta.dirname;
const generated = resolve(process.argv[2] ?? "");
if (!process.argv[2]) throw new Error("Pass the generated fixture directory");
const allowWorker = process.argv[3] === "allow-worker";
const allowStyles = process.argv[3] === "allow-styles";

const routes = new Map([
  [
    "/probe.mjs",
    [
      resolve(import.meta.dirname, "browser_preview_client.mjs"),
      "text/javascript",
    ],
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
    "/vendor/pptx.browser.mjs",
    [
      resolve(
        vendor,
        "node_modules/@aiden0z/pptx-renderer/dist/aiden0z-pptx-renderer.browser.es.js",
      ),
      "text/javascript",
    ],
  ],
  [
    "/vendor/pdf.mjs",
    [
      resolve(vendor, "node_modules/pdfjs-dist/build/pdf.min.mjs"),
      "text/javascript",
    ],
  ],
  [
    "/vendor/pdf.worker.mjs",
    [
      resolve(vendor, "node_modules/pdfjs-dist/build/pdf.worker.min.mjs"),
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
    "/fixture/pdf",
    [resolve(root, "spikes/anydoc/corpus/pdf/text.pdf"), "application/pdf"],
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
  ["/fixture/long.pdf", [resolve(generated, "long.pdf"), "application/pdf"]],
  [
    "/fixture/break.docx",
    [
      resolve(generated, "break.docx"),
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
  ],
]);

function csp(nonce) {
  return [
    "default-src 'none'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    `script-src 'nonce-${nonce}' 'strict-dynamic'`,
    allowStyles
      ? "style-src 'self' 'unsafe-inline'"
      : `style-src 'self' 'nonce-${nonce}'`,
    "connect-src 'self'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "manifest-src 'self'",
    allowWorker ? "worker-src 'self'" : "worker-src 'none'",
  ].join("; ");
}

const server = createServer(async (request, response) => {
  try {
    if (request.url === "/") {
      const nonce = randomBytes(18).toString("base64");
      response.writeHead(200, {
        "Content-Type": "text/html",
        "Content-Security-Policy": csp(nonce),
      });
      response.end(
        `<main><div id="docx"></div><div id="docx-break"></div><div id="pptx"></div><canvas id="pdf"></canvas><div id="docx-long"></div><div id="pptx-long"></div><canvas id="pdf-long"></canvas></main><script nonce="${nonce}" src="/vendor/jszip.js"></script><script nonce="${nonce}" src="/vendor/docx.js"></script><script nonce="${nonce}" type="module" src="/probe.mjs"></script>`,
      );
      return;
    }
    const route = routes.get(request.url ?? "");
    if (!route) {
      response.writeHead(404);
      response.end();
      return;
    }
    const [path, contentType] = route;
    const content = await readFile(path);
    response.writeHead(200, {
      "Content-Type": contentType,
      "Cache-Control": "no-store",
    });
    response.end(content);
  } catch (error) {
    response.writeHead(500);
    response.end(String(error));
  }
});

await new Promise((done) => server.listen(0, "127.0.0.1", done));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({
    viewport: { width: 1400, height: 900 },
  });
  await page.addInitScript(() => {
    window.__violations = [];
    document.addEventListener("securitypolicyviolation", (event) => {
      window.__violations.push({
        directive: event.effectiveDirective,
        blocked: event.blockedURI,
        target: event.target?.tagName ?? null,
      });
    });
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  const port = server.address().port;
  const origin = `http://127.0.0.1:${port}`;
  const externalRequests = [];
  await page.route("**/*", (route) => {
    if (route.request().url().startsWith(`${origin}/`)) return route.continue();
    externalRequests.push(route.request().url());
    return route.abort();
  });
  await page.goto(`${origin}/`, { waitUntil: "load" });
  await page.waitForFunction(() => window.__probe !== undefined, null, {
    timeout: 90_000,
  });
  const browserResult = await page.evaluate(() => ({
    result: window.__probe,
    violations: window.__violations,
  }));
  console.log(
    JSON.stringify(
      {
        ...browserResult,
        external_requests: externalRequests,
        page_errors: errors,
      },
      null,
      2,
    ),
  );
} finally {
  await browser.close();
  await new Promise((done) => server.close(done));
}
