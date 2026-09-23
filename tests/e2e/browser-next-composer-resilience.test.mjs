import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import https from "node:https";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";

import { chromium } from "playwright-core";

const baseURL = process.env.MARKWEAVE_E2E_BASE_URL || "http://localhost:3100";

async function request(page, method, path, upload, payload, extraHeaders = {}) {
  return page.evaluate(
    async ({ method, path, upload, payload, extraHeaders }) => {
      const csrf = document.cookie
        .split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith("__Host-md_converter_csrf="))
        ?.split("=", 2)[1];
      const headers = { ...extraHeaders };
      let body;
      if (method !== "GET" && csrf)
        headers["X-CSRF-Token"] = decodeURIComponent(csrf);
      if (upload) {
        body = new FormData();
        body.set(
          "source",
          new File([upload], "scanner-unavailable.md", {
            type: "text/markdown",
          }),
        );
      } else if (payload !== undefined) {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(payload);
      }
      const response = await fetch(path, {
        method,
        headers,
        body,
        cache: "no-store",
        credentials: "same-origin",
      });
      const text = await response.text();
      let json;
      try {
        json = JSON.parse(text);
      } catch {
        json = null;
      }
      return {
        status: response.status,
        retryAfter: response.headers.get("Retry-After"),
        text,
        json,
      };
    },
    { method, path, upload, payload, extraHeaders },
  );
}

async function slowCalls(ca) {
  return new Promise((resolve, reject) => {
    const call = https.get(
      {
        hostname: "e2e-llm",
        port: 8443,
        path: "/e2e/status",
        ca,
        timeout: 1_000,
      },
      (response) => {
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          body += chunk;
        });
        response.on("end", () => {
          if (response.statusCode !== 200)
            reject(new Error("Provider control endpoint failed"));
          else resolve(JSON.parse(body).slow_calls);
        });
      },
    );
    call.on("timeout", () =>
      call.destroy(new Error("Provider control endpoint timed out")),
    );
    call.on("error", reject);
  });
}

async function waitForSlowCall(ca, previous) {
  const deadline = Date.now() + 2_000;
  while (Date.now() < deadline) {
    if ((await slowCalls(ca)) > previous) return;
    await delay(20);
  }
  assert.fail("The model step did not reach the HTTPS provider");
}

test("Composer retains exact owner artifacts through scanner outage and restore", async () => {
  const profile = process.env.MARKWEAVE_E2E_PROFILE;
  const phase = process.env.MARKWEAVE_E2E_COMPOSER_PHASE;
  assert.ok(profile === "standalone" || profile === "distributed");
  assert.ok(
    ["scanner-unavailable", "restart", "restored-backup"].includes(phase),
  );
  const statePath = process.env.MARKWEAVE_E2E_COMPOSER_STATE;
  assert.ok(statePath?.startsWith("/browser-session/composer-"));
  const {
    draftId,
    revisionId,
    restoredId,
    connectionId,
    backupConnectionId,
    source,
  } = JSON.parse(await readFile(statePath, "utf8"));
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
    await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
    await page.getByRole("textbox", { name: "Username" }).fill("e2e-admin");
    await page.getByLabel("Password").fill("e2e-admin-password");
    await Promise.all([
      page.waitForURL("**/convert"),
      page.getByRole("button", { name: "Sign in" }).click(),
    ]);
    const draftPath = `/api/v1/composer/drafts/${draftId}`;
    const draft = await request(page, "GET", draftPath);
    assert.equal(draft.status, 200);
    assert.equal(draft.json.current_revision_id, restoredId);
    for (const id of [revisionId, restoredId]) {
      const revision = await request(
        page,
        "GET",
        `${draftPath}/revisions/${id}`,
      );
      assert.equal(revision.status, 200);
      const download = await request(
        page,
        "GET",
        `${draftPath}/revisions/${id}/artifacts/download`,
      );
      assert.equal(download.status, 200);
      assert.equal(download.text, source);
    }
    if (phase === "scanner-unavailable") {
      const rejected = await request(
        page,
        "POST",
        "/api/v1/composer/drafts",
        source,
      );
      assert.equal(rejected.status, 503);
      assert.equal(rejected.json.error.code, "UPLOAD_SCANNER_UNAVAILABLE");
      assert.equal((await request(page, "GET", draftPath)).status, 200);
    } else {
      const connection = await request(
        page,
        "GET",
        `/api/v1/composer/connections/${connectionId}`,
      );
      assert.equal(connection.status, 200);
      assert.equal(connection.json.credential_present, false);
      assert.equal(connection.json.selected_model, "composer-e2e-model");
      if (phase === "restored-backup") {
        const backupPath = `/api/v1/composer/connections/${backupConnectionId}`;
        const backup = await request(page, "GET", backupPath);
        assert.equal(backup.status, 200);
        assert.equal(backup.json.credential_present, true);
        assert.equal(backup.json.internal_ca_present, true);
        assert.equal(backup.json.selected_model, "composer-e2e-model");
        const tested = await request(
          page,
          "POST",
          `${backupPath}/test`,
          undefined,
          {},
        );
        assert.equal(tested.status, 200);
        assert.equal(tested.json.status, "ready");
        const selected = await request(
          page,
          "PATCH",
          backupPath,
          undefined,
          { selected_model: "composer-e2e-slow-model" },
          { "If-Match": backup.json.etag },
        );
        assert.equal(selected.status, 200);
        assert.equal(selected.json.selected_model, "composer-e2e-slow-model");
        const priorProposals = await request(
          page,
          "GET",
          `${draftPath}/proposals`,
        );
        const priorRevisions = await request(
          page,
          "GET",
          `${draftPath}/revisions`,
        );
        assert.equal(priorProposals.status, 200);
        assert.equal(priorRevisions.status, 200);
        const ca = await readFile("/run/composer-e2e-ca.crt", "utf8");
        const previousSlowCalls = await slowCalls(ca);
        const started = await request(
          page,
          "POST",
          `${draftPath}/model-steps`,
          undefined,
          {
            connection_id: backupConnectionId,
            approved_endpoint: backup.json.endpoint,
            approved_model: "composer-e2e-slow-model",
            content: "Propose one change to this draft.",
            max_output_tokens: 16,
          },
          {
            "If-Match": draft.json.etag,
            "Idempotency-Key": `cancel-${profile}-${Date.now()}`,
          },
        );
        assert.equal(started.status, 202);
        assert.equal(started.json.status, "running");
        assert.equal(started.json.proposal_id, null);
        const stepPath = `${draftPath}/model-steps/${started.json.id}`;
        await waitForSlowCall(ca, previousSlowCalls);
        const saturated = await request(
          page,
          "POST",
          `${draftPath}/model-steps`,
          undefined,
          {
            connection_id: backupConnectionId,
            approved_endpoint: backup.json.endpoint,
            approved_model: "composer-e2e-slow-model",
            content: "This request must wait for the occupied model slot.",
            max_output_tokens: 16,
          },
          {
            "If-Match": draft.json.etag,
            "Idempotency-Key": `saturated-${profile}-${Date.now()}`,
          },
        );
        assert.equal(saturated.status, 503);
        assert.equal(saturated.json.error.code, "COMPOSER_CAPACITY_EXHAUSTED");
        assert.equal(saturated.retryAfter, "2");
        const stillReady = await request(page, "GET", backupPath);
        assert.equal(stillReady.status, 200);
        assert.equal(stillReady.json.status, "ready");
        const cancelled = await request(page, "DELETE", stepPath);
        assert.equal(cancelled.status, 200);
        assert.equal(cancelled.json.status, "cancelled");
        assert.equal(cancelled.json.proposal_id, null);
        await delay(1_700);
        const terminal = await request(page, "GET", stepPath);
        assert.equal(terminal.status, 200);
        assert.equal(terminal.json.status, "cancelled");
        assert.equal(terminal.json.proposal_id, null);
        const afterProposals = await request(
          page,
          "GET",
          `${draftPath}/proposals`,
        );
        const afterRevisions = await request(
          page,
          "GET",
          `${draftPath}/revisions`,
        );
        assert.equal(afterProposals.status, 200);
        assert.equal(afterRevisions.status, 200);
        assert.deepEqual(
          afterProposals.json.proposals,
          priorProposals.json.proposals,
        );
        assert.deepEqual(
          afterRevisions.json.revisions,
          priorRevisions.json.revisions,
        );
        const resetModel = await request(
          page,
          "PATCH",
          backupPath,
          undefined,
          { selected_model: "composer-e2e-model" },
          { "If-Match": selected.json.etag },
        );
        assert.equal(resetModel.status, 200);
        assert.equal(resetModel.json.selected_model, "composer-e2e-model");
        const replacement = await request(
          page,
          "POST",
          `${draftPath}/model-steps`,
          undefined,
          {
            connection_id: backupConnectionId,
            approved_endpoint: backup.json.endpoint,
            approved_model: "composer-e2e-model",
            content: "Propose one replacement change to this draft.",
            max_output_tokens: 16,
          },
          {
            "If-Match": draft.json.etag,
            "Idempotency-Key": `replacement-${profile}-${Date.now()}`,
          },
        );
        assert.equal(replacement.status, 202);
        assert.equal(replacement.json.status, "running");
        const replacementPath = `${draftPath}/model-steps/${replacement.json.id}`;
        let completed;
        const completionDeadline = Date.now() + 5_000;
        while (Date.now() < completionDeadline) {
          completed = await request(page, "GET", replacementPath);
          assert.equal(completed.status, 200);
          if (completed.json.status !== "running") break;
          await delay(50);
        }
        assert.equal(completed?.json.status, "completed");
        assert.ok(completed.json.proposal_id);
        const replacementProposal = await request(
          page,
          "GET",
          `${draftPath}/proposals/${completed.json.proposal_id}`,
        );
        assert.equal(replacementProposal.status, 200);
        assert.equal(replacementProposal.json.state, "pending");
        const revisionsWithProposal = await request(
          page,
          "GET",
          `${draftPath}/revisions`,
        );
        assert.equal(revisionsWithProposal.status, 200);
        assert.deepEqual(
          revisionsWithProposal.json.revisions,
          priorRevisions.json.revisions,
        );
        const proposalsWithReplacement = await request(
          page,
          "GET",
          `${draftPath}/proposals`,
        );
        const latestDraft = await request(page, "GET", draftPath);
        assert.equal(proposalsWithReplacement.status, 200);
        assert.equal(latestDraft.status, 200);
        const beforeOccupiedCall = await slowCalls(ca);
        const occupyingTest = request(
          page,
          "POST",
          `${backupPath}/test`,
          undefined,
          { model: "composer-e2e-slow-model" },
        );
        await waitForSlowCall(ca, beforeOccupiedCall);
        const admittedWithoutEgress = await request(
          page,
          "POST",
          `${draftPath}/model-steps`,
          undefined,
          {
            connection_id: backupConnectionId,
            approved_endpoint: backup.json.endpoint,
            approved_model: "composer-e2e-model",
            content: "This admitted step must report transport capacity.",
            max_output_tokens: 16,
          },
          {
            "If-Match": latestDraft.json.etag,
            "Idempotency-Key": `egress-capacity-${profile}-${Date.now()}`,
          },
        );
        assert.equal(admittedWithoutEgress.status, 202);
        const failedPath = `${draftPath}/model-steps/${admittedWithoutEgress.json.id}`;
        let failed;
        const failureDeadline = Date.now() + 3_000;
        while (Date.now() < failureDeadline) {
          failed = await request(page, "GET", failedPath);
          assert.equal(failed.status, 200);
          if (failed.json.status !== "running") break;
          await delay(50);
        }
        assert.equal(failed?.json.status, "failed");
        assert.equal(failed.json.error_code, "capacity_exhausted");
        assert.equal(failed.json.proposal_id, null);
        const occupiedResult = await occupyingTest;
        assert.equal(occupiedResult.status, 200);
        assert.equal(occupiedResult.json.status, "ready");
        const afterEgressCapacity = await request(page, "GET", backupPath);
        assert.equal(afterEgressCapacity.status, 200);
        assert.equal(afterEgressCapacity.json.status, "ready");
        const afterFailedProposals = await request(
          page,
          "GET",
          `${draftPath}/proposals`,
        );
        const afterFailedRevisions = await request(
          page,
          "GET",
          `${draftPath}/revisions`,
        );
        assert.equal(afterFailedProposals.status, 200);
        assert.equal(afterFailedRevisions.status, 200);
        assert.deepEqual(
          afterFailedProposals.json.proposals,
          proposalsWithReplacement.json.proposals,
        );
        assert.deepEqual(
          afterFailedRevisions.json.revisions,
          revisionsWithProposal.json.revisions,
        );
      }
    }
    await context.close();
  } finally {
    await browser.close();
  }
});
