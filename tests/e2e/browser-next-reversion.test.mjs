import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright-core";

const baseURL = "http://localhost:3100";

async function login(page) {
  await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
  const retry = page.getByRole("button", { name: "Try again" });
  if (await retry.isVisible()) await retry.click();
  await page.getByRole("textbox", { name: "Username" }).fill("e2e-admin");
  await page.getByLabel("Password").fill("e2e-admin-password");
  await Promise.all([
    page.waitForURL("**/convert"),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);
}

test(
  "Next Revert workspace uses live capabilities and the real reverse lifecycle",
  { timeout: 600_000 },
  async () => {
    const phase = process.env.MARKWEAVE_E2E_REVERSE_PHASE ?? "primary";
    assert.ok(["primary", "admission"].includes(phase));
    const profile = process.env.MARKWEAVE_E2E_PROFILE;
    assert.ok(profile === "standalone" || profile === "distributed");
    const browser = await chromium.launch({
      executablePath:
        process.env.MARKWEAVE_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
      headless: true,
    });
    try {
      const context = await browser.newContext({
        baseURL,
        serviceWorkers: "block",
      });
      const page = await context.newPage();
      await login(page);
      await page.getByLabel(/Source file/).setInputFiles({
        buffer: Buffer.from("# Forward conversion source"),
        mimeType: "text/markdown",
        name: "forward-only.md",
      });
      await page.getByText(/Selected forward-only.md/).waitFor();
      const reverseSubmissions = [];
      const forwardSubmissions = [];
      page.on("request", (request) => {
        if (
          request.method() === "POST" &&
          new URL(request.url()).pathname === "/api/v1/conversions"
        )
          forwardSubmissions.push(request);
        if (
          request.method() === "POST" &&
          new URL(request.url()).pathname === "/api/v1/reversions"
        )
          reverseSubmissions.push(request);
      });
      const capabilitiesResponse = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/reversions/capabilities") &&
          response.request().method() === "GET",
      );
      await page.getByRole("link", { name: "2md, Experimental" }).click();
      await page.waitForURL("**/revert");
      const capabilityResponse = await capabilitiesResponse;
      assert.equal(capabilityResponse.status(), 200);
      const capabilities = await capabilityResponse.json();
      await page
        .getByRole("heading", { name: "New conversion to Markdown" })
        .waitFor();
      await page.getByText(/Extensions are a selection hint/).waitFor();
      await page.getByText(/OCR is not available/).waitFor();

      const input = page.getByLabel(/Source document/);
      assert.equal(await input.evaluate((element) => element.files.length), 0);
      assert.equal(await page.getByText(/Selected forward-only.md/).count(), 0);
      assert.deepEqual(
        (
          await page.getByRole("main").getByRole("alert").allTextContents()
        ).filter((text) => text.trim()),
        [],
      );
      assert.equal(reverseSubmissions.length, 0);
      const acceptedExtensions = (await input.getAttribute("accept")) ?? "";
      assert.deepEqual(acceptedExtensions.split(","), [
        ...new Set(
          capabilities.format_families.flatMap((family) => family.extensions),
        ),
      ]);
      await page
        .getByText(
          `Choose or drop exactly one supported document (maximum ${capabilities.maximum_upload_bytes} bytes).`,
          { exact: true },
        )
        .waitFor();
      await page.getByRole("button", { name: "Start conversion" }).click();
      await page
        .getByRole("alert")
        .getByText(/Choose a supported document/)
        .waitFor();
      assert.equal(reverseSubmissions.length, 0);

      if (phase === "admission") {
        assert.equal(capabilities.maximum_upload_bytes, 1024);
        await input.setInputFiles({
          buffer: Buffer.alloc(capabilities.maximum_upload_bytes + 1, 65),
          mimeType: "application/rtf",
          name: "oversized.rtf",
        });
        await page.getByRole("button", { name: "Start conversion" }).click();
        await page
          .getByRole("alert")
          .getByText(/upload limit/i)
          .waitFor();
        assert.equal(reverseSubmissions.length, 0);
      }

      await input.setInputFiles({
        buffer: Buffer.from("{\\rtf1\\ansi Browser reverse conversion}"),
        mimeType: "application/rtf",
        name: "browser-source.rtf",
      });
      const accepted = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/reversions") &&
          response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Start conversion" }).click();
      const acceptedResponse = await accepted;
      assert.equal(acceptedResponse.status(), 202);
      assert.ok(acceptedResponse.request().headers()["idempotency-key"]);
      if (phase === "admission") {
        await page.getByText("Your document is queued.").waitFor();

        const cancelled = page.waitForResponse(
          (response) =>
            /\/api\/v1\/reversions\/[0-9a-f-]+$/.test(response.url()) &&
            response.request().method() === "DELETE",
        );
        await page.getByRole("button", { name: "Cancel conversion" }).click();
        assert.equal((await cancelled).status(), 200);
        await page.getByText(/conversion was cancelled/i).waitFor();
        assert.equal(
          await page.getByRole("button", { name: "Download result" }).count(),
          0,
        );
      } else {
        const button = page.getByRole("button", { name: "Download result" });
        await button.waitFor({ timeout: 60_000 });
        const downloaded = page.waitForEvent("download");
        await button.click();
        const download = await downloaded;
        assert.match(download.suggestedFilename(), /\.md$/);
        const path = await download.path();
        assert.ok(path);
        assert.match(
          await readFile(path, "utf8"),
          /Browser reverse conversion/,
        );
      }
      await page.getByRole("link", { name: "2docx", exact: true }).click();
      await page
        .getByRole("heading", { name: "New conversion", exact: true })
        .waitFor();
      assert.equal(
        await page
          .getByLabel(/Source file/)
          .evaluate((element) =>
            Array.from(element.files).some(
              (file) => file.name === "browser-source.rtf",
            ),
          ),
        false,
      );
      assert.equal(
        await page.getByText(/Selected browser-source.rtf/).count(),
        0,
      );
      assert.equal(forwardSubmissions.length, 0);
      assert.equal(reverseSubmissions.length, 1);
      assert.deepEqual(
        (
          await page.getByRole("main").getByRole("alert").allTextContents()
        ).filter((text) => text.trim()),
        [],
      );
      if (phase === "primary") {
        for (const fault of ["unavailable", "future-schema"]) {
          await page.route(
            "**/api/v1/reversions/capabilities",
            async (route) => {
              if (fault === "unavailable") {
                await route.fulfill({
                  status: 503,
                  contentType: "application/json",
                  body: "{}",
                });
              } else {
                const response = await route.fetch();
                assert.equal(response.status(), 200);
                const body = await response.json();
                body.schema_version = 2;
                await route.fulfill({ response, json: body });
              }
            },
          );
          await page.goto(`${baseURL}/revert`);
          await page
            .getByRole("heading", { name: "Revert is unavailable" })
            .waitFor();
          assert.equal(
            await page
              .getByRole("button", { name: "Start conversion" })
              .count(),
            0,
          );
          assert.equal(await page.getByLabel(/Source document/).count(), 0);
          assert.equal(reverseSubmissions.length, 1);
          await page.unroute("**/api/v1/reversions/capabilities");
        }
      }
      await context.close();
    } finally {
      await browser.close();
    }
  },
);
