import * as v from "valibot";
import { ApiTransport, type JsonResult } from "../api/transport";
import { ComposerConnectionsApi } from "./api";

const draftSchema = v.object({
  id: v.string(),
  title: v.string(),
  content: v.string(),
  version: v.number(),
  current_revision_id: v.nullable(v.string()),
  source_kind: v.string(),
  source_media_type: v.string(),
  created_at: v.string(),
  updated_at: v.string(),
  etag: v.string(),
});
const draftSummarySchema = v.omit(draftSchema, ["content", "created_at"]);
const messageSchema = v.object({
  id: v.string(),
  draft_id: v.string(),
  role: v.string(),
  content: v.string(),
  created_at: v.string(),
});
const proposalSchema = v.object({
  id: v.string(),
  draft_id: v.string(),
  base_version: v.number(),
  state: v.string(),
  proposed_value: v.string(),
  decided_value: v.nullable(v.string()),
  provenance: v.string(),
  created_at: v.string(),
  decided_at: v.nullable(v.string()),
  decided_by: v.nullable(v.string()),
});
const stepSchema = v.object({
  id: v.string(),
  draft_id: v.string(),
  connection_id: v.string(),
  model_identity: v.string(),
  intent: v.picklist(["proposal", "question"]),
  base_version: v.number(),
  status: v.string(),
  proposal_id: v.nullable(v.string()),
  question_id: v.nullable(v.string()),
  answered_question_id: v.nullable(v.string()),
  error_code: v.nullable(v.string()),
  created_at: v.string(),
  updated_at: v.string(),
});
const promptPreviewSchema = v.object({
  transmitted_content: v.string(),
  author_refs: v.array(v.unknown()),
  preview_digest: v.string(),
});
const questionSchema = v.object({
  id: v.string(),
  draft_id: v.string(),
  model_step_id: v.string(),
  base_version: v.number(),
  state: v.picklist(["pending", "answered"]),
  text: v.string(),
  answer_message_id: v.nullable(v.string()),
  answer_content: v.nullable(v.string()),
  source_author_ids: v.optional(v.array(v.string())),
  created_at: v.string(),
  answered_at: v.nullable(v.string()),
});
const artifactSchema = v.object({
  kind: v.string(),
  sha256: v.string(),
  size: v.number(),
  media_type: v.string(),
});
const revisionSchema = v.object({
  id: v.string(),
  draft_id: v.string(),
  number: v.number(),
  operation: v.string(),
  provenance: v.string(),
  source_sha256: v.string(),
  template_reference: v.nullable(v.string()),
  approved_values: v.string(),
  render_options: v.string(),
  model_identity: v.nullable(v.string()),
  artifacts: v.array(artifactSchema),
  restored_from_revision_id: v.nullable(v.string()),
  created_at: v.string(),
});
const revisionSummarySchema = v.pick(revisionSchema, [
  "id",
  "draft_id",
  "number",
  "operation",
  "provenance",
  "model_identity",
  "restored_from_revision_id",
  "created_at",
]);
const diffSchema = v.object({
  from_revision_id: v.string(),
  to_revision_id: v.string(),
  status: v.picklist(["available", "unchanged", "unavailable"]),
  reason: v.nullable(v.string()),
  scope: v.picklist(["artifact", "approved_markdown"]),
  metadata_changes: v.array(v.string()),
  changes: v.array(
    v.object({
      kind: v.string(),
      before_start: v.number(),
      before_end: v.number(),
      after_start: v.number(),
      after_end: v.number(),
      before_text: v.string(),
      after_text: v.string(),
    }),
  ),
});
const generationSchema = v.object({
  id: v.string(),
  draft_id: v.string(),
  source_revision_id: v.string(),
  job_id: v.nullable(v.string()),
  status: v.string(),
  output: v.picklist(["docx", "pdf", "pptx"]),
  template_id: v.nullable(v.string()),
  template_version_id: v.nullable(v.string()),
  presentation_dialect: v.nullable(v.picklist(["auto", "markdown", "marp"])),
  slide_level: v.nullable(v.number()),
  result_revision_id: v.nullable(v.string()),
  publishable: v.boolean(),
  created_at: v.string(),
});
const templateSchema = v.object({
  id: v.string(),
  name: v.string(),
  description: v.string(),
  current_version_id: v.nullable(v.string()),
  kind: v.optional(v.string()),
});
const conversionOptionsSchema = v.object({
  resolved_template: v.nullable(templateSchema),
  template_version_id: v.nullable(v.string()),
  selection_source: v.string(),
});

export type ComposerDraft = v.InferOutput<typeof draftSchema>;
export type ComposerDraftSummary = v.InferOutput<typeof draftSummarySchema>;
export type ComposerMessage = v.InferOutput<typeof messageSchema>;
export type ComposerProposal = v.InferOutput<typeof proposalSchema>;
export type ComposerStep = v.InferOutput<typeof stepSchema>;
export type ComposerPromptPreview = v.InferOutput<typeof promptPreviewSchema>;
export type ComposerQuestion = v.InferOutput<typeof questionSchema>;
export type ComposerRevision = v.InferOutput<typeof revisionSchema>;
export type ComposerRevisionSummary = v.InferOutput<
  typeof revisionSummarySchema
>;
export type ComposerDiff = v.InferOutput<typeof diffSchema>;
export type ComposerGeneration = v.InferOutput<typeof generationSchema>;
export type ComposerTemplate = v.InferOutput<typeof templateSchema>;
export type ComposerConversionOptions = v.InferOutput<
  typeof conversionOptionsSchema
>;

const page = <T extends v.BaseSchema<unknown, unknown, v.BaseIssue<unknown>>>(
  key: string,
  item: T,
) => v.object({ [key]: v.array(item), limit: v.number(), offset: v.number() });

export class ComposerWorkspaceApi {
  readonly connections: ComposerConnectionsApi;

  constructor(private readonly transport = new ApiTransport()) {
    this.connections = new ComposerConnectionsApi(transport);
  }

  async drafts(
    signal?: AbortSignal,
    offset = 0,
  ): Promise<ComposerDraftSummary[]> {
    const result = await this.transport.json(
      `/api/v1/composer/drafts?limit=100&offset=${offset}`,
      page("drafts", draftSummarySchema),
      { signal },
    );
    return result.drafts as ComposerDraftSummary[];
  }

  draft(id: string, signal?: AbortSignal): Promise<ComposerDraft> {
    return this.transport.json(`/api/v1/composer/drafts/${id}`, draftSchema, {
      signal,
    });
  }

  createDraft(
    source: File,
    signal?: AbortSignal,
  ): Promise<JsonResult<ComposerDraft>> {
    const form = new FormData();
    form.set("source", source);
    return this.transport.multipartWithMetadata(
      "/api/v1/composer/drafts",
      form,
      draftSchema,
      { csrf: true, signal },
    );
  }

  saveDraft(
    draft: ComposerDraft,
    title: string,
    content: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<ComposerDraft>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/drafts/${draft.id}`,
      draftSchema,
      {
        body: JSON.stringify({ title, content }),
        csrf: true,
        etag: draft.etag,
        method: "PUT",
        signal,
      },
    );
  }

  async messages(
    id: string,
    signal?: AbortSignal,
    offset = 0,
  ): Promise<ComposerMessage[]> {
    const result = await this.transport.json(
      `/api/v1/composer/drafts/${id}/messages?limit=100&offset=${offset}&order=desc`,
      page("messages", messageSchema),
      { signal },
    );
    return result.messages as ComposerMessage[];
  }

  addMessage(
    draft: ComposerDraft,
    content: string,
    key: string,
    signal?: AbortSignal,
  ): Promise<ComposerMessage> {
    return this.transport.json(
      `/api/v1/composer/drafts/${draft.id}/messages`,
      messageSchema,
      {
        body: JSON.stringify({ content }),
        csrf: true,
        etag: draft.etag,
        idempotencyKey: key,
        method: "POST",
        signal,
      },
    );
  }

  async proposals(
    id: string,
    signal?: AbortSignal,
    offset = 0,
  ): Promise<ComposerProposal[]> {
    const result = await this.transport.json(
      `/api/v1/composer/drafts/${id}/proposals?limit=100&offset=${offset}&order=desc`,
      page("proposals", proposalSchema),
      { signal },
    );
    return result.proposals as ComposerProposal[];
  }

  async questions(
    id: string,
    signal?: AbortSignal,
    offset = 0,
  ): Promise<ComposerQuestion[]> {
    const result = await this.transport.json(
      `/api/v1/composer/drafts/${id}/questions?limit=100&offset=${offset}&order=desc`,
      page("questions", questionSchema),
      { signal },
    );
    return result.questions as ComposerQuestion[];
  }

  answerQuestion(
    draft: ComposerDraft,
    questionId: string,
    content: string,
    key: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<ComposerQuestion>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/drafts/${draft.id}/questions/${questionId}/answer`,
      questionSchema,
      {
        body: JSON.stringify({ content }),
        csrf: true,
        etag: draft.etag,
        idempotencyKey: key,
        method: "POST",
        signal,
      },
    );
  }

  decide(
    draft: ComposerDraft,
    proposalId: string,
    state: "accepted" | "edited" | "rejected",
    decidedValue?: string,
    signal?: AbortSignal,
  ): Promise<ComposerProposal> {
    return this.transport.json(
      `/api/v1/composer/drafts/${draft.id}/proposals/${proposalId}/decision`,
      proposalSchema,
      {
        body: JSON.stringify({
          state,
          ...(decidedValue === undefined
            ? {}
            : { decided_value: decidedValue }),
        }),
        csrf: true,
        etag: draft.etag,
        method: "POST",
        signal,
      },
    );
  }

  publishProposal(
    draft: ComposerDraft,
    proposalId: string,
    key: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<ComposerRevision>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/drafts/${draft.id}/proposals/${proposalId}/publish`,
      revisionSchema,
      {
        body: JSON.stringify({}),
        csrf: true,
        etag: draft.etag,
        idempotencyKey: key,
        method: "POST",
        signal,
      },
    );
  }

  startStep(
    draft: ComposerDraft,
    input: {
      connection_id: string;
      approved_endpoint: string;
      approved_model: string;
      content: string;
      max_output_tokens: number;
      intent: "proposal" | "question";
      answered_question_id?: string;
      author_refs?: unknown[];
      author_preview_digest?: string;
    },
    key: string,
    signal?: AbortSignal,
  ): Promise<ComposerStep> {
    return this.transport.json(
      `/api/v1/composer/drafts/${draft.id}/model-steps`,
      stepSchema,
      {
        body: JSON.stringify(input),
        csrf: true,
        etag: draft.etag,
        idempotencyKey: key,
        method: "POST",
        signal,
      },
    );
  }

  previewStep(
    draft: Pick<ComposerDraft, "id" | "etag">,
    input: {
      connection_id: string;
      approved_endpoint: string;
      approved_model: string;
      content: string;
      author_ids: string[];
      max_output_tokens: number;
    },
    signal?: AbortSignal,
  ): Promise<ComposerPromptPreview> {
    return this.transport.json(
      `/api/v1/composer/drafts/${encodeURIComponent(draft.id)}/model-steps/preview`,
      promptPreviewSchema,
      {
        body: JSON.stringify(input),
        csrf: true,
        etag: draft.etag,
        method: "POST",
        signal,
      },
    );
  }

  step(
    draftId: string,
    stepId: string,
    signal?: AbortSignal,
  ): Promise<ComposerStep> {
    return this.transport.json(
      `/api/v1/composer/drafts/${draftId}/model-steps/${stepId}`,
      stepSchema,
      { signal },
    );
  }

  cancelStep(
    draftId: string,
    stepId: string,
    signal?: AbortSignal,
  ): Promise<ComposerStep> {
    return this.transport.json(
      `/api/v1/composer/drafts/${draftId}/model-steps/${stepId}`,
      stepSchema,
      { csrf: true, method: "DELETE", signal },
    );
  }

  async revisions(
    id: string,
    signal?: AbortSignal,
    offset = 0,
  ): Promise<ComposerRevisionSummary[]> {
    const result = await this.transport.json(
      `/api/v1/composer/drafts/${id}/revisions?limit=100&offset=${offset}&order=desc`,
      page("revisions", revisionSummarySchema),
      { signal },
    );
    return result.revisions as ComposerRevisionSummary[];
  }

  revision(
    draftId: string,
    revisionId: string,
    signal?: AbortSignal,
  ): Promise<ComposerRevision> {
    return this.transport.json(
      `/api/v1/composer/drafts/${draftId}/revisions/${revisionId}`,
      revisionSchema,
      { signal },
    );
  }

  diff(
    draftId: string,
    revisionId: string,
    fromRevisionId: string,
    signal?: AbortSignal,
  ): Promise<ComposerDiff> {
    return this.transport.json(
      `/api/v1/composer/drafts/${draftId}/revisions/${revisionId}/diff?from_revision_id=${fromRevisionId}`,
      diffSchema,
      { signal },
    );
  }

  captureSource(
    draft: ComposerDraft,
    key: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<ComposerRevision>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/drafts/${draft.id}/revisions/from-source`,
      revisionSchema,
      {
        csrf: true,
        etag: draft.etag,
        idempotencyKey: key,
        method: "POST",
        signal,
      },
    );
  }

  publishDraft(
    draft: ComposerDraft,
    key: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<ComposerRevision>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/drafts/${draft.id}/revisions/from-draft`,
      revisionSchema,
      {
        csrf: true,
        etag: draft.etag,
        idempotencyKey: key,
        method: "POST",
        signal,
      },
    );
  }

  restore(
    draft: ComposerDraft,
    revisionId: string,
    key: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<ComposerRevision>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/drafts/${draft.id}/revisions/${revisionId}/restore`,
      revisionSchema,
      {
        csrf: true,
        etag: draft.etag,
        idempotencyKey: key,
        method: "POST",
        signal,
      },
    );
  }

  conversionOptions(
    output: "docx" | "pdf" | "pptx",
    signal?: AbortSignal,
  ): Promise<ComposerConversionOptions> {
    const family = output === "pptx" ? "?template_kind=pptx" : "";
    return this.transport.json(
      `/api/v1/conversion-options${family}`,
      conversionOptionsSchema,
      { signal },
    );
  }

  async templates(
    output: "docx" | "pdf" | "pptx",
    signal?: AbortSignal,
  ): Promise<ComposerTemplate[]> {
    const kind = output === "pptx" ? "pptx" : "docx";
    const response = await this.transport.json(
      `/api/v1/templates?status=active&kind=${kind}&offset=0&limit=100`,
      page("items", templateSchema),
      { signal },
    );
    return response.items as ComposerTemplate[];
  }

  startGeneration(
    draft: ComposerDraft,
    sourceRevisionId: string,
    input: {
      output: "docx" | "pdf" | "pptx";
      template_id: string | null;
      template_version_id: string | null;
      presentation_dialect: "auto" | "markdown" | "marp" | null;
      slide_level: number | null;
    },
    key: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<ComposerGeneration>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/drafts/${draft.id}/revisions/${sourceRevisionId}/generations`,
      generationSchema,
      {
        body: JSON.stringify(input),
        csrf: true,
        etag: draft.etag,
        idempotencyKey: key,
        method: "POST",
        signal,
      },
    );
  }

  generation(
    draftId: string,
    generationId: string,
    signal?: AbortSignal,
  ): Promise<ComposerGeneration> {
    return this.transport.json(
      `/api/v1/composer/drafts/${draftId}/generations/${generationId}`,
      generationSchema,
      { signal },
    );
  }

  async generations(
    draftId: string,
    signal?: AbortSignal,
  ): Promise<ComposerGeneration[]> {
    const result = await this.transport.json(
      `/api/v1/composer/drafts/${draftId}/generations?limit=20&offset=0`,
      page("generations", generationSchema),
      { signal },
    );
    return result.generations as ComposerGeneration[];
  }

  cancelGeneration(
    draftId: string,
    generationId: string,
    signal?: AbortSignal,
  ): Promise<ComposerGeneration> {
    return this.transport.json(
      `/api/v1/composer/drafts/${draftId}/generations/${generationId}`,
      generationSchema,
      { csrf: true, method: "DELETE", signal },
    );
  }

  publishGeneration(
    draft: ComposerDraft,
    generationId: string,
    key: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<ComposerRevision>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/drafts/${draft.id}/generations/${generationId}/publish`,
      revisionSchema,
      {
        csrf: true,
        etag: draft.etag,
        idempotencyKey: key,
        method: "POST",
        signal,
      },
    );
  }

  download(
    draftId: string,
    revisionId: string,
    kind: "download" | "preview",
    signal?: AbortSignal,
  ): Promise<Response> {
    return this.transport.download(
      `/api/v1/composer/drafts/${draftId}/revisions/${revisionId}/artifacts/${kind}`,
      { signal },
    );
  }
}
