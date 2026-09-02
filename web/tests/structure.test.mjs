import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, readdir, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
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
  for (const file of ["index.ts", "types.gen.ts", "valibot.gen.ts"]) {
    assert.equal(
      await readFile(`src/api/generated/${file}`, "utf8"),
      await readFile(`tests/fixtures/generated/${file}`, "utf8"),
    );
  }
});

test("tooling and runtime preserve the reviewed production configuration", async () => {
  const manifest = JSON.parse(await readFile("package.json", "utf8"));
  assert.match(manifest.scripts.check, /npm run typegen.*npm run typecheck/);
  const eslint = await readFile("eslint.config.mjs", "utf8");
  for (const ignored of ["build/**", "next-env.d.ts", "out/**"])
    assert.ok(eslint.includes(JSON.stringify(ignored)));
  const container = await readFile("Containerfile", "utf8");
  assert.match(container, /COPY --from=build[^\n]+next\.config\.ts/);
  const generator = await readFile("scripts/generate-openapi.mjs", "utf8");
  assert.doesNotMatch(generator, /process\.exit\s*\(/);
  assert.match(generator, /result\.error/);
  assert.match(generator, /finally\s*\{[\s\S]*rmSync\(/);
});

test("binding generation reports spawn failures and cleans its temporary tree", async () => {
  const temporary = await mkdtemp(join(tmpdir(), "markweave-generator-test-"));
  const result = spawnSync(
    process.execPath,
    ["scripts/generate-openapi.mjs", "--check"],
    {
      cwd: process.cwd(),
      encoding: "utf8",
      env: {
        ...process.env,
        MARKWEAVE_OPENAPI_TS_BIN: join(temporary, "missing-generator"),
        TMPDIR: temporary,
      },
    },
  );
  assert.equal(result.status, 1);
  assert.match(result.stderr, /ENOENT/);
  assert.deepEqual(await readdir(temporary), []);
  await rm(temporary, { recursive: true });
});

test("no application route duplicates the FastAPI API", async () => {
  await assert.rejects(stat("app/api"), { code: "ENOENT" });
  const nextConfig = await readFile("next.config.ts", "utf8");
  assert.doesNotMatch(nextConfig, /rewrites|redirects|headers\s*\(/);
});

test("shell links target only delivered application routes", async () => {
  const shell = await readFile("components/primitives.tsx", "utf8");
  const destinations = [...shell.matchAll(/<Link[\s\S]*?href="([^"]+)"/g)].map(
    (match) => match[1],
  );
  assert.deepEqual(destinations, ["/convert", "/templates", "/users"]);
  for (const destination of destinations)
    assert.ok((await stat(`app${destination}`)).isDirectory());
});

test("authentication keeps authority and secrets outside browser persistence", async () => {
  const controller = await readFile("src/auth/controller.ts", "utf8");
  const context = await readFile("src/auth/context.tsx", "utf8");
  for (const source of [controller, context]) {
    assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB/);
    assert.doesNotMatch(source, /setInterval|setTimeout/);
  }
  assert.doesNotMatch(controller, /session_token|sessionToken/);
  assert.doesNotMatch(context, /session_token|sessionToken/);
  await assert.rejects(stat("app/api"), { code: "ENOENT" });
  assert.deepEqual(
    (await readdir("app", { recursive: true })).filter((path) =>
      /(?:route\.(?:js|ts)|actions?\.(?:js|ts))$/.test(path),
    ),
    ["foundation-response/route.ts"],
  );
});
