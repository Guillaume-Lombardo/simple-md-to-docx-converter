import assert from "node:assert/strict";
import test from "node:test";
import { chromium } from "playwright-core";

const baseURL = "http://localhost:3100";

async function login(page, username, password) {
  await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
  const retry = page.getByRole("button", { name: "Try again" });
  if (await retry.isVisible()) await retry.click();
  await page.getByRole("textbox", { name: "Username" }).fill(username);
  await page.getByLabel("Password").fill(password);
  await Promise.all([
    page.waitForURL("**/convert"),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);
  await page.getByRole("heading", { name: "New conversion" }).waitFor();
}

async function api(page, method, path, body) {
  return page.evaluate(
    async ({ method, path, body }) => {
      const csrf = document.cookie
        .split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith("__Host-md_converter_csrf="))
        ?.split("=", 2)[1];
      const response = await fetch(path, {
        method,
        cache: "no-store",
        credentials: "same-origin",
        headers: {
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          ...(csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {}),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      return {
        status: response.status,
        body: await response.json().catch(() => null),
      };
    },
    { method, path, body },
  );
}

test(
  "Next conversion workspace preserves real final-image workflow and authority",
  { timeout: 300_000 },
  async () => {
    const profile = process.env.MARKWEAVE_E2E_PROFILE;
    assert.ok(profile === "standalone" || profile === "distributed");
    const suffix = `${profile}-${Date.now()}`;
    const alice = {
      username: `conversion-alice-${suffix}`,
      password: `Alice-${suffix}-password`,
    };
    const bob = {
      username: `conversion-bob-${suffix}`,
      password: `Bob-${suffix}-password`,
    };
    const browser = await chromium.launch({
      executablePath:
        process.env.MARKWEAVE_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
      headless: true,
    });
    try {
      const adminContext = await browser.newContext({
        baseURL,
        serviceWorkers: "block",
      });
      const aliceContext = await browser.newContext({
        baseURL,
        serviceWorkers: "block",
      });
      const bobContext = await browser.newContext({
        baseURL,
        serviceWorkers: "block",
      });
      const adminPage = await adminContext.newPage();
      const alicePage = await aliceContext.newPage();
      const bobPage = await bobContext.newPage();
      await login(adminPage, "e2e-admin", "e2e-admin-password");
      for (const identity of [alice, bob]) {
        const created = await api(adminPage, "POST", "/api/v1/admin/users", {
          username: identity.username,
          password: identity.password,
          password_change_required: false,
        });
        assert.equal(created.status, 201);
      }

      await login(alicePage, alice.username, alice.password);
      await alicePage
        .getByText("System fallback template", { exact: true })
        .waitFor();
      await alicePage
        .getByRole("button", { name: "Use Pandoc default" })
        .click();
      assert.equal(
        await alicePage.getByText("Pandoc default", { exact: true }).count(),
        1,
      );

      await alicePage.getByRole("button", { name: "Start conversion" }).click();
      await alicePage
        .getByRole("alert")
        .getByText(/Choose a Markdown or ZIP file/)
        .waitFor();
      await alicePage.getByLabel(/Source file/).setInputFiles({
        name: "browser-source.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("# Next final-image conversion\n"),
      });

      const cdp = await alicePage.context().newCDPSession(alicePage);
      let failFirstAccepted = true;
      await cdp.send("Fetch.enable", {
        patterns: [
          { urlPattern: "*/api/v1/conversions", requestStage: "Response" },
        ],
      });
      cdp.on("Fetch.requestPaused", (event) => {
        const fail = failFirstAccepted && event.responseStatusCode === 202;
        if (fail) failFirstAccepted = false;
        void cdp
          .send(fail ? "Fetch.failRequest" : "Fetch.continueRequest", {
            requestId: event.requestId,
            ...(fail ? { errorReason: "Aborted" } : {}),
          })
          .catch(() => {});
      });

      await alicePage.getByRole("button", { name: "Start conversion" }).click();
      await alicePage
        .getByRole("alert")
        .getByText(/same request key/)
        .waitFor();
      await alicePage.getByRole("button", { name: "Start conversion" }).click();
      await alicePage
        .getByText("Your conversion is ready to download.")
        .waitFor({
          timeout: 180_000,
        });
      const listing = await api(
        alicePage,
        "GET",
        "/api/v1/conversions?limit=10",
      );
      assert.equal(listing.status, 200);
      assert.equal(listing.body.items.length >= 1, true);
      const completed = listing.body.items[0];
      assert.equal(completed.state, "succeeded");
      assert.equal(completed.template_mode, "pandoc-default");
      assert.equal(typeof completed.expires_at, "string");

      const downloadResponse = await alicePage.evaluate(async (jobId) => {
        const response = await fetch(`/api/v1/conversions/${jobId}/result`, {
          cache: "no-store",
          credentials: "same-origin",
        });
        return {
          status: response.status,
          disposition: response.headers.get("Content-Disposition"),
          cache: response.headers.get("Cache-Control"),
          nosniff: response.headers.get("X-Content-Type-Options"),
          size: (await response.arrayBuffer()).byteLength,
        };
      }, completed.id);
      assert.equal(downloadResponse.status, 200);
      assert.equal(
        downloadResponse.disposition,
        'attachment; filename="browser-source.docx"',
      );
      assert.equal(downloadResponse.cache, "private, no-store");
      assert.equal(downloadResponse.nosniff, "nosniff");
      assert.ok(downloadResponse.size > 0);
      const [browserDownload] = await Promise.all([
        alicePage.waitForEvent("download"),
        alicePage.getByRole("button", { name: "Download result" }).click(),
      ]);
      assert.equal(browserDownload.suggestedFilename(), "browser-source.docx");

      await alicePage.reload({ waitUntil: "networkidle" });
      await alicePage
        .getByRole("button", {
          name: new RegExp(`Conversion ${completed.id.slice(0, 8)}`),
        })
        .click();
      await alicePage
        .getByText("Your conversion is ready to download.")
        .waitFor();

      await login(bobPage, bob.username, bob.password);
      assert.equal(
        (await api(bobPage, "GET", `/api/v1/conversions/${completed.id}`))
          .status,
        404,
      );

      await alicePage.getByLabel(/Source file/).setInputFiles({
        name: "cancel-source.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("# Cancel this conversion\n"),
      });
      await alicePage.getByRole("radio", { name: "PDF" }).click();
      await alicePage.getByRole("button", { name: "Start conversion" }).click();
      const cancel = alicePage.getByRole("button", {
        name: "Cancel conversion",
      });
      await cancel.waitFor({ timeout: 30_000 });
      await cancel.click();
      await alicePage
        .getByText(/Cancellation requested|conversion was cancelled/)
        .waitFor();
      await alicePage
        .getByText("The conversion was cancelled.")
        .waitFor({ timeout: 60_000 });
      assert.equal(
        await alicePage
          .getByRole("button", { name: "Download result" })
          .count(),
        0,
      );

      const untilExpiration = Math.max(
        0,
        Date.parse(completed.expires_at) - Date.now() + 1_500,
      );
      assert.ok(Number.isFinite(untilExpiration));
      await new Promise((resolve) => setTimeout(resolve, untilExpiration));
      await alicePage.reload({ waitUntil: "networkidle" });
      await alicePage
        .getByRole("button", {
          name: new RegExp(`Conversion ${completed.id.slice(0, 8)}`),
        })
        .click();
      await alicePage
        .getByText(
          "This conversion has expired and its files are no longer available.",
        )
        .waitFor({ timeout: 15_000 });
      assert.equal(
        await alicePage
          .getByRole("button", { name: "Download result" })
          .count(),
        0,
      );

      for (const context of [adminContext, aliceContext, bobContext])
        await context.close();
    } finally {
      await browser.close();
    }
  },
);
