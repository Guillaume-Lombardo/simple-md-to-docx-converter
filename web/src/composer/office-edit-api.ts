import * as v from "valibot";
import { ApiTransport, type JsonResult } from "../api/transport";

export type OfficeFormat = "docx" | "pptx";

const targetSchema = v.object({
  target: v.string(),
  paragraph: v.number(),
  in_table: v.boolean(),
  text: v.string(),
  slide: v.optional(v.number()),
  notes: v.optional(v.boolean()),
});
const targetListSchema = v.object({
  revision_id: v.string(),
  download_sha256: v.string(),
  engine_version: v.number(),
  targets: v.array(targetSchema),
  total: v.number(),
  limit: v.number(),
  offset: v.number(),
});
const revisionSchema = v.object({
  id: v.string(),
  draft_id: v.string(),
  number: v.number(),
  operation: v.string(),
});
const stepPreviewSchema = v.object({
  format: v.picklist(["docx", "pptx"]),
  base_revision_id: v.string(),
  base_sha256: v.string(),
  transmitted_content: v.string(),
  targets: v.array(v.object({ target: v.string(), text: v.string() })),
  preview_digest: v.string(),
});
const stepSchema = v.object({
  id: v.string(),
  status: v.string(),
  proposal_id: v.nullable(v.string()),
  error_code: v.nullable(v.string()),
});
const proposalOperationSchema = v.strictObject({
  op: v.literal("replace_text"),
  target: v.string(),
  expected_text: v.string(),
  text: v.string(),
});
const proposalSchema = v.strictObject({
  kind: v.literal("office_edit"),
  format: v.picklist(["docx", "pptx"]),
  engine_version: v.number(),
  base_revision_id: v.string(),
  base_sha256: v.string(),
  allowed_targets: v.array(v.string()),
  operations: v.array(proposalOperationSchema),
});

export type OfficeTextTarget = v.InferOutput<typeof targetSchema>;
export type OfficeTextTargets = v.InferOutput<typeof targetListSchema>;
export type OfficeEditRevision = v.InferOutput<typeof revisionSchema>;
export type OfficeStepPreview = v.InferOutput<typeof stepPreviewSchema>;
export type OfficeStep = v.InferOutput<typeof stepSchema>;
export type OfficeProposal = v.InferOutput<typeof proposalSchema>;
export interface OfficeReplacement {
  target: string;
  expected_text: string;
  text: string;
}
export interface OfficeStepRequest {
  connection_id: string;
  approved_endpoint: string;
  approved_model: string;
  instruction: string;
  targets: string[];
  max_output_tokens: number;
}

/** Return a reviewed Office edit proposal, or null for any other proposal text. */
export function parseOfficeProposal(value: string): OfficeProposal | null {
  let data: unknown;
  try {
    data = JSON.parse(value);
  } catch {
    return null;
  }
  const parsed = v.safeParse(proposalSchema, data);
  return parsed.success ? parsed.output : null;
}

function revisionPath(draftId: string, revisionId: string) {
  return `/api/v1/composer/drafts/${encodeURIComponent(draftId)}/revisions/${encodeURIComponent(revisionId)}` as const;
}

export class OfficeEditApi {
  constructor(private readonly transport = new ApiTransport()) {}

  targets(
    draftId: string,
    revisionId: string,
    format: OfficeFormat,
    offset = 0,
    signal?: AbortSignal,
  ): Promise<OfficeTextTargets> {
    return this.transport.json(
      `${revisionPath(draftId, revisionId)}/${format}-text-targets?limit=200&offset=${offset}`,
      targetListSchema,
      { signal },
    );
  }

  edit(
    draftId: string,
    revisionId: string,
    format: OfficeFormat,
    etag: string,
    key: string,
    operations: OfficeReplacement[],
    signal?: AbortSignal,
  ): Promise<JsonResult<OfficeEditRevision>> {
    return this.transport.jsonWithMetadata(
      `${revisionPath(draftId, revisionId)}/${format}-edits`,
      revisionSchema,
      {
        body: JSON.stringify({
          operations: operations.map((item) => ({
            op: "replace_text",
            ...item,
          })),
        }),
        csrf: true,
        etag,
        idempotencyKey: key,
        method: "POST",
        signal,
      },
    );
  }

  previewStep(
    draftId: string,
    revisionId: string,
    etag: string,
    request: OfficeStepRequest,
    signal?: AbortSignal,
  ): Promise<OfficeStepPreview> {
    return this.transport.json(
      `${revisionPath(draftId, revisionId)}/office-edit-steps/preview`,
      stepPreviewSchema,
      {
        body: JSON.stringify(request),
        csrf: true,
        etag,
        method: "POST",
        signal,
      },
    );
  }

  startStep(
    draftId: string,
    revisionId: string,
    etag: string,
    key: string,
    request: OfficeStepRequest & { preview_digest: string },
    signal?: AbortSignal,
  ): Promise<OfficeStep> {
    return this.transport.json(
      `${revisionPath(draftId, revisionId)}/office-edit-steps`,
      stepSchema,
      {
        body: JSON.stringify(request),
        csrf: true,
        etag,
        idempotencyKey: key,
        method: "POST",
        signal,
      },
    );
  }

  step(draftId: string, stepId: string, signal?: AbortSignal) {
    return this.transport.json(
      `/api/v1/composer/drafts/${encodeURIComponent(draftId)}/model-steps/${encodeURIComponent(stepId)}`,
      stepSchema,
      { signal },
    );
  }

  publishProposal(
    draftId: string,
    proposalId: string,
    etag: string,
    key: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<OfficeEditRevision>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/drafts/${encodeURIComponent(draftId)}/proposals/${encodeURIComponent(proposalId)}/office-edits`,
      revisionSchema,
      {
        body: JSON.stringify({}),
        csrf: true,
        etag,
        idempotencyKey: key,
        method: "POST",
        signal,
      },
    );
  }
}
