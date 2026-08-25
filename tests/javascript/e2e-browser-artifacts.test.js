import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { retainFailureArtifacts } from "../e2e/browser-helpers.mjs";

test("controlled browser failure retains bounded redacted diagnostics", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "md-converter-e2e-artifacts-"));
  let screenshotOptions;
  const page = {
    isClosed: () => false,
    locator: (selector) => ({ selector }),
    screenshot: async (options) => {
      screenshotOptions = options;
      await writeFile(options.path, "controlled screenshot");
    },
  };
  const trace = {
    started: true,
    context: {
      tracing: {
        stop: async ({ path: destination }) => writeFile(destination, "controlled trace"),
      },
    },
  };
  try {
    await retainFailureArtifacts({
      artifactRoot: root,
      profile: "standalone",
      step: "controlled artifact failure",
      pages: [{ name: "admin", page }],
      traces: [{ name: "admin", trace }],
      error: new Error("private failure detail"),
    });
    const [directoryName] = await readdir(root);
    const directory = path.join(root, directoryName);
    assert.deepEqual(
      (await readdir(directory)).sort(),
      ["admin-trace.zip", "admin.png", "failure.json"],
    );
    const diagnostic = await readFile(path.join(directory, "failure.json"), "utf8");
    assert.match(diagnostic, /controlled artifact failure/);
    assert.doesNotMatch(diagnostic, /private failure detail/);
    assert.equal(screenshotOptions.mask.length, 2);
    assert.equal(trace.started, false);
  } finally {
    await rm(root, { recursive: true });
  }
});
