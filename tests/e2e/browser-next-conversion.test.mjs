import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import test from "node:test";
import { chromium } from "playwright-core";

const baseURL = "http://localhost:3100";

function oversizedPdfMarkdown() {
  const paragraph = "Bounded final-image PDF validation text. ".repeat(8);
  return `# PDF output limit\n\n${Array.from(
    { length: 1_000 },
    (_, index) => `${index}. ${paragraph}`,
  ).join("\n\n")}\n`;
}

async function preparePdfSubmission(page, name, source) {
  await page.goto(`${baseURL}/convert`, { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: "New conversion" }).waitFor();
  await page.getByLabel(/Source file/).setInputFiles({
    name,
    mimeType: "text/markdown",
    buffer: Buffer.from(source),
  });
  await page.getByRole("radio", { name: "PDF", exact: true }).click();
}

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
  { timeout: 600_000 },
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
      const dropped = await alicePage.evaluate(() => {
        const input = document.querySelector('input[name="source"]');
        const dropZone = input?.closest("label");
        if (!(input instanceof HTMLInputElement))
          throw new Error("source input missing");
        if (!(dropZone instanceof HTMLLabelElement))
          throw new Error("source drop zone missing");
        const transfer = new DataTransfer();
        transfer.items.add(
          new File(["# Next final-image conversion\n"], "browser-source.md", {
            type: "text/markdown",
          }),
        );
        dropZone.dispatchEvent(
          new DragEvent("drop", {
            bubbles: true,
            cancelable: true,
            dataTransfer: transfer,
          }),
        );
        return input.files?.length ?? -1;
      });
      assert.equal(dropped, 0);

      let failFirstAccepted = true;
      let failedAcceptedResponses = 0;
      const interceptAccepted = async (route) => {
        if (failFirstAccepted && route.request().method() === "POST") {
          failFirstAccepted = false;
          const upstream = await route.fetch();
          assert.equal(upstream.status(), 202);
          failedAcceptedResponses += 1;
          await route.abort("failed");
          return;
        }
        await route.continue();
      };
      await alicePage.route("**/api/v1/conversions", interceptAccepted);

      await alicePage.getByRole("button", { name: "Start conversion" }).click();
      await alicePage
        .getByRole("alert")
        .getByText(/same request key/)
        .waitFor();
      assert.equal(failedAcceptedResponses, 1);
      await alicePage.unroute("**/api/v1/conversions", interceptAccepted);
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
      await Promise.all([adminContext.close(), bobContext.close()]);

      const heavySource = oversizedPdfMarkdown();
      const failurePage = await aliceContext.newPage();
      const blockingPage = await aliceContext.newPage();
      await Promise.all([
        preparePdfSubmission(failurePage, "pdf-limit.md", heavySource),
        preparePdfSubmission(blockingPage, "pdf-blocker.md", heavySource),
      ]);
      const failureAccepted = failurePage.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/conversions") &&
          response.request().method() === "POST",
      );
      const blockingAccepted = blockingPage.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/conversions") &&
          response.request().method() === "POST",
      );
      await Promise.all([
        failurePage
          .getByRole("button", { name: "Start conversion" })
          .click(),
        blockingPage
          .getByRole("button", { name: "Start conversion" })
          .click(),
      ]);
      const [failureResponse, blockingResponse] = await Promise.all([
        failureAccepted,
        blockingAccepted,
      ]);
      assert.equal(failureResponse.status(), 202);
      assert.equal(blockingResponse.status(), 202);
      const failureJob = await failureResponse.json();
      await Promise.all([
        failurePage
          .getByRole("button", { name: "Cancel conversion" })
          .waitFor({ timeout: 30_000 }),
        blockingPage
          .getByRole("button", { name: "Cancel conversion" })
          .waitFor({ timeout: 30_000 }),
      ]);

      await alicePage.getByLabel(/Source file/).setInputFiles({
        name: "cancel-source.md",
        mimeType: "text/markdown",
        buffer: Buffer.from(`${heavySource}\n${heavySource}`),
      });
      await alicePage
        .getByRole("radio", { name: "DOCX", exact: true })
        .click();
      await alicePage.getByRole("button", { name: "Start conversion" }).click();
      const cancel = alicePage.getByRole("button", {
        name: "Cancel conversion",
      });
      await cancel.waitFor({ timeout: 30_000 });
      await cancel.click();
      await alicePage
        .getByText("The conversion was cancelled.")
        .waitFor({ timeout: 60_000 });
      assert.equal(
        await alicePage
          .getByRole("button", { name: "Download result" })
          .count(),
        0,
      );

      const quotaPage = await aliceContext.newPage();
      const racingPage = await aliceContext.newPage();
      await Promise.all([
        preparePdfSubmission(quotaPage, "quota-primary.md", heavySource),
        preparePdfSubmission(racingPage, "quota-racing.md", heavySource),
      ]);
      const acceptedJobIds = [];
      const quotaPageJobIds = [];
      for (let index = 0; index < 5; index += 1) {
        if (index > 0) {
          await preparePdfSubmission(
            quotaPage,
            `quota-primary-${index}.md`,
            heavySource,
          );
        }
        const acceptedResponse = quotaPage.waitForResponse(
          (response) =>
            response.url().endsWith("/api/v1/conversions") &&
            response.request().method() === "POST",
        );
        await quotaPage
          .getByRole("button", { name: "Start conversion" })
          .click();
        const response = await acceptedResponse;
        assert.equal(response.status(), 202);
        const jobId = (await response.json()).id;
        acceptedJobIds.push(jobId);
        quotaPageJobIds.push(jobId);
      }
      await preparePdfSubmission(
        quotaPage,
        "quota-primary-race.md",
        heavySource,
      );
      const quotaResponse = quotaPage.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/conversions") &&
          response.request().method() === "POST",
      );
      const racingResponse = racingPage.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/conversions") &&
          response.request().method() === "POST",
      );
      await Promise.all([
        quotaPage.getByRole("button", { name: "Start conversion" }).click(),
        racingPage.getByRole("button", { name: "Start conversion" }).click(),
      ]);
      const raced = await Promise.all([quotaResponse, racingResponse]);
      assert.deepEqual(
        raced.map((response) => response.status()).sort(),
        [202, 429],
      );
      const admitted = raced.find((response) => response.status() === 202);
      const rejectedPage = raced[0].status() === 429 ? quotaPage : racingPage;
      assert.ok(admitted);
      const admittedJobId = (await admitted.json()).id;
      acceptedJobIds.push(admittedJobId);
      if (raced[0].status() === 202) {
        quotaPageJobIds.push(admittedJobId);
      }
      await rejectedPage
        .getByRole("alert")
        .getByText("The active conversion quota is exhausted.")
        .waitFor();

      let serializedJobId;
      for (let attempt = 0; attempt < 120; attempt += 1) {
        const jobs = await api(quotaPage, "GET", "/api/v1/conversions?limit=20");
        assert.equal(jobs.status, 200);
        const failureState = jobs.body.items.find(
          (candidate) => candidate.id === failureJob.id,
        );
        const primaryStates = jobs.body.items.filter((candidate) =>
          quotaPageJobIds.includes(candidate.id),
        );
        if (profile === "distributed") {
          serializedJobId = primaryStates.find(
            (candidate) => candidate.state === "running",
          )?.id;
          if (failureState?.state === "running" && serializedJobId) break;
        } else {
          serializedJobId = primaryStates.find(
            (candidate) => candidate.state === "queued",
          )?.id;
          if (failureState?.state === "running" && serializedJobId) break;
        }
        await quotaPage.waitForTimeout(250);
      }
      assert.ok(serializedJobId);
      await failurePage
        .getByText(/Processing|Validating|Creating the PDF/)
        .waitFor();
      await quotaPage
        .getByRole("button", {
          name: new RegExp(`Conversion ${serializedJobId.slice(0, 8)}`),
        })
        .click();
      await quotaPage
        .getByText(
          profile === "distributed"
            ? /Processing|Validating|Creating the PDF/
            : "Your conversion is queued.",
        )
        .waitFor();
      for (const jobId of acceptedJobIds) {
        const cancelled = await api(
          quotaPage,
          "DELETE",
          `/api/v1/conversions/${jobId}`,
        );
        assert.equal(cancelled.status, 200);
      }
      await failurePage
        .getByText(/PDF.*configured limits/)
        .waitFor({ timeout: 180_000 });
      assert.equal(
        await failurePage
          .getByRole("button", { name: "Download result" })
          .count(),
        0,
      );
      await Promise.all([
        failurePage.close(),
        blockingPage.close(),
        quotaPage.close(),
        racingPage.close(),
      ]);

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

      await alicePage.getByLabel(/Source file/).setInputFiles({
        name: "restart-recovery.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("# Recover this conversion after restart\n"),
      });
      await alicePage.getByRole("radio", { name: "DOCX", exact: true }).click();
      const recoveryAccepted = alicePage.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/conversions") &&
          response.request().method() === "POST" &&
          response.status() === 202,
      );
      await alicePage.getByRole("button", { name: "Start conversion" }).click();
      const recoveryJob = await (await recoveryAccepted).json();
      await alicePage
        .getByText("Your conversion is ready to download.")
        .waitFor({ timeout: 180_000 });
      const statePath = process.env.MARKWEAVE_E2E_CONVERSION_STATE;
      assert.ok(statePath);
      await writeFile(
        statePath,
        `${JSON.stringify({ ...alice, job_id: recoveryJob.id })}\n`,
        { encoding: "utf8", mode: 0o600 },
      );

      await aliceContext.close();
    } finally {
      await browser.close();
    }
  },
);
