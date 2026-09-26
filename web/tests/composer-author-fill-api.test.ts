import type { ApiTransport } from "../src/api/transport";
import { AuthorDirectoryApi } from "../src/composer/author-directory-api";
import { FillTemplateApi } from "../src/composer/fill-template-api";
import { ComposerWorkspaceApi } from "../src/composer/workspace-api";

test("author writes use same-origin Composer routes, CSRF, and exact revision preconditions", async () => {
  const transport = {
    json: vi.fn().mockResolvedValue({ authors: [] }),
    jsonWithMetadata: vi.fn().mockResolvedValue({ data: {} }),
  };
  const api = new AuthorDirectoryApi(transport as unknown as ApiTransport);
  await api.list();
  await api.create({ name: "Alice", fields: {} });
  await api.update("author-1", '"author-2"', { name: "Alice", fields: {} });
  await api.grant("author-1", "user-2", '"author-3"');
  await api.revoke("author-1", "user-2", '"author-4"');
  expect(transport.json.mock.calls[0]![0]).toBe(
    "/api/v1/composer/authors?limit=100&offset=0",
  );
  expect(transport.json.mock.calls[1]![2]).toMatchObject({
    csrf: true,
    method: "POST",
  });
  expect(transport.jsonWithMetadata.mock.calls.map((call) => call[2])).toEqual([
    expect.objectContaining({
      csrf: true,
      etag: '"author-2"',
      method: "PATCH",
    }),
    expect.objectContaining({ csrf: true, etag: '"author-3"', method: "PUT" }),
    expect.objectContaining({
      csrf: true,
      etag: '"author-4"',
      method: "DELETE",
    }),
  ]);
});

test("typed upload and fill operations stay separate from Pandoc templates and keep fences", async () => {
  const transport = {
    json: vi.fn().mockResolvedValue({ templates: [], plans: [] }),
    jsonWithMetadata: vi.fn().mockResolvedValue({ data: {} }),
    multipartWithMetadata: vi.fn().mockResolvedValue({ data: {} }),
    download: vi.fn(),
  };
  const api = new FillTemplateApi(transport as unknown as ApiTransport);
  const file = new File(["DOCX"], "letter.docx");
  const schema = {
    fields: [
      {
        name: "author_name",
        type: "text" as const,
        required: true,
        constraints: {},
      },
    ],
    repeats: [],
  };
  await api.create("Letter", schema, file);
  await api.replace("template-1", '"template-2"', schema, file);
  const form = transport.multipartWithMetadata.mock.calls[0]![1] as FormData;
  expect(form.get("schema")).toBe(JSON.stringify(schema));
  expect(form.get("file")).toBe(file);
  expect(transport.multipartWithMetadata.mock.calls[0]![0]).toBe(
    "/api/v1/composer/fill-templates",
  );
  expect(transport.multipartWithMetadata.mock.calls[1]![3]).toMatchObject({
    csrf: true,
    etag: '"template-2"',
  });

  await api.createPlan("draft-1", '"draft-3"', "create-key", {
    source_revision_id: "source-1",
    template_id: "template-1",
    template_version_id: "version-1",
    author_ids: ["author-1"],
    values: { author_name: "Alice" },
  });
  await api.updatePlan(
    "draft-1",
    { id: "plan-1", etag: '"plan-2"' } as never,
    { author_name: "Alice" },
    { author_name: { kind: "human_edited" } },
    "update-key",
  );
  await api.approve(
    "draft-1",
    { id: "plan-1", etag: '"plan-3"' } as never,
    "approve-key",
  );
  await api.publish(
    "draft-1",
    { id: "plan-1", etag: '"plan-4"' } as never,
    "publish-key",
  );
  await api.regenerate("draft-1", "revision-1", '"draft-5"', "regen-key");
  const calls = transport.json.mock.calls.filter((call) => call[2]?.method);
  expect(calls.map((call) => call[0])).toEqual([
    "/api/v1/composer/drafts/draft-1/fill-plans",
    "/api/v1/composer/drafts/draft-1/fill-plans/plan-1",
    "/api/v1/composer/drafts/draft-1/fill-plans/plan-1/approve",
    "/api/v1/composer/drafts/draft-1/fill-plans/plan-1/publish",
    "/api/v1/composer/drafts/draft-1/revisions/revision-1/regenerations",
  ]);
  expect(calls.map((call) => call[2])).toEqual([
    expect.objectContaining({
      csrf: true,
      etag: '"draft-3"',
      idempotencyKey: "create-key",
    }),
    expect.objectContaining({
      csrf: true,
      etag: '"plan-2"',
      idempotencyKey: "update-key",
      method: "PATCH",
    }),
    expect.objectContaining({
      csrf: true,
      etag: '"plan-3"',
      idempotencyKey: "approve-key",
    }),
    expect.objectContaining({
      csrf: true,
      etag: '"plan-4"',
      idempotencyKey: "publish-key",
    }),
    expect.objectContaining({
      csrf: true,
      etag: '"draft-5"',
      idempotencyKey: "regen-key",
    }),
  ]);
});

test("author prompt preview and model step carry the reviewed binding", async () => {
  const transport = { json: vi.fn().mockResolvedValue({}) };
  const api = new ComposerWorkspaceApi(transport as unknown as ApiTransport);
  await api.previewStep(
    { id: "draft-1", etag: '"draft-3"' },
    {
      connection_id: "connection-1",
      approved_endpoint: "https://llm.example/v1",
      approved_model: "small-model",
      content: "Draft approved text",
      author_ids: ["author-1"],
      max_output_tokens: 100,
    },
  );
  await api.startStep(
    { id: "draft-1", etag: '"draft-3"' } as never,
    {
      connection_id: "connection-1",
      approved_endpoint: "https://llm.example/v1",
      approved_model: "small-model",
      content: "Draft approved text",
      intent: "proposal",
      max_output_tokens: 100,
      author_refs: [{ id: "author-1", version: 2 }],
      author_preview_digest: "digest",
    },
    "step-key",
  );
  expect(transport.json.mock.calls[0]![0]).toBe(
    "/api/v1/composer/drafts/draft-1/model-steps/preview",
  );
  expect(transport.json.mock.calls[0]![2]).toMatchObject({
    csrf: true,
    etag: '"draft-3"',
    method: "POST",
  });
  expect(JSON.parse(transport.json.mock.calls[1]![2].body)).toMatchObject({
    author_refs: [{ id: "author-1", version: 2 }],
    author_preview_digest: "digest",
  });
});

test("read routes address authorized author and exact typed versions without mutation headers", async () => {
  const transport = {
    json: vi.fn().mockResolvedValue({
      authors: [],
      templates: [],
      versions: [],
      plans: [],
    }),
    jsonWithMetadata: vi.fn().mockResolvedValue({ data: {} }),
    download: vi.fn().mockResolvedValue(new Response("DOCX")),
  };
  const authors = new AuthorDirectoryApi(transport as unknown as ApiTransport);
  const templates = new FillTemplateApi(transport as unknown as ApiTransport);
  await authors.get("author-1");
  await authors.list(100);
  await templates.list(100);
  await templates.get("template-1");
  await templates.versions("template-1", 100);
  await templates.version("template-1", "version-1");
  await templates.download("template-1", "version-1");
  await templates.plans("draft-1", 100);
  await templates.getPlan("draft-1", "plan-1");
  expect(transport.json.mock.calls.map((call) => call[0])).toEqual([
    "/api/v1/composer/authors?limit=100&offset=100",
    "/api/v1/composer/fill-templates?limit=100&offset=100",
    "/api/v1/composer/fill-templates/template-1/versions?limit=100&offset=100",
    "/api/v1/composer/fill-templates/template-1/versions/version-1",
    "/api/v1/composer/drafts/draft-1/fill-plans?limit=100&offset=100",
    "/api/v1/composer/drafts/draft-1/fill-plans/plan-1",
  ]);
  expect(transport.download.mock.calls[0]![0]).toBe(
    "/api/v1/composer/fill-templates/template-1/versions/version-1/content",
  );
  expect(transport.jsonWithMetadata.mock.calls.map((call) => call[0])).toEqual([
    "/api/v1/composer/authors/author-1",
    "/api/v1/composer/fill-templates/template-1",
  ]);
});
