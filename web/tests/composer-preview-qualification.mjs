import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { resolve } from "node:path";
import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import JSZip from "jszip";
import { build } from "esbuild";

const base = process.env.MARKWEAVE_PREVIEW_BASE_URL ?? "http://127.0.0.1:33771";
const root = resolve(import.meta.dirname, "../..");
const webRoot = resolve(root, "web");
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
const sourcePaths = [
  "src/composer/preview/frame-client.ts",
  "src/composer/preview/docx-window.ts",
  "src/composer/preview/docx-lists.ts",
  "src/composer/preview/composer-preview.tsx",
  "src/composer/preview/pdf-preview.tsx",
];
const sourceHashes = Object.fromEntries(
  await Promise.all(
    sourcePaths.map(async (path) => [
      path,
      sha256(await readFile(resolve(webRoot, path))),
    ]),
  ),
);
const bundlePath = resolve(webRoot, "public/composer-preview/frame-client.js");
const localBundle = await readFile(bundlePath);
const reproducible = await build({
  entryPoints: [resolve(webRoot, "src/composer/preview/frame-client.ts")],
  bundle: true,
  format: "iife",
  minify: true,
  outfile: bundlePath,
  platform: "browser",
  write: false,
});
assert.equal(
  sha256(localBundle),
  sha256(reproducible.outputFiles[0].contents),
  "Generated child bundle is stale relative to its source files.",
);
const vendorAssets = [
  ["jszip.js", "node_modules/jszip/dist/jszip.min.js"],
  ["docx.js", "node_modules/docx-preview/dist/docx-preview.min.js"],
];
const vendorHashes = {};
for (const [asset, source] of vendorAssets) {
  const installed = await readFile(resolve(webRoot, source));
  const generated = await readFile(
    resolve(webRoot, "public/composer-preview", asset),
  );
  const response = await fetch(`${base}/composer-preview/${asset}`, {
    cache: "no-store",
  });
  assert.equal(response.status, 200);
  const served = Buffer.from(await response.arrayBuffer());
  assert.equal(sha256(installed), sha256(generated), `${asset} is stale.`);
  assert.equal(
    sha256(generated),
    sha256(served),
    `${asset} is stale on server.`,
  );
  vendorHashes[asset] = sha256(generated);
}
const pptxPath = resolve(webRoot, "public/composer-preview/pptx.js");
const pptxBundle = await readFile(pptxPath);
const pptxReproducible = await build({
  entryPoints: [
    resolve(
      webRoot,
      "node_modules/@aiden0z/pptx-renderer/dist/aiden0z-pptx-renderer.browser.es.js",
    ),
  ],
  bundle: true,
  format: "iife",
  globalName: "ComposerPptx",
  minify: true,
  outfile: pptxPath,
  platform: "browser",
  write: false,
});
assert.equal(
  sha256(pptxBundle),
  sha256(pptxReproducible.outputFiles[0].contents),
  "Generated PPTX renderer is stale.",
);
const pptxServed = await fetch(`${base}/composer-preview/pptx.js`, {
  cache: "no-store",
});
assert.equal(pptxServed.status, 200);
assert.equal(
  sha256(pptxBundle),
  sha256(Buffer.from(await pptxServed.arrayBuffer())),
  "Server is serving a stale PPTX renderer.",
);
vendorHashes["pptx.js"] = sha256(pptxBundle);
const notice = await readFile(
  resolve(webRoot, "public/composer-preview/THIRD_PARTY_NOTICES.txt"),
  "utf8",
);
const servedNotice = await fetch(
  `${base}/composer-preview/THIRD_PARTY_NOTICES.txt`,
  {
    cache: "no-store",
  },
);
assert.equal(servedNotice.status, 200);
assert.equal(
  sha256(Buffer.from(notice)),
  sha256(Buffer.from(await servedNotice.arrayBuffer())),
  "Server is serving stale third-party notices.",
);
for (const name of [
  "docx-preview@0.4.1",
  "jszip@3.10.2",
  "pdfjs-dist@6.3.289",
  "@aiden0z/pptx-renderer@1.3.0",
])
  assert.ok(notice.includes(name), `Missing ${name} license notice.`);
for (const [name, license] of [
  ["docx-preview", "LICENSE"],
  ["jszip", "LICENSE.markdown"],
  ["pdfjs-dist", "LICENSE"],
  ["@aiden0z/pptx-renderer", "LICENSE"],
  ["@aiden0z/pptx-renderer", "licenses/mtx-decompressor-MPL-2.0.txt"],
  ["@aiden0z/pptx-renderer", "licenses/ECMA-text-copyright-notice.txt"],
]) {
  const outputName = `${name.replaceAll(/[\\/@]/g, "-")}-${license.replaceAll("/", "-")}`;
  const installed = await readFile(
    resolve(webRoot, "node_modules", name, license),
  );
  const copied = await readFile(
    resolve(webRoot, "public/composer-preview/licenses", outputName),
  );
  assert.equal(sha256(installed), sha256(copied), `${outputName} is stale.`);
}
const servedResponse = await fetch(`${base}/composer-preview/frame-client.js`, {
  cache: "no-store",
});
assert.equal(servedResponse.status, 200);
assert.match(servedResponse.headers.get("cache-control") ?? "", /no-store/);
const servedBundle = Buffer.from(await servedResponse.arrayBuffer());
assert.equal(
  sha256(servedBundle),
  sha256(localBundle),
  "Server is serving a stale child bundle.",
);
const routeResponse = await fetch(`${base}/composer-preview`, {
  cache: "no-store",
});
assert.equal(routeResponse.status, 200);
const servedPolicy = routeResponse.headers.get("content-security-policy") ?? "";
const policyTemplate = servedPolicy.replace(/nonce-[^']+/g, "nonce-REDACTED");
let localNextBuildId = null;
try {
  localNextBuildId = (
    await readFile(resolve(webRoot, ".next/BUILD_ID"), "utf8")
  ).trim();
} catch {
  // The probe can run before an integrated Next build, but cannot certify it.
}
const trace = {
  base,
  node: process.version,
  localNextBuildId,
  servedRoutePolicySha256: sha256(Buffer.from(policyTemplate)),
  servedRouteHasStrictDynamic: servedPolicy.includes("strict-dynamic"),
  sourceHashes,
  bundleSha256: sha256(localBundle),
  vendorHashes,
  noticesSha256: sha256(Buffer.from(notice)),
  servedBundleSha256: sha256(servedBundle),
  servedCacheControl: servedResponse.headers.get("cache-control"),
  servedLastModified: servedResponse.headers.get("last-modified"),
  servedEtag: servedResponse.headers.get("etag"),
};
const docx = await readFile(
  resolve(root, "spikes/anydoc/corpus/docx/text.docx"),
);
const pptx = await readFile(
  resolve(root, "spikes/anydoc/corpus/pptx/pres.pptx"),
);
trace.fixtureHashes = { docx: sha256(docx), pptx: sha256(pptx) };
const externalPackage = await JSZip.loadAsync(docx);
const relationships = await externalPackage
  .file("word/_rels/document.xml.rels")
  .async("string");
externalPackage.file(
  "word/_rels/document.xml.rels",
  relationships.replace(
    "</Relationships>",
    '<Relationship Id="remote-image" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" TargetMode="External" Target="https://example.invalid/secret"/></Relationships>',
  ),
);
const svgPackage = await JSZip.loadAsync(docx);
svgPackage.file(
  "word/media/evil.svg",
  '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
);
const cases = [
  { label: "docx", format: "docx", bytes: docx, expected: "ready" },
  { label: "pptx", format: "pptx", bytes: pptx, expected: "ready" },
  {
    label: "docx-low-budget",
    format: "docx",
    bytes: docx,
    expected: "error",
    maxSerializedMarkupBytes: 1024,
  },
  {
    label: "docx-external-resource",
    format: "docx",
    bytes: await externalPackage.generateAsync({ type: "nodebuffer" }),
    expected: "error",
  },
  {
    label: "docx-svg",
    format: "docx",
    bytes: await svgPackage.generateAsync({ type: "nodebuffer" }),
    expected: "error",
  },
];
if (process.env.MARKWEAVE_PREVIEW_LONG_DOCX)
  cases.push({
    label: "docx-long",
    format: "docx",
    bytes: await readFile(process.env.MARKWEAVE_PREVIEW_LONG_DOCX),
    expected: "ready",
  });
if (process.env.MARKWEAVE_PREVIEW_MEDIA_DOCX)
  cases.push({
    label: "docx-media",
    format: "docx",
    bytes: await readFile(process.env.MARKWEAVE_PREVIEW_MEDIA_DOCX),
    expected: "ready",
  });
if (process.env.MARKWEAVE_PREVIEW_STRUCTURED_DOCX)
  cases.push({
    label: "docx-structured",
    format: "docx",
    bytes: await readFile(process.env.MARKWEAVE_PREVIEW_STRUCTURED_DOCX),
    expected: "ready",
  });
const browser = await chromium.launch({
  headless: true,
  args: ["--enable-precise-memory-info"],
});
trace.browser = browser.version();
console.log(JSON.stringify({ trace }));

async function diagnostics(page, identity) {
  await page.evaluate((identity) => {
    window.__previewDiagnostic = null;
    const frame = document.querySelector("#preview");
    const receive = (event) => {
      if (
        event.source === frame?.contentWindow &&
        event.origin === "null" &&
        event.data?.type === "composer-preview-diagnostics" &&
        event.data?.token === identity.token
      ) {
        window.__previewDiagnostic = event.data;
        window.removeEventListener("message", receive);
      }
    };
    window.addEventListener("message", receive);
    frame?.contentWindow?.postMessage(
      { ...identity, type: "composer-preview-diagnostics" },
      "*",
    );
  }, identity);
  await page.waitForFunction(() => window.__previewDiagnostic);
  return page.evaluate(() => window.__previewDiagnostic);
}

async function pixelDifference(page, firstPng, secondPng) {
  return page.evaluate(
    async ([firstPng, secondPng]) => {
      const decode = (base64) =>
        new Promise((resolve, reject) => {
          const image = new Image();
          image.onload = () => resolve(image);
          image.onerror = reject;
          image.src = `data:image/png;base64,${base64}`;
        });
      const [first, second] = await Promise.all([
        decode(firstPng),
        decode(secondPng),
      ]);
      if (first.width !== second.width || first.height !== second.height)
        return 1;
      const pixels = (image) => {
        const canvas = document.createElement("canvas");
        canvas.width = image.width;
        canvas.height = image.height;
        const context = canvas.getContext("2d");
        context.drawImage(image, 0, 0);
        return context.getImageData(0, 0, image.width, image.height).data;
      };
      const a = pixels(first);
      const b = pixels(second);
      let different = 0;
      for (let index = 0; index < a.length; index += 4)
        if (
          a[index] !== b[index] ||
          a[index + 1] !== b[index + 1] ||
          a[index + 2] !== b[index + 2] ||
          a[index + 3] !== b[index + 3]
        )
          different++;
      return different / (a.length / 4);
    },
    [firstPng.toString("base64"), secondPng.toString("base64")],
  );
}
try {
  for (const { label, format, bytes, expected } of cases) {
    const page = await browser.newPage({
      viewport: { width: 1300, height: 900 },
    });
    const errors = [];
    const external = [];
    const popups = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    page.on("popup", (popup) => {
      popups.push(popup.url());
      void popup.close();
    });
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    await page.context().route("**/*", (route) => {
      if (route.request().url().startsWith(`${base}/`)) return route.continue();
      external.push(route.request().url());
      return route.abort();
    });
    await page.route("**/*", (route) => {
      if (route.request().url() === `${base}/test-parent`) {
        return route.fulfill({
          contentType: "text/html",
          headers: {
            "Content-Security-Policy":
              "default-src 'none'; frame-src 'self'; script-src 'none'",
          },
          body: '<iframe id="preview" sandbox="allow-scripts" src="/composer-preview" width="1000" height="700"></iframe>',
        });
      }
      if (route.request().url().startsWith(`${base}/`)) return route.continue();
      external.push(route.request().url());
      return route.abort();
    });
    const loadedBundle = page.waitForResponse(
      (response) =>
        response.url().split("?")[0] ===
        `${base}/composer-preview/frame-client.js`,
    );
    await page.goto(`${base}/test-parent`);
    const browserBundle = await loadedBundle;
    assert.equal(
      sha256(await browserBundle.body()),
      sha256(localBundle),
      "Browser loaded a stale child bundle.",
    );
    assert.match(browserBundle.headers()["cache-control"] ?? "", /no-store/);
    await page.evaluate(() => {
      window.__previewResult = null;
      window.__previewReady = false;
      window.addEventListener("message", (event) => {
        if (
          event.source !== document.querySelector("#preview")?.contentWindow ||
          event.origin !== "null"
        )
          return;
        if (event.data?.type === "composer-preview-ready")
          window.__previewReady = true;
        if (event.data?.type === "composer-preview-result")
          window.__previewResult = event.data;
      });
      document
        .querySelector("#preview")
        ?.contentWindow?.postMessage({ type: "composer-preview-ping" }, "*");
    });
    await page.waitForFunction(() => window.__previewReady, {
      timeout: 10_000,
    });
    await page.evaluate(
      ({ bytes, format, maxSerializedMarkupBytes }) => {
        const array = new Uint8Array(bytes).buffer;
        document.querySelector("#preview")?.contentWindow?.postMessage(
          {
            type: "composer-preview-render",
            token: "qualification-token-1234567890",
            draftId: "11111111-1111-1111-1111-111111111111",
            revisionId: "22222222-2222-2222-2222-222222222222",
            format,
            bytes: array,
            maxSerializedMarkupBytes,
            zoom: 1,
            position: 0,
          },
          "*",
          [array],
        );
      },
      {
        bytes: Array.from(bytes),
        format,
        maxSerializedMarkupBytes:
          label === "docx-low-budget" ? 1024 : undefined,
      },
    );
    await page.waitForFunction(() => window.__previewResult, {
      timeout: 20_000,
    });
    const result = await page.evaluate(() => window.__previewResult);
    const frame = page.frame({ url: `${base}/composer-preview` });
    await page.waitForTimeout(400);
    const visibleText = frame
      ? (await frame.locator("#document").innerText()).slice(0, 180)
      : "";
    const childOrigin = frame
      ? await frame.evaluate(() => globalThis.origin)
      : "missing";
    const domNodes = frame
      ? await frame.evaluate(() => document.getElementsByTagName("*").length)
      : 0;
    const structure =
      format === "docx" && frame
        ? await frame.evaluate(() => {
            const section = document.querySelector("section.docx");
            return {
              sectionChildren: [...(section?.children ?? [])].map(
                (node) => node.tagName,
              ),
              articleChildren: [
                ...(section?.querySelector("article")?.children ?? []),
              ]
                .slice(0, 12)
                .map((node) => node.tagName),
            };
          })
        : undefined;
    let rawRendererPixelDifference;
    if (
      ["docx", "docx-long", "docx-media", "docx-structured"].includes(label) &&
      frame
    ) {
      const baseline = await browser.newPage({
        viewport: { width: 1000, height: 700 },
      });
      await baseline.goto(`${base}/composer-preview`);
      await baseline.evaluate(async (data) => {
        await window.docx.renderAsync(
          new Uint8Array(data).buffer,
          document.querySelector("#document"),
          undefined,
          {
            useBase64URL: true,
            renderAltChunks: false,
          },
        );
        await document.fonts.ready;
      }, Array.from(bytes));
      await frame.evaluate(() => document.fonts.ready);
      const windowedPng = await frame.locator("#viewport").screenshot();
      const baselinePng = await baseline.locator("#viewport").screenshot();
      rawRendererPixelDifference = await pixelDifference(
        baseline,
        windowedPng,
        baselinePng,
      );
      await baseline.close();
    }
    let nextSlideText = "";
    let thumbnailCount = 0;
    let slide2SettledPixelDifference;
    let slideBounds;
    if (format === "pptx" && frame) {
      assert.match(visibleText, /•/);
      assert.doesNotMatch(visibleText, /[\ue000-\uf8ff]/);
      slideBounds = await frame.locator("#document").boundingBox();
      const viewportWidth = await frame.evaluate(
        () => document.documentElement.clientWidth,
      );
      assert.ok(slideBounds && slideBounds.width <= viewportWidth + 1);
      thumbnailCount = await frame.locator("#slide-thumbnails button").count();
      assert.equal(thumbnailCount, 2);
      await frame.getByRole("button", { name: "Show slide 2" }).waitFor();
      await frame.getByRole("button", { name: "Next slide" }).click();
      nextSlideText = (await frame.locator("#document").innerText()).slice(
        0,
        180,
      );
      assert.match(nextSlideText, /Numbers Slide/);
      assert.doesNotMatch(nextSlideText, /Deck Title Slide/);
      const immediateSlide = await frame.locator("#document").screenshot();
      await page.waitForTimeout(1_200);
      nextSlideText = (await frame.locator("#document").innerText()).slice(
        0,
        180,
      );
      assert.match(nextSlideText, /Numbers Slide/);
      assert.match(nextSlideText, /Inside a group shape/);
      assert.doesNotMatch(nextSlideText, /[\ue000-\uf8ff]/);
      const settledBounds = await frame.locator("#document").boundingBox();
      assert.ok(settledBounds && settledBounds.width <= viewportWidth + 1);
      slide2SettledPixelDifference = await pixelDifference(
        frame,
        immediateSlide,
        await frame.locator("#document").screenshot(),
      );
      assert.ok(slide2SettledPixelDifference <= 0.001);
      await frame.evaluate(() => {
        const anchor = document.createElement("a");
        anchor.id = "passive-document-link";
        anchor.href = "https://example.invalid/preview-link";
        anchor.textContent = "Passive source link";
        document.querySelector("#document").append(anchor);
      });
      const sourceLink = frame.locator("#passive-document-link");
      await sourceLink.focus();
      await sourceLink.press("Enter");
      await sourceLink.click({ button: "middle", force: true });
      await sourceLink.click({ button: "right", force: true });
      await sourceLink.press("Shift+F10");
      await page.waitForTimeout(100);
      assert.equal(
        await frame.locator("#passive-document-link").getAttribute("href"),
        "https://example.invalid/preview-link",
      );
      assert.equal(
        await frame.evaluate(() => document.URL),
        `${base}/composer-preview`,
      );
      assert.deepEqual(popups, []);
    }
    let distantText = "";
    let distantDiagnostic;
    let restoredDiagnostic;
    let midUnitDiagnostic;
    let zoomUnitDiagnostics;
    let postGc;
    let remountPixelDifference;
    if (label === "docx-long" && frame) {
      const initialWindowedPng = await frame.locator("#viewport").screenshot();
      assert.ok(result.flowUnits > 3);
      assert.ok(result.mountedFlowUnits <= 3);
      await frame.evaluate(() => {
        const viewport = document.querySelector("#viewport");
        const first = document.querySelector(
          'article [data-preview-flow-unit="0"]',
        );
        viewport.scrollTop = Math.max(0, (first?.offsetHeight ?? 900) / 2);
      });
      await page.waitForTimeout(250);
      midUnitDiagnostic = await diagnostics(page, result);
      assert.equal(midUnitDiagnostic.contentPreserved, true);
      assert.ok(midUnitDiagnostic.mountedFlowUnits <= 3);
      zoomUnitDiagnostics = [];
      for (const zoom of [0.5, 1.25, 2]) {
        await page.evaluate(
          ({ identity, zoom }) => {
            document.querySelector("#preview")?.contentWindow?.postMessage(
              {
                type: "composer-preview-zoom",
                token: identity.token,
                draftId: identity.draftId,
                revisionId: identity.revisionId,
                zoom,
              },
              "*",
            );
          },
          { identity: result, zoom },
        );
        await frame.evaluate(() => {
          const viewport = document.querySelector("#viewport");
          const unit = document.querySelector('[data-preview-flow-unit="20"]');
          if (!unit)
            throw new Error("Expected distant flow placeholder is missing.");
          const view = viewport.getBoundingClientRect();
          const rect = unit.getBoundingClientRect();
          viewport.scrollTop +=
            rect.top + rect.height / 2 - view.top - view.height / 2;
        });
        await page.waitForTimeout(250);
        const mounted = await frame.evaluate(
          () => !document.querySelector('[data-preview-flow-unit="20"]'),
        );
        assert.equal(
          mounted,
          true,
          `flow unit 20 did not mount at zoom ${zoom}`,
        );
        const snapshot = await diagnostics(page, result);
        assert.equal(snapshot.contentPreserved, true);
        assert.ok(snapshot.mountedFlowUnits <= 3);
        zoomUnitDiagnostics.push({ zoom, ...snapshot });
        await frame.evaluate(() => {
          document.querySelector("#viewport").scrollTop = 0;
        });
        await page.waitForTimeout(150);
      }
      await frame.evaluate(() => {
        const viewport = document.querySelector("#viewport");
        viewport.scrollTop = viewport.scrollHeight - viewport.clientHeight;
      });
      await page.waitForTimeout(300);
      distantText = (await frame.locator("#document").innerText()).slice(-220);
      assert.match(distantText, /Paragraph 1999/);
      const mountedParagraphs = await frame
        .locator("#document article p")
        .count();
      assert.ok(mountedParagraphs < 200);
      distantDiagnostic = await diagnostics(page, result);
      assert.equal(distantDiagnostic.contentPreserved, true);
      assert.ok(distantDiagnostic.mountedFlowUnits <= 3);
      await frame.evaluate(() => {
        document.querySelector("#viewport").scrollTop = 0;
      });
      await page.waitForTimeout(250);
      assert.match(await frame.locator("#document").innerText(), /Paragraph 0/);
      await page.evaluate((identity) => {
        document.querySelector("#preview")?.contentWindow?.postMessage(
          {
            type: "composer-preview-zoom",
            token: identity.token,
            draftId: identity.draftId,
            revisionId: identity.revisionId,
            zoom: 1.25,
          },
          "*",
        );
      }, result);
      restoredDiagnostic = await diagnostics(page, result);
      assert.equal(restoredDiagnostic.contentPreserved, true);
      assert.ok(restoredDiagnostic.mountedFlowUnits <= 3);
      await page.evaluate((identity) => {
        document.querySelector("#preview")?.contentWindow?.postMessage(
          {
            type: "composer-preview-zoom",
            token: identity.token,
            draftId: identity.draftId,
            revisionId: identity.revisionId,
            zoom: 1,
          },
          "*",
        );
      }, result);
      await page.waitForTimeout(150);
      remountPixelDifference = await pixelDifference(
        frame,
        initialWindowedPng,
        await frame.locator("#viewport").screenshot(),
      );
      assert.ok(
        remountPixelDifference < 0.01,
        `DOCX flow remount changed ${remountPixelDifference} of visible pixels.`,
      );
      const cdp = await page.context().newCDPSession(page);
      await cdp.send("HeapProfiler.collectGarbage");
      await page.waitForTimeout(100);
      const counters = await cdp.send("Memory.getDOMCounters");
      postGc = {
        heap: await frame.evaluate(
          () => performance.memory?.usedJSHeapSize ?? null,
        ),
        dom: counters,
      };
      await cdp.detach();
    }
    if (label === "docx-media" && frame) {
      const cdp = await page.context().newCDPSession(page);
      await cdp.send("HeapProfiler.collectGarbage");
      await page.waitForTimeout(100);
      postGc = {
        heap: await frame.evaluate(
          () => performance.memory?.usedJSHeapSize ?? null,
        ),
        dom: await cdp.send("Memory.getDOMCounters"),
      };
      await cdp.detach();
    }
    let structuredDiagnostic;
    if (label === "docx-structured" && frame) {
      assert.equal(result.status, "ready", JSON.stringify(result));
      assert.ok(result.flowUnits > 3);
      const initial = await frame.evaluate(() => ({
        tables: document.querySelectorAll("#document table").length,
        anchors: document.querySelectorAll("#document a, #document [id]")
          .length,
        styled: document.querySelectorAll(
          "#document [class], #document [style]",
        ).length,
      }));
      await frame.evaluate(() => {
        const viewport = document.querySelector("#viewport");
        viewport.scrollTop = viewport.scrollHeight - viewport.clientHeight;
      });
      await page.waitForTimeout(300);
      assert.match(
        await frame.locator("#document").innerText(),
        /Fixture Document/,
      );
      const distant = await diagnostics(page, result);
      assert.equal(distant.contentPreserved, true);
      assert.ok(distant.mountedFlowUnits <= 3);
      const final = await frame.evaluate(() => ({
        tables: document.querySelectorAll("#document table").length,
        anchors: document.querySelectorAll("#document a, #document [id]")
          .length,
        styled: document.querySelectorAll(
          "#document [class], #document [style]",
        ).length,
      }));
      assert.ok(initial.tables > 0 && final.tables > 0);
      assert.ok(initial.anchors > 0 && final.anchors > 0);
      assert.ok(initial.styled > 0 && final.styled > 0);
      const baseline = await browser.newPage({
        viewport: { width: 1000, height: 700 },
      });
      await baseline.goto(`${base}/composer-preview`);
      await baseline.evaluate(async (data) => {
        await window.docx.renderAsync(
          new Uint8Array(data).buffer,
          document.querySelector("#document"),
          undefined,
          {
            useBase64URL: true,
            renderAltChunks: false,
          },
        );
        await document.fonts.ready;
        const viewport = document.querySelector("#viewport");
        viewport.scrollTop = viewport.scrollHeight - viewport.clientHeight;
      }, Array.from(bytes));
      await page.waitForTimeout(100);
      const bottomPixelDifference = await pixelDifference(
        baseline,
        await frame.locator("#viewport").screenshot(),
        await baseline.locator("#viewport").screenshot(),
      );
      await baseline.close();
      structuredDiagnostic = { initial, distant, final, bottomPixelDifference };
    }
    assert.equal(result.status, expected);
    assert.equal(childOrigin, "null");
    assert.deepEqual(external, []);
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({
        label,
        bytes: bytes.byteLength,
        result,
        visibleText,
        nextSlideText,
        slideBounds,
        slide2SettledPixelDifference,
        thumbnailCount,
        distantText,
        distantDiagnostic,
        restoredDiagnostic,
        midUnitDiagnostic,
        zoomUnitDiagnostics,
        postGc,
        structuredDiagnostic,
        childOrigin,
        domNodes,
        structure,
        rawRendererPixelDifference,
        remountPixelDifference,
        external,
        popups,
        errors,
      }),
    );
    await page.close();
  }
  const direct = await browser.newPage();
  await direct.goto(`${base}/composer-preview`);
  const directOrigin = await direct.evaluate(() => globalThis.origin);
  assert.equal(directOrigin, "null");
  const scriptRequests = [];
  await direct.route("https://example.invalid/**", (route) => {
    scriptRequests.push(route.request().url());
    return route.abort();
  });
  const dynamicScriptViolation = await direct.evaluate(
    () =>
      new Promise((resolve) => {
        const timeout = setTimeout(() => resolve(null), 1_000);
        window.addEventListener("securitypolicyviolation", (event) => {
          if (event.blockedURI !== "https://example.invalid/preview-probe.js")
            return;
          clearTimeout(timeout);
          resolve({
            blockedURI: event.blockedURI,
            directive: event.effectiveDirective,
          });
        });
        const script = document.createElement("script");
        script.src = "https://example.invalid/preview-probe.js";
        document.head.append(script);
      }),
  );
  assert.equal(
    dynamicScriptViolation?.blockedURI,
    "https://example.invalid/preview-probe.js",
  );
  assert.deepEqual(scriptRequests, []);
  console.log(
    JSON.stringify({
      directlyNavigatedChildOrigin: directOrigin,
      dynamicScriptViolation,
      scriptRequests,
    }),
  );
  await direct.close();
} finally {
  await browser.close();
}
