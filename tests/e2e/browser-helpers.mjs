import assert from "node:assert/strict";
import { access, chmod, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const REQUIRED_ENVIRONMENT = [
  "MD_CONVERTER_E2E_BASE_URL",
  "MD_CONVERTER_E2E_PROFILE",
  "MD_CONVERTER_E2E_ARTIFACT_DIR",
  "MD_CONVERTER_E2E_SOURCE_FIXTURE",
  "MD_CONVERTER_E2E_TEMPLATE_FIXTURE",
  "MD_CONVERTER_E2E_ADMIN_USERNAME",
  "MD_CONVERTER_E2E_ADMIN_PASSWORD",
];

export async function configuration(environment = process.env) {
  for (const name of REQUIRED_ENVIRONMENT) assert.ok(environment[name], `${name} is required`);
  const parsed = new URL(environment.MD_CONVERTER_E2E_BASE_URL);
  assert.ok(["http:", "https:"].includes(parsed.protocol), "base URL must use HTTP(S)");
  assert.equal(parsed.username, "", "base URL must not contain credentials");
  assert.equal(parsed.password, "", "base URL must not contain credentials");
  assert.equal(parsed.pathname, "/", "base URL must not contain a path");
  assert.equal(parsed.search, "", "base URL must not contain a query");
  assert.equal(parsed.hash, "", "base URL must not contain a fragment");
  const profile = environment.MD_CONVERTER_E2E_PROFILE;
  assert.ok(["standalone", "distributed"].includes(profile), "profile is invalid");
  const timeoutSeconds = Number(environment.MD_CONVERTER_E2E_TIMEOUT_SECONDS || "360");
  assert.ok(Number.isFinite(timeoutSeconds) && timeoutSeconds > 0, "timeout is invalid");
  const sourceFixture = path.resolve(environment.MD_CONVERTER_E2E_SOURCE_FIXTURE);
  const templateFixture = path.resolve(environment.MD_CONVERTER_E2E_TEMPLATE_FIXTURE);
  assert.match(sourceFixture, /\.(md|zip)$/i, "source fixture must be Markdown or ZIP");
  assert.match(templateFixture, /\.docx$/i, "template fixture must be DOCX");
  await Promise.all([access(sourceFixture), access(templateFixture)]);
  const artifactRoot = path.resolve(environment.MD_CONVERTER_E2E_ARTIFACT_DIR);
  assert.notEqual(artifactRoot, path.parse(artifactRoot).root, "artifact directory is too broad");
  return {
    baseUrl: parsed.href.replace(/\/$/, ""),
    profile,
    artifactRoot,
    sourceFixture,
    templateFixture,
    adminUsername: environment.MD_CONVERTER_E2E_ADMIN_USERNAME,
    adminPassword: environment.MD_CONVERTER_E2E_ADMIN_PASSWORD,
    chromiumExecutable: environment.MD_CONVERTER_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
    timeoutMilliseconds: timeoutSeconds * 1_000,
  };
}

export async function login(page, baseUrl, username, password) {
  await page.goto("/login", { waitUntil: "networkidle" });
  await page.locator('input[name="username"]').fill(username);
  await page.locator('input[name="password"]').fill(password);
  await page.setExtraHTTPHeaders({ Origin: baseUrl });
  await Promise.all([
    page.waitForURL("**/convert"),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);
  await page.setExtraHTTPHeaders({});
  assert.equal(page.url(), `${baseUrl}/convert`, "login did not reach the conversion page");
}

export async function waitForText(page, selector, expected, timeout) {
  await page.locator(selector).filter({ hasText: expected }).waitFor({ timeout });
}

export async function sessionRequest(page, pathName, options = {}) {
  return page.evaluate(
    async ({ pathName, options }) => {
      const csrf = document.cookie
        .split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith("__Host-md_converter_csrf="))
        ?.split("=", 2)[1];
      const headers = { ...(options.headers || {}) };
      if (options.mutate && csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf);
      const response = await fetch(pathName, {
        method: options.method || "GET",
        headers,
      });
      return {
        status: response.status,
        body: options.json ? await response.json() : null,
      };
    },
    { pathName, options },
  );
}

export async function startTrace(context) {
  await context.tracing.start({ screenshots: true, snapshots: true, sources: false });
  return { context, started: true };
}

export async function discardTrace(trace) {
  if (!trace.started) return;
  await trace.context.tracing.stop();
  trace.started = false;
}

async function safeScreenshot(page, destination) {
  if (!page || page.isClosed()) return;
  await page.screenshot({
    path: destination,
    fullPage: true,
    animations: "disabled",
    mask: [page.locator('input[type="password"]'), page.locator('input[type="file"]')],
  });
}

export async function retainFailureArtifacts({
  artifactRoot,
  profile,
  step,
  pages,
  traces,
  error,
}) {
  const directory = path.join(artifactRoot, `browser-${profile}-${Date.now()}-${process.pid}`);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  await Promise.allSettled(
    pages.map(({ name, page }) => safeScreenshot(page, path.join(directory, `${name}.png`))),
  );
  await Promise.allSettled(
    traces.map(async ({ name, trace }) => {
      if (!trace.started) return;
      await trace.context.tracing.stop({ path: path.join(directory, `${name}-trace.zip`) });
      trace.started = false;
    }),
  );
  const diagnostic = path.join(directory, "failure.json");
  await writeFile(
    diagnostic,
    `${JSON.stringify(
      {
        schema_version: 1,
        scenario: "final-image-browser-journey",
        profile,
        step,
        error_name: error instanceof Error ? error.name : "UnknownError",
      },
      null,
      2,
    )}\n`,
    { encoding: "utf8", mode: 0o600 },
  );
  await chmod(diagnostic, 0o600);
}

export function assertDownloadedResult(output, headers, content) {
  assert.ok(content.length > 0, `${output} download is empty`);
  const disposition = headers["content-disposition"] || "";
  const expectedExtension = output === "both" ? "zip" : output;
  assert.match(
    disposition,
    new RegExp(`^attachment; filename="conversion-[0-9a-f-]+\\.${expectedExtension}"$`),
    `${output} download disposition is invalid`,
  );
  assert.equal(headers["cache-control"], "private, no-store");
  assert.equal(headers["x-content-type-options"], "nosniff");
  if (output === "pdf") assert.equal(content.subarray(0, 5).toString(), "%PDF-");
  else assert.deepEqual([...content.subarray(0, 4)], [0x50, 0x4b, 0x03, 0x04]);
}
