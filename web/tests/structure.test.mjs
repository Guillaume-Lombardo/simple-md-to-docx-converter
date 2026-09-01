import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import { test } from "node:test";

test("Next.js 16 uses only the reviewed proxy interception hook", async () => {
  const source = await readFile("proxy.ts", "utf8");
  assert.match(source, /export function proxy\s*\(/);
  assert.doesNotMatch(source, /export function middleware\s*\(/);
  await assert.rejects(stat("middleware.ts"), { code: "ENOENT" });
});

test("direct dependencies and package manager use exact reviewed versions", async () => {
  const manifest = JSON.parse(await readFile("package.json", "utf8"));
  assert.equal(manifest.packageManager, "npm@11.17.0");
  assert.equal(manifest.engines.node, "24.19.0");
  assert.equal(manifest.dependencies.next, "16.3.4");
  assert.equal(manifest.devDependencies.typescript, "6.0.3");
  assert.equal(manifest.devDependencies.tailwindcss, "4.3.3");
  for (const dependencies of [
    manifest.dependencies,
    manifest.devDependencies,
  ]) {
    for (const version of Object.values(dependencies))
      assert.match(version, /^\d+\.\d+\.\d+$/);
  }
});

test("generated production bindings and fixture are byte-identical", async () => {
  for (const file of ["index.ts", "types.gen.ts"]) {
    assert.equal(
      await readFile(`src/api/generated/${file}`, "utf8"),
      await readFile(`tests/fixtures/generated/${file}`, "utf8"),
    );
  }
});

test("no application route duplicates the FastAPI API", async () => {
  await assert.rejects(stat("app/api"), { code: "ENOENT" });
  const nextConfig = await readFile("next.config.ts", "utf8");
  assert.doesNotMatch(nextConfig, /rewrites|redirects|headers\s*\(/);
});
