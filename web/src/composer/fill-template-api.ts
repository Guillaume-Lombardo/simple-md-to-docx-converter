import * as v from "valibot";
import { ApiTransport, type JsonResult } from "../api/transport";

const fieldSchema = v.object({
  name: v.string(),
  type: v.picklist(["text", "date", "boolean", "integer"]),
  required: v.boolean(),
  constraints: v.record(v.string(), v.unknown()),
  default: v.optional(v.unknown()),
});
const repeatSchema = v.object({
  name: v.string(),
  min_items: v.number(),
  max_items: v.number(),
  fields: v.array(fieldSchema),
});
const schemaSchema = v.object({
  fields: v.array(fieldSchema),
  repeats: v.array(repeatSchema),
});
const templateSchema = v.object({
  id: v.string(),
  owner_id: v.string(),
  name: v.string(),
  version: v.number(),
  etag: v.string(),
  active_version_id: v.nullable(v.string()),
  shared_with: v.array(v.string()),
  created_at: v.string(),
  updated_at: v.string(),
});
const versionSchema = v.object({
  id: v.string(),
  template_id: v.string(),
  number: v.number(),
  schema_version: v.number(),
  schema: schemaSchema,
  schema_sha256: v.string(),
  docx_sha256: v.string(),
  size: v.number(),
  created_at: v.string(),
});
const listSchema = v.object({
  templates: v.array(templateSchema),
  limit: v.number(),
  offset: v.number(),
});
const versionsSchema = v.object({
  versions: v.array(versionSchema),
  limit: v.number(),
  offset: v.number(),
});
const questionSchema = v.object({
  path: v.string(),
  text: v.string(),
  reason: v.picklist(["missing", "ambiguous"]),
});
const provenanceSchema = v.object({
  kind: v.picklist([
    "supplied",
    "cited",
    "model_suggested",
    "human_approved",
    "human_edited",
    "unresolved",
    "template_default",
  ]),
  source_reference: v.optional(v.nullable(v.string())),
});
const planSchema = v.object({
  id: v.string(),
  draft_id: v.string(),
  source_revision_id: v.string(),
  template_id: v.string(),
  template_version_id: v.string(),
  author_refs: v.array(v.unknown()),
  version: v.number(),
  etag: v.string(),
  values: v.record(v.string(), v.unknown()),
  provenance: v.record(v.string(), provenanceSchema),
  questions: v.array(questionSchema),
  state: v.picklist(["pending", "approved", "published"]),
  result_revision_id: v.nullable(v.string()),
});
const plansSchema = v.object({
  plans: v.array(planSchema),
  limit: v.number(),
  offset: v.number(),
});

export type FillField = v.InferOutput<typeof fieldSchema>;
export type FillSchema = v.InferOutput<typeof schemaSchema>;
export type FillTemplate = v.InferOutput<typeof templateSchema>;
export type FillTemplateVersion = v.InferOutput<typeof versionSchema>;
export type FillPlan = v.InferOutput<typeof planSchema>;
export type FillProvenance = v.InferOutput<typeof provenanceSchema>;
export type FillValues = Record<string, unknown>;

const path = (id: string) =>
  `/api/v1/composer/fill-templates/${encodeURIComponent(id)}` as const;
const planPath = (draftId: string, planId: string) =>
  `/api/v1/composer/drafts/${encodeURIComponent(draftId)}/fill-plans/${encodeURIComponent(planId)}` as const;

export class FillTemplateApi {
  constructor(private readonly transport = new ApiTransport()) {}

  async list(offset = 0, signal?: AbortSignal): Promise<FillTemplate[]> {
    const result = await this.transport.json(
      `/api/v1/composer/fill-templates?limit=100&offset=${offset}`,
      listSchema,
      { signal },
    );
    return result.templates;
  }

  get(id: string, signal?: AbortSignal): Promise<JsonResult<FillTemplate>> {
    return this.transport.jsonWithMetadata(path(id), templateSchema, {
      signal,
    });
  }

  create(
    name: string,
    schema: FillSchema,
    file: File,
    signal?: AbortSignal,
  ): Promise<JsonResult<FillTemplate>> {
    const form = new FormData();
    form.set("name", name);
    form.set("schema", JSON.stringify(schema));
    form.set("file", file);
    return this.transport.multipartWithMetadata(
      "/api/v1/composer/fill-templates",
      form,
      templateSchema,
      { csrf: true, signal },
    );
  }

  version(
    id: string,
    versionId: string,
    signal?: AbortSignal,
  ): Promise<FillTemplateVersion> {
    return this.transport.json(
      `${path(id)}/versions/${encodeURIComponent(versionId)}`,
      versionSchema,
      { signal },
    );
  }

  async versions(id: string, offset = 0, signal?: AbortSignal) {
    const result = await this.transport.json(
      `${path(id)}/versions?limit=100&offset=${offset}`,
      versionsSchema,
      { signal },
    );
    return result.versions;
  }

  replace(
    id: string,
    etag: string,
    schema: FillSchema,
    file: File,
    signal?: AbortSignal,
  ): Promise<JsonResult<FillTemplate>> {
    const form = new FormData();
    form.set("schema", JSON.stringify(schema));
    form.set("file", file);
    return this.transport.multipartWithMetadata(
      `${path(id)}/versions`,
      form,
      templateSchema,
      {
        csrf: true,
        etag,
        signal,
      },
    );
  }

  download(id: string, versionId: string, signal?: AbortSignal) {
    return this.transport.download(
      `${path(id)}/versions/${encodeURIComponent(versionId)}/content`,
      { signal },
    );
  }

  grant(id: string, userId: string, etag: string, signal?: AbortSignal) {
    return this.transport.jsonWithMetadata(
      `${path(id)}/grants/${encodeURIComponent(userId)}`,
      templateSchema,
      { method: "PUT", csrf: true, etag, signal },
    );
  }

  revoke(id: string, userId: string, etag: string, signal?: AbortSignal) {
    return this.transport.jsonWithMetadata(
      `${path(id)}/grants/${encodeURIComponent(userId)}`,
      templateSchema,
      { method: "DELETE", csrf: true, etag, signal },
    );
  }

  createPlan(
    draftId: string,
    etag: string,
    key: string,
    input: {
      source_revision_id: string;
      template_id: string;
      template_version_id: string;
      author_ids: string[];
      values: FillValues;
    },
    signal?: AbortSignal,
  ): Promise<FillPlan> {
    return this.transport.json(
      `/api/v1/composer/drafts/${encodeURIComponent(draftId)}/fill-plans`,
      planSchema,
      {
        method: "POST",
        csrf: true,
        etag,
        idempotencyKey: key,
        body: JSON.stringify(input),
        signal,
      },
    );
  }

  getPlan(draftId: string, planId: string, signal?: AbortSignal) {
    return this.transport.json(planPath(draftId, planId), planSchema, {
      signal,
    });
  }

  async plans(draftId: string, offset = 0, signal?: AbortSignal) {
    const result = await this.transport.json(
      `/api/v1/composer/drafts/${encodeURIComponent(draftId)}/fill-plans?limit=100&offset=${offset}`,
      plansSchema,
      { signal },
    );
    return result.plans;
  }

  updatePlan(
    draftId: string,
    plan: FillPlan,
    values: FillValues,
    provenance: Record<string, FillProvenance>,
    key: string,
    signal?: AbortSignal,
  ) {
    return this.transport.json(planPath(draftId, plan.id), planSchema, {
      method: "PATCH",
      csrf: true,
      etag: plan.etag,
      idempotencyKey: key,
      body: JSON.stringify({ values, provenance }),
      signal,
    });
  }

  approve(draftId: string, plan: FillPlan, key: string, signal?: AbortSignal) {
    return this.transport.json(
      `${planPath(draftId, plan.id)}/approve`,
      planSchema,
      {
        method: "POST",
        csrf: true,
        etag: plan.etag,
        idempotencyKey: key,
        body: JSON.stringify({}),
        signal,
      },
    );
  }

  publish(draftId: string, plan: FillPlan, key: string, signal?: AbortSignal) {
    return this.transport.json(
      `${planPath(draftId, plan.id)}/publish`,
      v.object({ id: v.string(), number: v.number(), draft_id: v.string() }),
      {
        method: "POST",
        csrf: true,
        etag: plan.etag,
        idempotencyKey: key,
        body: JSON.stringify({}),
        signal,
      },
    );
  }

  regenerate(
    draftId: string,
    revisionId: string,
    etag: string,
    key: string,
    signal?: AbortSignal,
  ) {
    return this.transport.json(
      `/api/v1/composer/drafts/${encodeURIComponent(draftId)}/revisions/${encodeURIComponent(revisionId)}/regenerations`,
      v.object({ id: v.string(), number: v.number(), draft_id: v.string() }),
      {
        method: "POST",
        csrf: true,
        etag,
        idempotencyKey: key,
        body: JSON.stringify({}),
        signal,
      },
    );
  }
}
