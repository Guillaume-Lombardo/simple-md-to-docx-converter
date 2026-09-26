import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { chromium } from "playwright-core";

const baseURL = process.env.MARKWEAVE_E2E_BASE_URL || "http://localhost:3100";
const profile = process.env.MARKWEAVE_E2E_PROFILE || "standalone";
const typedDocx = "/spikes/anydoc/corpus/docx/typed.docx";
const typedSchema = "/spikes/anydoc/corpus/docx/typed-schema.json";
const malwareMarker =
  "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*";
const providerEndpoint = "https://e2e-llm:8443/v1";
const providerModel = "composer-e2e-model";
const providerSecret = "composer-e2e-write-only-secret";

async function login(page, username, password) {
  await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
  await page.getByRole("textbox", { name: "Username" }).fill(username);
  await page.getByLabel("Password").fill(password);
  await Promise.all([
    page.waitForURL("**/convert"),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);
}

async function api(page, method, path, options = {}) {
  return page.evaluate(
    async ({ method, path, options }) => {
      const csrf = document.cookie
        .split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith("__Host-md_converter_csrf="))
        ?.split("=", 2)[1];
      const headers = { ...options.headers };
      let body;
      if (options.source) {
        const form = new FormData();
        form.set(
          "source",
          new File([options.source.content], options.source.filename, {
            type: "text/markdown",
          }),
        );
        form.set("title", options.source.title);
        body = form;
      } else if (options.typed) {
        const form = new FormData();
        form.set("name", options.typed.name);
        form.set("schema", options.typed.schema);
        form.set(
          "file",
          new File([new Uint8Array(options.typed.bytes)], "typed.docx", {
            type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          }),
        );
        body = form;
      } else if (options.json !== undefined) {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(options.json);
      }
      if (method !== "GET" && csrf) {
        headers["X-CSRF-Token"] = decodeURIComponent(csrf);
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
      return { status: response.status, text, json };
    },
    { method, path, options },
  );
}

function expectStatus(result, expected) {
  assert.equal(result.status, expected, result.text);
  return result.json;
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function createDraftWithSource(page, suffix) {
  const draft = expectStatus(
    await api(page, "POST", "/api/v1/composer/drafts", {
      source: {
        content: `# Typed Composer ${suffix}\n\nReviewed source.\n`,
        filename: "typed-source.md",
        title: `Typed Composer ${suffix}`,
      },
    }),
    201,
  );
  const path = `/api/v1/composer/drafts/${draft.id}`;
  const source = expectStatus(
    await api(page, "POST", `${path}/revisions/from-source`, {
      headers: {
        "If-Match": draft.etag,
        "Idempotency-Key": `typed-source-${suffix}`,
      },
    }),
    201,
  );
  const current = expectStatus(await api(page, "GET", path), 200);
  assert.equal(current.current_revision_id, source.id);
  return { path, source, current };
}

async function exactDocx(page, path, revision) {
  const artifactPath = `${path}/revisions/${revision.id}/artifacts`;
  const [preview, download] = await Promise.all([
    page.request.get(`${artifactPath}/preview`),
    page.request.get(`${artifactPath}/download`),
  ]);
  assert.equal(preview.status(), 200);
  assert.equal(download.status(), 200);
  const [previewBytes, downloadBytes] = await Promise.all([
    preview.body(),
    download.body(),
  ]);
  assert.deepEqual(previewBytes, downloadBytes);
  assert.equal(downloadBytes.subarray(0, 2).toString(), "PK");
  assert.equal(
    revision.artifacts.find((artifact) => artifact.kind === "download")?.sha256,
    sha256(downloadBytes),
  );
  assert.equal(
    revision.artifacts.find((artifact) => artifact.kind === "preview")?.sha256,
    sha256(previewBytes),
  );
  return downloadBytes;
}

test(`typed Composer shares, human decisions, and exact regeneration in ${profile}`, async () => {
  const suffix = `${profile}-${Date.now()}`;
  const [templateBytes, schema] = await Promise.all([
    readFile(typedDocx),
    readFile(typedSchema, "utf8"),
  ]);
  assert.ok(templateBytes.length > 1000);
  const browser = await chromium.launch({
    executablePath:
      process.env.MARKWEAVE_E2E_CHROMIUM || "/usr/bin/google-chrome-stable",
    headless: true,
  });
  const contexts = await Promise.all(
    [0, 1, 2].map(() =>
      browser.newContext({ baseURL, serviceWorkers: "block" }),
    ),
  );
  const [admin, alice, bob] = await Promise.all(
    contexts.map((context) => context.newPage()),
  );
  try {
    await login(admin, "e2e-admin", "e2e-admin-password");
    const newUser = async (name) => {
      const password = `Composer-${name}-password`;
      const user = expectStatus(
        await api(admin, "POST", "/api/v1/admin/users", {
          json: {
            username: name,
            password,
            password_change_required: false,
          },
        }),
        201,
      );
      return { ...user, password };
    };
    const aliceUser = await newUser(`typed-alice-${suffix}`);
    const bobUser = await newUser(`typed-bob-${suffix}`);
    await Promise.all([
      login(alice, aliceUser.username, aliceUser.password),
      login(bob, bobUser.username, bobUser.password),
    ]);
    const connection = expectStatus(
      await api(admin, "POST", "/api/v1/composer/connections", {
        json: {
          scope: "instance",
          identity_mode: "shared",
          name: `Typed filling ${suffix}`,
          endpoint: providerEndpoint,
          permitted_models: [providerModel],
          allowed_user_ids: [aliceUser.id],
          selected_model: null,
          enabled: false,
        },
      }),
      201,
    );
    const connectionPath = `/api/v1/composer/connections/${connection.id}`;
    const credential = expectStatus(
      await api(admin, "PUT", `${connectionPath}/credentials`, {
        headers: { "If-Match": connection.etag },
        json: {
          api_key: providerSecret,
          internal_ca: await readFile("/run/composer-e2e-ca.crt", "utf8"),
        },
      }),
      200,
    );
    expectStatus(
      await api(admin, "PATCH", connectionPath, {
        headers: { "If-Match": credential.etag },
        json: { enabled: true, selected_model: providerModel },
      }),
      200,
    );
    expectStatus(
      await api(alice, "POST", `${connectionPath}/test`, { json: {} }),
      200,
    );

    const authorName = `Ada Lovelace ${suffix}`;
    let author = expectStatus(
      await api(admin, "POST", "/api/v1/composer/authors", {
        json: {
          name: authorName,
          fields: {
            role: { value: "Reviewer", provenance: "supplied" },
          },
        },
      }),
      201,
    );
    const authorPath = `/api/v1/composer/authors/${author.id}`;
    const templateCount = expectStatus(
      await api(admin, "GET", "/api/v1/composer/fill-templates"),
      200,
    ).templates.length;
    const infected = await api(
      admin,
      "POST",
      "/api/v1/composer/fill-templates",
      {
        typed: {
          name: `Rejected typed decision ${suffix}`,
          schema,
          bytes: Array.from(
            Buffer.concat([templateBytes, Buffer.from(malwareMarker)]),
          ),
        },
      },
    );
    assert.equal(infected.status, 422);
    assert.equal(infected.json.error.code, "UPLOAD_MALWARE_DETECTED");
    assert.equal(
      expectStatus(
        await api(admin, "GET", "/api/v1/composer/fill-templates"),
        200,
      ).templates.length,
      templateCount,
    );
    const malformedSchema = await api(
      admin,
      "POST",
      "/api/v1/composer/fill-templates",
      {
        typed: {
          name: `Malformed typed decision ${suffix}`,
          schema: '{"fields":"not-an-array","repeats":[]}',
          bytes: Array.from(templateBytes),
        },
      },
    );
    assert.equal(malformedSchema.status, 422);
    assert.equal(
      expectStatus(
        await api(admin, "GET", "/api/v1/composer/fill-templates"),
        200,
      ).templates.length,
      templateCount,
    );
    let template = expectStatus(
      await api(admin, "POST", "/api/v1/composer/fill-templates", {
        typed: {
          name: `Typed decision ${suffix}`,
          schema,
          bytes: Array.from(templateBytes),
        },
      }),
      201,
    );
    const templatePath = `/api/v1/composer/fill-templates/${template.id}`;
    const versionPath = `${templatePath}/versions/${template.active_version_id}`;
    const version = expectStatus(await api(admin, "GET", versionPath), 200);
    assert.equal(version.docx_sha256, sha256(templateBytes));
    assert.equal(version.schema_version, 1);
    assert.deepEqual(version.schema, JSON.parse(schema));
    const original = await admin.request.get(`${versionPath}/content`);
    assert.equal(original.status(), 200);
    assert.deepEqual(await original.body(), templateBytes);

    for (const page of [alice, bob]) {
      assert.equal((await api(page, "GET", authorPath)).status, 404);
      assert.equal((await api(page, "GET", templatePath)).status, 404);
      assert.equal(
        (await api(page, "GET", `${versionPath}/content`)).status,
        404,
      );
    }
    author = expectStatus(
      await api(admin, "PUT", `${authorPath}/grants/${aliceUser.id}`, {
        headers: { "If-Match": author.etag },
      }),
      200,
    );
    template = expectStatus(
      await api(admin, "PUT", `${templatePath}/grants/${aliceUser.id}`, {
        headers: { "If-Match": template.etag },
      }),
      200,
    );
    assert.equal((await api(alice, "GET", authorPath)).status, 200);
    assert.equal((await api(alice, "GET", versionPath)).status, 200);
    assert.equal((await api(bob, "GET", authorPath)).status, 404);
    assert.equal((await api(bob, "GET", templatePath)).status, 404);
    await alice.goto(`${baseURL}/composer/authors`);
    await alice.getByRole("heading", { name: "Author directory" }).waitFor();
    await alice
      .getByRole("button", { name: `${authorName} (shared)` })
      .waitFor();
    await alice.goto(`${baseURL}/composer/fill-templates`);
    await alice
      .getByRole("heading", { name: "Typed filling templates" })
      .waitFor();
    await alice
      .getByRole("button", { name: `${template.name} (shared)` })
      .waitFor();

    const browserDraft = await createDraftWithSource(
      alice,
      `${suffix}-browser`,
    );
    await alice.goto(`${baseURL}/composer`, { waitUntil: "networkidle" });
    await alice
      .getByRole("combobox", { name: "Saved drafts" })
      .selectOption(browserDraft.current.id);
    await alice
      .getByRole("combobox", { name: "Connection" })
      .selectOption(connection.id);
    await alice
      .getByRole("combobox", { name: "Model", exact: true })
      .selectOption(providerModel);
    await alice
      .getByRole("textbox", { name: "Exact approved text for transmission" })
      .fill("Review the named author facts for this report.");
    await alice
      .getByRole("spinbutton", { name: /Maximum output tokens/ })
      .fill("128");
    await alice
      .getByRole("group", { name: "Author entries for this model step" })
      .getByRole("checkbox", { name: authorName })
      .check();
    const [browserPromptResponse] = await Promise.all([
      alice.waitForResponse(
        (response) =>
          response.url().endsWith(`${browserDraft.path}/model-steps/preview`) &&
          response.request().method() === "POST",
      ),
      alice.getByRole("button", { name: "Preview exact prompt" }).click(),
    ]);
    assert.equal(browserPromptResponse.status(), 200);
    const browserPrompt = await browserPromptResponse.json();
    assert.match(
      browserPrompt.transmitted_content,
      /Review the named author facts/,
    );
    assert.match(browserPrompt.transmitted_content, new RegExp(authorName));
    assert.deepEqual(browserPrompt.author_refs, [
      { id: author.id, version: author.version },
    ]);
    await alice.getByText("Exact text selected for transmission:").waitFor();
    const [browserStepResponse] = await Promise.all([
      alice.waitForResponse(
        (response) =>
          response.url().endsWith(`${browserDraft.path}/model-steps`) &&
          response.request().method() === "POST",
      ),
      alice.getByRole("button", { name: "Send reviewed text" }).click(),
    ]);
    assert.equal(browserStepResponse.status(), 202);
    const browserStep = await browserStepResponse.json();
    assert.equal(browserStep.connection_id, connection.id);
    await alice.getByText("Model step queued for review.").waitFor();
    let completedStep = null;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const observed = expectStatus(
        await api(
          alice,
          "GET",
          `${browserDraft.path}/model-steps/${browserStep.id}`,
        ),
        200,
      );
      if (observed.status === "completed") {
        completedStep = observed;
        break;
      }
      assert.notEqual(
        observed.status,
        "failed",
        observed.error_code ?? "Model step failed",
      );
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    assert.ok(
      completedStep,
      "Authorized author step did not complete through the real provider",
    );
    assert.ok(completedStep.proposal_id);
    await alice
      .locator("details")
      .filter({ hasText: /Suggestion step completed/ })
      .locator("summary")
      .waitFor();
    await alice.reload({ waitUntil: "networkidle" });
    assert.equal(
      await alice.getByRole("combobox", { name: "Saved drafts" }).inputValue(),
      browserDraft.current.id,
    );

    const fillPanel = alice.getByRole("region", {
      name: "Typed document filling",
    });
    await fillPanel
      .getByRole("combobox", { name: "Typed filling template" })
      .selectOption(template.id);
    await fillPanel
      .getByRole("group", { name: "Authorized authors to include" })
      .getByRole("checkbox", { name: authorName })
      .check();
    await fillPanel.getByLabel("finding.count").fill("1");
    await fillPanel.getByRole("button", { name: "Add findings row" }).click();
    await fillPanel
      .getByLabel("findings[0].title")
      .fill("A browser-reviewed finding");
    await fillPanel.getByLabel("findings[0].score").fill("73");
    await fillPanel.getByRole("button", { name: "Save fill plan" }).click();
    await fillPanel.getByText(/Fill plan saved/).waitFor();
    await fillPanel.getByText(/decision.date:.*missing/).waitFor();
    await fillPanel.getByText(/decision.approved:.*missing/).waitFor();
    await fillPanel.getByLabel("decision.date").fill("2026-09-24");
    await fillPanel.getByLabel("decision.approved").selectOption("true");
    await fillPanel
      .getByRole("button", { name: "Save answers and edits" })
      .click();
    await fillPanel
      .getByText("No missing or ambiguous values remain.")
      .waitFor();
    await fillPanel
      .getByRole("button", { name: "Approve reviewed values" })
      .click();
    await fillPanel
      .getByText("Approved values are frozen for this plan.")
      .waitFor();
    await fillPanel
      .getByRole("button", { name: "Fill DOCX from approved values" })
      .click();
    await fillPanel.getByText(/Filled DOCX published as revision/).waitFor();
    const browserFill = expectStatus(
      await api(alice, "GET", `${browserDraft.path}/fill-plans`),
      200,
    ).plans[0];
    assert.equal(browserFill.state, "published");
    assert.equal(browserFill.values["decision.approved"], true);
    assert.equal(
      browserFill.values.findings[0].title,
      "A browser-reviewed finding",
    );
    const publishedFrame = alice.frameLocator('iframe[title^="DOCX revision"]');
    await publishedFrame.getByText("A browser-reviewed finding").waitFor();
    assert.match(
      await publishedFrame.locator("body").innerText(),
      /A browser-reviewed finding/,
    );

    const { path, source, current } = await createDraftWithSource(
      alice,
      suffix,
    );
    const planPath = `${path}/fill-plans`;
    let plan = expectStatus(
      await api(alice, "POST", planPath, {
        headers: {
          "If-Match": current.etag,
          "Idempotency-Key": `typed-plan-${suffix}`,
        },
        json: {
          source_revision_id: source.id,
          template_id: template.id,
          template_version_id: template.active_version_id,
          author_ids: [author.id],
          values: {
            "finding.count": 1,
            findings: [{ title: "A reviewed finding", score: 73 }],
          },
        },
      }),
      201,
    );
    assert.equal(plan.state, "pending");
    assert.equal(plan.values["author.name"], authorName);
    assert.deepEqual(plan.questions.map((question) => question.path).sort(), [
      "decision.approved",
      "decision.date",
    ]);
    assert.deepEqual(plan.author_refs, [
      { id: author.id, version: author.version },
    ]);
    const decisionPath = `${planPath}/${plan.id}`;
    assert.equal(
      (
        await api(alice, "POST", `${decisionPath}/approve`, {
          headers: {
            "If-Match": plan.etag,
            "Idempotency-Key": `typed-early-approval-${suffix}`,
          },
        })
      ).status,
      412,
    );
    const pendingEtag = plan.etag;
    plan = expectStatus(
      await api(alice, "PATCH", decisionPath, {
        headers: {
          "If-Match": pendingEtag,
          "Idempotency-Key": `typed-decision-${suffix}`,
        },
        json: {
          values: {
            ...plan.values,
            "decision.date": "2026-09-24",
            "decision.approved": true,
          },
          provenance: {
            ...plan.provenance,
            "decision.date": { kind: "human_edited" },
            "decision.approved": { kind: "human_edited" },
          },
        },
      }),
      200,
    );
    assert.deepEqual(plan.questions, []);
    assert.equal(
      (
        await api(alice, "PATCH", decisionPath, {
          headers: {
            "If-Match": pendingEtag,
            "Idempotency-Key": `typed-stale-${suffix}`,
          },
          json: { values: plan.values, provenance: plan.provenance },
        })
      ).status,
      412,
    );
    plan = expectStatus(
      await api(alice, "POST", `${decisionPath}/approve`, {
        headers: {
          "If-Match": plan.etag,
          "Idempotency-Key": `typed-approval-${suffix}`,
        },
      }),
      200,
    );
    assert.equal(plan.state, "approved");
    const published = expectStatus(
      await api(alice, "POST", `${decisionPath}/publish`, {
        headers: {
          "If-Match": plan.etag,
          "Idempotency-Key": `typed-publish-${suffix}`,
        },
      }),
      201,
    );
    assert.equal(published.operation, "fill_template");
    assert.equal(published.model_identity, null);
    assert.equal(
      expectStatus(
        await api(alice, "POST", `${decisionPath}/publish`, {
          headers: {
            "If-Match": plan.etag,
            "Idempotency-Key": `typed-publish-${suffix}`,
          },
        }),
        201,
      ).id,
      published.id,
    );
    assert.equal(
      (
        await api(alice, "POST", `${decisionPath}/publish`, {
          headers: {
            "If-Match": plan.etag,
            "Idempotency-Key": `typed-publish-different-${suffix}`,
          },
        })
      ).status,
      412,
    );
    const frozen = JSON.parse(published.typed_fill_snapshot);
    assert.equal(frozen.template_version_id, template.active_version_id);
    assert.equal(frozen.template_docx_sha256, version.docx_sha256);
    assert.equal(frozen.fill_plan_id, plan.id);
    assert.equal(frozen.approved_values["author.name"], authorName);
    assert.equal(frozen.approved_values["decision.date"], "2026-09-24");
    assert.equal(frozen.approved_values["decision.approved"], true);
    assert.equal(frozen.provenance["decision.date"].kind, "human_edited");
    const firstBytes = await exactDocx(alice, path, published);
    assert.equal(frozen.result_sha256, sha256(firstBytes));
    assert.equal(
      (await api(bob, "GET", `${path}/revisions/${published.id}`)).status,
      404,
    );
    const afterPublish = expectStatus(await api(alice, "GET", path), 200);
    const regenerated = expectStatus(
      await api(
        alice,
        "POST",
        `${path}/revisions/${published.id}/regenerations`,
        {
          headers: {
            "If-Match": afterPublish.etag,
            "Idempotency-Key": `typed-regenerate-${suffix}`,
          },
        },
      ),
      201,
    );
    assert.equal(regenerated.operation, "regenerate_fill");
    assert.equal(regenerated.model_identity, null);
    assert.deepEqual(await exactDocx(alice, path, regenerated), firstBytes);
    assert.equal(
      (
        await api(
          alice,
          "POST",
          `${path}/revisions/${published.id}/regenerations`,
          {
            headers: {
              "If-Match": afterPublish.etag,
              "Idempotency-Key": `typed-stale-regenerate-${suffix}`,
            },
          },
        )
      ).status,
      412,
    );

    const second = await createDraftWithSource(alice, `${suffix}-revoked`);
    const revocablePlan = expectStatus(
      await api(alice, "POST", `${second.path}/fill-plans`, {
        headers: {
          "If-Match": second.current.etag,
          "Idempotency-Key": `typed-revocable-${suffix}`,
        },
        json: {
          source_revision_id: second.source.id,
          template_id: template.id,
          template_version_id: template.active_version_id,
          author_ids: [author.id],
          values: {},
        },
      }),
      201,
    );
    const secondAfterPlan = expectStatus(
      await api(alice, "GET", second.path),
      200,
    );
    const revokedPreview = expectStatus(
      await api(alice, "POST", `${second.path}/model-steps/preview`, {
        headers: { "If-Match": secondAfterPlan.etag },
        json: {
          connection_id: connection.id,
          approved_endpoint: providerEndpoint,
          approved_model: providerModel,
          content: "Use selected author facts.",
          author_ids: [author.id],
          max_output_tokens: 8,
        },
      }),
      200,
    );
    assert.deepEqual(revokedPreview.author_refs, revocablePlan.author_refs);
    author = expectStatus(
      await api(admin, "DELETE", `${authorPath}/grants/${aliceUser.id}`, {
        headers: { "If-Match": author.etag },
      }),
      200,
    );
    template = expectStatus(
      await api(admin, "DELETE", `${templatePath}/grants/${aliceUser.id}`, {
        headers: { "If-Match": template.etag },
      }),
      200,
    );
    assert.equal((await api(alice, "GET", authorPath)).status, 404);
    assert.equal((await api(alice, "GET", templatePath)).status, 404);
    assert.equal(
      (await api(alice, "GET", `${versionPath}/content`)).status,
      404,
    );
    assert.equal(
      (await api(alice, "GET", `${second.path}/fill-plans/${revocablePlan.id}`))
        .status,
      412,
    );
    assert.equal(
      (
        await api(
          alice,
          "POST",
          `${second.path}/fill-plans/${revocablePlan.id}/approve`,
          {
            headers: {
              "If-Match": revocablePlan.etag,
              "Idempotency-Key": `typed-revoked-${suffix}`,
            },
          },
        )
      ).status,
      412,
    );
    const revokedDraft = expectStatus(
      await api(alice, "GET", second.path),
      200,
    );
    assert.equal(
      (
        await api(alice, "POST", `${second.path}/model-steps`, {
          headers: {
            "If-Match": revokedDraft.etag,
            "Idempotency-Key": `typed-revoked-model-${suffix}`,
          },
          json: {
            connection_id: connection.id,
            approved_endpoint: "https://e2e-llm:8443/v1",
            approved_model: "composer-e2e-model",
            content: "Use selected author facts.",
            max_output_tokens: 8,
            author_refs: revokedPreview.author_refs,
            author_preview_digest: revokedPreview.preview_digest,
          },
        })
      ).status,
      412,
    );
    assert.equal((await api(admin, "GET", authorPath)).status, 200);
    assert.equal((await api(admin, "GET", templatePath)).status, 200);
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
    await browser.close();
  }
});
