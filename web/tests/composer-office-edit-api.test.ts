import type { ApiTransport } from "../src/api/transport";
import {
  OfficeEditApi,
  parseOfficeProposal,
} from "../src/composer/office-edit-api";

const draftId = "00000000-0000-4000-8000-000000000101";
const revisionId = "00000000-0000-4000-8000-000000000201";
const request = {
  connection_id: "connection-1",
  approved_endpoint: "https://llm.example/v1",
  approved_model: "small",
  instruction: "Formal.",
  targets: ["word/document.xml#t0"],
  max_output_tokens: 64,
};
const proposal = {
  kind: "office_edit",
  format: "docx",
  engine_version: 1,
  base_revision_id: revisionId,
  base_sha256: "a".repeat(64),
  allowed_targets: ["word/document.xml#t0"],
  operations: [
    {
      op: "replace_text",
      target: "word/document.xml#t0",
      expected_text: "Old",
      text: "New",
    },
  ],
};

test("Office edits use exact revision routes, CSRF, preconditions, and keys", async () => {
  const transport = {
    json: vi.fn().mockResolvedValue({}),
    jsonWithMetadata: vi.fn().mockResolvedValue({ data: {} }),
  };
  const api = new OfficeEditApi(transport as unknown as ApiTransport);
  const base = `/api/v1/composer/drafts/${draftId}/revisions/${revisionId}`;

  await api.targets(draftId, revisionId, "pptx", 200);
  await api.edit(draftId, revisionId, "docx", '"draft-2"', "key-1", [
    { target: "word/document.xml#t0", expected_text: "Old", text: "New" },
  ]);
  await api.previewStep(draftId, revisionId, '"draft-2"', request);
  await api.startStep(draftId, revisionId, '"draft-2"', "key-2", {
    ...request,
    preview_digest: "b".repeat(64),
  });
  await api.step(draftId, "step-1");
  await api.publishProposal(draftId, "proposal-1", '"draft-4"', "key-3");

  expect(transport.json.mock.calls.map((call) => call[0])).toEqual([
    `${base}/pptx-text-targets?limit=200&offset=200`,
    `${base}/office-edit-steps/preview`,
    `${base}/office-edit-steps`,
    `/api/v1/composer/drafts/${draftId}/model-steps/step-1`,
  ]);
  expect(transport.json.mock.calls[1]![2]).toMatchObject({
    csrf: true,
    etag: '"draft-2"',
    method: "POST",
  });
  expect(transport.json.mock.calls[2]![2]).toMatchObject({
    csrf: true,
    etag: '"draft-2"',
    idempotencyKey: "key-2",
  });
  expect(transport.jsonWithMetadata.mock.calls.map((call) => call[0])).toEqual([
    `${base}/docx-edits`,
    `/api/v1/composer/drafts/${draftId}/proposals/proposal-1/office-edits`,
  ]);
  expect(
    JSON.parse(transport.jsonWithMetadata.mock.calls[0]![2].body as string),
  ).toEqual({
    operations: [
      {
        op: "replace_text",
        target: "word/document.xml#t0",
        expected_text: "Old",
        text: "New",
      },
    ],
  });
  expect(transport.jsonWithMetadata.mock.calls[1]![2]).toMatchObject({
    csrf: true,
    etag: '"draft-4"',
    idempotencyKey: "key-3",
  });
});

test("only a complete Office edit record is treated as an Office proposal", () => {
  expect(parseOfficeProposal(JSON.stringify(proposal))?.operations).toEqual(
    proposal.operations,
  );
  expect(parseOfficeProposal("# Markdown proposal")).toBeNull();
  expect(
    parseOfficeProposal(JSON.stringify({ ...proposal, kind: "markdown" })),
  ).toBeNull();
  expect(
    parseOfficeProposal(JSON.stringify({ ...proposal, extra: true })),
  ).toBeNull();
  expect(
    parseOfficeProposal(
      JSON.stringify({
        ...proposal,
        operations: [{ ...proposal.operations[0], op: "delete" }],
      }),
    ),
  ).toBeNull();
});
