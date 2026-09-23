import type { ApiTransport } from "../src/api/transport";
import { ComposerWorkspaceApi } from "../src/composer/workspace-api";

const draft = {
  id: "00000000-0000-4000-8000-000000000001",
  title: "Draft",
  content: "Original",
  version: 3,
  current_revision_id: null,
  source_kind: "upload",
  source_media_type: "text/markdown",
  created_at: "2026-09-23T12:00:00Z",
  updated_at: "2026-09-23T12:00:00Z",
  etag: '"composer-draft-3"',
};

test("workspace mutations retain CSRF, exact ETag, and idempotency fences", async () => {
  const transport = {
    json: vi.fn().mockResolvedValue({}),
    jsonWithMetadata: vi.fn().mockResolvedValue({ data: {} }),
    multipartWithMetadata: vi.fn().mockResolvedValue({ data: {} }),
    download: vi.fn(),
  };
  const api = new ComposerWorkspaceApi(transport as unknown as ApiTransport);
  await api.addMessage(draft, "Question", "message-key");
  await api.startStep(
    draft,
    {
      connection_id: "connection",
      approved_endpoint: "https://llm.example/v1",
      approved_model: "small-model",
      content: "Approved exact text",
      max_output_tokens: 100,
      intent: "proposal",
      answered_question_id: "question",
    },
    "step-key",
  );
  await api.decide(draft, "proposal", "edited", "Human correction");
  await api.answerQuestion(draft, "question", "Signed date", "answer-key");
  await api.publishProposal(draft, "proposal", "publish-key");
  await api.restore(draft, "revision", "restore-key");
  await api.captureSource(draft, "capture-key");

  const mutationOptions = [
    ...transport.json.mock.calls,
    ...transport.jsonWithMetadata.mock.calls,
  ].map((call) => call[2]);
  expect(
    mutationOptions.every(
      (options) =>
        options.csrf &&
        options.etag === draft.etag &&
        options.method === "POST",
    ),
  ).toBe(true);
  expect(mutationOptions.map((options) => options.idempotencyKey)).toEqual([
    "message-key",
    "step-key",
    undefined,
    "answer-key",
    "publish-key",
    "restore-key",
    "capture-key",
  ]);
  expect(JSON.parse(transport.json.mock.calls[1]![2].body)).toMatchObject({
    approved_endpoint: "https://llm.example/v1",
    approved_model: "small-model",
    content: "Approved exact text",
    intent: "proposal",
    answered_question_id: "question",
  });
  expect(JSON.parse(transport.json.mock.calls[2]![2].body)).toEqual({
    state: "edited",
    decided_value: "Human correction",
  });
});

test("artifact requests and semantic comparison stay bound to exact draft and revisions", async () => {
  const transport = {
    json: vi.fn().mockResolvedValue({}),
    jsonWithMetadata: vi.fn(),
    multipartWithMetadata: vi.fn(),
    download: vi.fn().mockResolvedValue(new Response("bytes")),
  };
  const api = new ComposerWorkspaceApi(transport as unknown as ApiTransport);
  await api.diff(draft.id, "new-revision", "old-revision");
  await api.download(draft.id, "new-revision", "preview");
  expect(transport.json.mock.calls[0]![0]).toBe(
    `/api/v1/composer/drafts/${draft.id}/revisions/new-revision/diff?from_revision_id=old-revision`,
  );
  expect(transport.download.mock.calls[0]![0]).toBe(
    `/api/v1/composer/drafts/${draft.id}/revisions/new-revision/artifacts/preview`,
  );
});

test("owner reads address one draft and use bounded pages for its conversation and history", async () => {
  const transport = {
    json: vi.fn().mockResolvedValue({
      drafts: [],
      messages: [],
      proposals: [],
      questions: [],
      revisions: [],
    }),
    jsonWithMetadata: vi.fn(),
    multipartWithMetadata: vi.fn(),
    download: vi.fn(),
  };
  const api = new ComposerWorkspaceApi(transport as unknown as ApiTransport);
  await api.drafts();
  await api.draft(draft.id);
  await api.messages(draft.id);
  await api.proposals(draft.id);
  await api.questions(draft.id);
  await api.revisions(draft.id);
  await api.revision(draft.id, "revision-id");
  await api.step(draft.id, "step-id");
  expect(transport.json.mock.calls.map((call) => call[0])).toEqual([
    "/api/v1/composer/drafts?limit=100&offset=0",
    `/api/v1/composer/drafts/${draft.id}`,
    `/api/v1/composer/drafts/${draft.id}/messages?limit=100&offset=0&order=desc`,
    `/api/v1/composer/drafts/${draft.id}/proposals?limit=100&offset=0&order=desc`,
    `/api/v1/composer/drafts/${draft.id}/questions?limit=100&offset=0&order=desc`,
    `/api/v1/composer/drafts/${draft.id}/revisions?limit=100&offset=0&order=desc`,
    `/api/v1/composer/drafts/${draft.id}/revisions/revision-id`,
    `/api/v1/composer/drafts/${draft.id}/model-steps/step-id`,
  ]);
});

test("owner history requests use explicit offsets without changing the selected draft", async () => {
  const transport = {
    json: vi.fn().mockResolvedValue({
      drafts: [],
      messages: [],
      proposals: [],
      questions: [],
      revisions: [],
      limit: 100,
      offset: 100,
    }),
  };
  const api = new ComposerWorkspaceApi(transport as unknown as ApiTransport);
  await api.drafts(undefined, 100);
  await api.messages(draft.id, undefined, 100);
  await api.proposals(draft.id, undefined, 100);
  await api.questions(draft.id, undefined, 100);
  await api.revisions(draft.id, undefined, 100);
  expect(transport.json.mock.calls.map((call) => call[0])).toEqual([
    "/api/v1/composer/drafts?limit=100&offset=100",
    `/api/v1/composer/drafts/${draft.id}/messages?limit=100&offset=100&order=desc`,
    `/api/v1/composer/drafts/${draft.id}/proposals?limit=100&offset=100&order=desc`,
    `/api/v1/composer/drafts/${draft.id}/questions?limit=100&offset=100&order=desc`,
    `/api/v1/composer/drafts/${draft.id}/revisions?limit=100&offset=100&order=desc`,
  ]);
});

test("source upload and step cancellation keep the CSRF boundary", async () => {
  const transport = {
    json: vi.fn().mockResolvedValue({}),
    jsonWithMetadata: vi.fn().mockResolvedValue({ data: {} }),
    multipartWithMetadata: vi.fn().mockResolvedValue({ data: {} }),
    download: vi.fn(),
  };
  const api = new ComposerWorkspaceApi(transport as unknown as ApiTransport);
  const file = new File(["# Source"], "source.md", {
    type: "text/markdown",
  });
  await api.createDraft(file);
  await api.cancelStep(draft.id, "step-id");
  expect(transport.multipartWithMetadata.mock.calls[0]![0]).toBe(
    "/api/v1/composer/drafts",
  );
  expect(transport.multipartWithMetadata.mock.calls[0]![1].get("source")).toBe(
    file,
  );
  expect(transport.multipartWithMetadata.mock.calls[0]![3]).toMatchObject({
    csrf: true,
  });
  expect(transport.json.mock.calls[0]![0]).toBe(
    `/api/v1/composer/drafts/${draft.id}/model-steps/step-id`,
  );
  expect(transport.json.mock.calls[0]![2]).toMatchObject({
    method: "DELETE",
    csrf: true,
  });
});

test("generation stays bound to one approved source, selected options, and owner preconditions", async () => {
  const transport = {
    json: vi.fn().mockResolvedValue({}),
    jsonWithMetadata: vi.fn().mockResolvedValue({ data: {} }),
    multipartWithMetadata: vi.fn(),
    download: vi.fn(),
  };
  const api = new ComposerWorkspaceApi(transport as unknown as ApiTransport);
  await api.startGeneration(
    draft,
    "approved-revision",
    {
      output: "pptx",
      template_id: "template",
      template_version_id: "frozen-version",
      presentation_dialect: "marp",
      slide_level: 2,
    },
    "submit-key",
  );
  await api.generation(draft.id, "generation");
  await api.cancelGeneration(draft.id, "generation");
  await api.publishGeneration(draft, "generation", "publish-key");

  expect(transport.jsonWithMetadata.mock.calls[0]![0]).toBe(
    `/api/v1/composer/drafts/${draft.id}/revisions/approved-revision/generations`,
  );
  expect(JSON.parse(transport.jsonWithMetadata.mock.calls[0]![2].body)).toEqual(
    {
      output: "pptx",
      template_id: "template",
      template_version_id: "frozen-version",
      presentation_dialect: "marp",
      slide_level: 2,
    },
  );
  expect(transport.jsonWithMetadata.mock.calls[0]![2]).toMatchObject({
    csrf: true,
    etag: draft.etag,
    idempotencyKey: "submit-key",
    method: "POST",
  });
  expect(transport.json.mock.calls[0]![0]).toBe(
    `/api/v1/composer/drafts/${draft.id}/generations/generation`,
  );
  expect(transport.json.mock.calls[1]![2]).toMatchObject({
    csrf: true,
    method: "DELETE",
  });
  expect(transport.jsonWithMetadata.mock.calls[1]![0]).toBe(
    `/api/v1/composer/drafts/${draft.id}/generations/generation/publish`,
  );
  expect(transport.jsonWithMetadata.mock.calls[1]![2]).toMatchObject({
    csrf: true,
    etag: draft.etag,
    idempotencyKey: "publish-key",
    method: "POST",
  });
});
