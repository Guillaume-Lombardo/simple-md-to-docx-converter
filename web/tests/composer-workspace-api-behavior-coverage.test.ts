import { ApiTransport } from "../src/api/transport";
import { ComposerWorkspaceApi } from "../src/composer/workspace-api";

const draftId = "00000000-0000-4000-8000-000000000101";
const revisionId = "00000000-0000-4000-8000-000000000201";
const generationId = "00000000-0000-4000-8000-000000000301";
const templateId = "00000000-0000-4000-8000-000000000401";
const draft = {
  id: draftId,
  title: "Reviewed document",
  content: "# Approved text",
  version: 3,
  current_revision_id: revisionId,
  source_kind: "upload",
  source_media_type: "text/markdown",
  created_at: "2026-09-23T12:00:00Z",
  updated_at: "2026-09-23T12:00:00Z",
  etag: '"composer-draft-3"',
};
const proposal = {
  id: "00000000-0000-4000-8000-000000000501",
  draft_id: draftId,
  base_version: 3,
  state: "pending",
  proposed_value: "Unverified model text",
  decided_value: null,
  provenance: "model:approved",
  created_at: "2026-09-23T12:00:00Z",
  decided_at: null,
  decided_by: null,
};
const question = {
  id: "00000000-0000-4000-8000-000000000601",
  draft_id: draftId,
  model_step_id: "00000000-0000-4000-8000-000000000701",
  base_version: 3,
  state: "pending",
  text: "Which date is signed?",
  answer_message_id: null,
  answer_content: null,
  created_at: "2026-09-23T12:00:00Z",
  answered_at: null,
};
const revision = {
  id: revisionId,
  draft_id: draftId,
  number: 1,
  operation: "capture_source",
  provenance: "human:source",
  source_sha256: "a".repeat(64),
  template_reference: null,
  approved_values: "{}",
  render_options: "{}",
  model_identity: null,
  artifacts: [],
  restored_from_revision_id: null,
  created_at: "2026-09-23T12:00:00Z",
};
const generation = {
  id: generationId,
  draft_id: draftId,
  source_revision_id: revisionId,
  job_id: null,
  status: "queued",
  output: "pdf",
  template_id: null,
  template_version_id: null,
  presentation_dialect: null,
  slide_level: null,
  result_revision_id: null,
  publishable: false,
  created_at: "2026-09-23T12:00:00Z",
};
const template = {
  id: templateId,
  name: "Approved style",
  description: "Reviewed template",
  current_version_id: "00000000-0000-4000-8000-000000000402",
  kind: "docx",
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function apiWith(fetcher: typeof fetch) {
  return new ComposerWorkspaceApi(
    new ApiTransport(fetcher, () => "__Host-md_converter_csrf=review-token"),
  );
}

test("generation recovery lists only the selected draft's typed jobs and rejects malformed history", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(
      jsonResponse({ generations: [generation], limit: 20, offset: 0 }),
    )
    .mockResolvedValueOnce(
      jsonResponse({
        generations: [{ ...generation, output: "exe" }],
        limit: 20,
        offset: 0,
      }),
    );
  const api = apiWith(fetcher);
  expect(await api.generations(draftId)).toEqual([generation]);
  await expect(api.generations(draftId)).rejects.toMatchObject({
    status: 200,
    code: "UNEXPECTED_RESPONSE",
  });
  expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
    `/api/v1/composer/drafts/${draftId}/generations?limit=20&offset=0`,
    `/api/v1/composer/drafts/${draftId}/generations?limit=20&offset=0`,
  ]);
});

test("template choices and conversion defaults follow the requested output family", async () => {
  const fetcher = vi.fn<typeof fetch>(async (path) => {
    const url = String(path);
    if (url.startsWith("/api/v1/templates?"))
      return jsonResponse({
        items: [
          { ...template, kind: url.includes("kind=pptx") ? "pptx" : "docx" },
        ],
        limit: 100,
        offset: 0,
      });
    return jsonResponse({
      resolved_template: template,
      template_version_id: template.current_version_id,
      selection_source: "user_default",
    });
  });
  const api = apiWith(fetcher);
  expect((await api.conversionOptions("docx")).selection_source).toBe(
    "user_default",
  );
  expect((await api.conversionOptions("pdf")).template_version_id).toBe(
    template.current_version_id,
  );
  expect((await api.conversionOptions("pptx")).resolved_template?.id).toBe(
    templateId,
  );
  expect((await api.templates("docx"))[0]?.kind).toBe("docx");
  expect((await api.templates("pdf"))[0]?.kind).toBe("docx");
  expect((await api.templates("pptx"))[0]?.kind).toBe("pptx");
  expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
    "/api/v1/conversion-options",
    "/api/v1/conversion-options",
    "/api/v1/conversion-options?template_kind=pptx",
    "/api/v1/templates?status=active&kind=docx&offset=0&limit=100",
    "/api/v1/templates?status=active&kind=docx&offset=0&limit=100",
    "/api/v1/templates?status=active&kind=pptx&offset=0&limit=100",
  ]);
});

test("paged question, proposal, and revision records reject corrupt items instead of showing partial history", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(
      jsonResponse({ questions: [question], limit: 100, offset: 100 }),
    )
    .mockResolvedValueOnce(
      jsonResponse({ proposals: [proposal], limit: 100, offset: 100 }),
    )
    .mockResolvedValueOnce(
      jsonResponse({ revisions: [revision], limit: 100, offset: 100 }),
    )
    .mockResolvedValueOnce(
      jsonResponse({
        questions: [question, { ...question, state: "secret" }],
        limit: 100,
        offset: 200,
      }),
    );
  const api = apiWith(fetcher);
  expect(await api.questions(draftId, undefined, 100)).toEqual([question]);
  expect(await api.proposals(draftId, undefined, 100)).toEqual([proposal]);
  expect(await api.revisions(draftId, undefined, 100)).toEqual([
    {
      id: revision.id,
      draft_id: revision.draft_id,
      number: revision.number,
      operation: revision.operation,
      provenance: revision.provenance,
      model_identity: revision.model_identity,
      restored_from_revision_id: revision.restored_from_revision_id,
      created_at: revision.created_at,
    },
  ]);
  await expect(api.questions(draftId, undefined, 200)).rejects.toMatchObject({
    status: 200,
    code: "UNEXPECTED_RESPONSE",
  });
  expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
    `/api/v1/composer/drafts/${draftId}/questions?limit=100&offset=100&order=desc`,
    `/api/v1/composer/drafts/${draftId}/proposals?limit=100&offset=100&order=desc`,
    `/api/v1/composer/drafts/${draftId}/revisions?limit=100&offset=100&order=desc`,
    `/api/v1/composer/drafts/${draftId}/questions?limit=100&offset=200&order=desc`,
  ]);
});

test.each([401, 403])(
  "a %i on a private Composer page or artifact remains a typed authorization failure",
  async (status) => {
    const code = status === 401 ? "AUTHENTICATION_REQUIRED" : "FORBIDDEN";
    const fetcher = vi.fn<typeof fetch>(async () =>
      jsonResponse({ error: { code, message: "Access denied." } }, status),
    );
    const api = apiWith(fetcher);
    await expect(api.messages(draftId)).rejects.toMatchObject({
      status,
      code,
    });
    await expect(
      api.download(draftId, revisionId, "download"),
    ).rejects.toMatchObject({
      status,
      code,
    });
    expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
      `/api/v1/composer/drafts/${draftId}/messages?limit=100&offset=0&order=desc`,
      `/api/v1/composer/drafts/${draftId}/revisions/${revisionId}/artifacts/download`,
    ]);
  },
);

test("a human proposal decision omits the correction unless it was explicitly supplied", async () => {
  const fetcher = vi.fn<typeof fetch>(async () => jsonResponse(proposal));
  const api = apiWith(fetcher);
  await api.decide(draft, proposal.id, "accepted");
  await api.decide(draft, proposal.id, "rejected");
  await api.decide(draft, proposal.id, "edited", "");
  const requests = fetcher.mock.calls.map((call) => call[1]!);
  expect(requests.map((request) => JSON.parse(String(request.body)))).toEqual([
    { state: "accepted" },
    { state: "rejected" },
    { state: "edited", decided_value: "" },
  ]);
  for (const request of requests) {
    const headers = new Headers(request.headers);
    expect(headers.get("if-match")).toBe(draft.etag);
    expect(headers.get("x-csrf-token")).toBe("review-token");
  }
});

test("a draft save conflict keeps the server ETag boundary for an explicit retry", async () => {
  const changed = { ...draft, version: 4, etag: '"composer-draft-4"' };
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(
      jsonResponse(
        { error: { code: "PRECONDITION_FAILED", message: "Draft changed." } },
        412,
      ),
    )
    .mockResolvedValueOnce(
      jsonResponse({ ...changed, content: "# New review" }),
    );
  const api = apiWith(fetcher);
  await expect(
    api.saveDraft(draft, "Reviewed document", "# New review"),
  ).rejects.toMatchObject({
    status: 412,
    code: "PRECONDITION_FAILED",
  });
  const result = await api.saveDraft(
    changed,
    "Reviewed document",
    "# New review",
  );
  expect(result.data).toMatchObject({
    version: 4,
    content: "# New review",
  });
  expect(
    fetcher.mock.calls.map((call) =>
      new Headers(call[1]!.headers).get("if-match"),
    ),
  ).toEqual([draft.etag, changed.etag]);
  expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
    `/api/v1/composer/drafts/${draftId}`,
    `/api/v1/composer/drafts/${draftId}`,
  ]);
});

test("publishing an edited draft and retrying generation publication retain distinct exact request keys", async () => {
  const fetcher = vi.fn<typeof fetch>(async (path) =>
    jsonResponse(
      String(path).endsWith("/publish") || String(path).endsWith("/from-draft")
        ? revision
        : generation,
      201,
    ),
  );
  const api = apiWith(fetcher);
  await api.publishDraft(draft, "draft-publication-key");
  await api.startGeneration(
    draft,
    revisionId,
    {
      output: "pdf",
      template_id: null,
      template_version_id: null,
      presentation_dialect: null,
      slide_level: null,
    },
    "generation-start-key",
  );
  await api.publishGeneration(
    draft,
    generationId,
    "generation-publication-key",
  );
  const headers = fetcher.mock.calls.map(
    (call) => new Headers(call[1]!.headers),
  );
  expect(headers.map((item) => item.get("idempotency-key"))).toEqual([
    "draft-publication-key",
    "generation-start-key",
    "generation-publication-key",
  ]);
  expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
    `/api/v1/composer/drafts/${draftId}/revisions/from-draft`,
    `/api/v1/composer/drafts/${draftId}/revisions/${revisionId}/generations`,
    `/api/v1/composer/drafts/${draftId}/generations/${generationId}/publish`,
  ]);
});
