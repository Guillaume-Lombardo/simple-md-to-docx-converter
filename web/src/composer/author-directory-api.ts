import * as v from "valibot";
import { ApiTransport, type JsonResult } from "../api/transport";

const fieldSchema = v.object({
  value: v.nullable(v.string()),
  provenance: v.picklist([
    "supplied",
    "cited",
    "model_suggested",
    "human_approved",
    "human_edited",
    "unresolved",
  ]),
  source_reference: v.optional(v.nullable(v.string())),
});
const authorSchema = v.object({
  id: v.string(),
  owner_id: v.string(),
  name: v.string(),
  fields: v.record(v.string(), fieldSchema),
  version: v.number(),
  shared_with: v.array(v.string()),
});
const listSchema = v.object({
  authors: v.array(authorSchema),
  limit: v.number(),
  offset: v.number(),
});

export type AuthorField = v.InferOutput<typeof fieldSchema>;
export type AuthorEntry = v.InferOutput<typeof authorSchema>;
export type AuthorInput = Pick<AuthorEntry, "name" | "fields">;

export class AuthorDirectoryApi {
  constructor(private readonly transport = new ApiTransport()) {}

  async list(offset = 0, signal?: AbortSignal): Promise<AuthorEntry[]> {
    const response = await this.transport.json(
      `/api/v1/composer/authors?limit=100&offset=${offset}`,
      listSchema,
      { signal },
    );
    return response.authors;
  }

  get(id: string, signal?: AbortSignal): Promise<JsonResult<AuthorEntry>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/authors/${encodeURIComponent(id)}`,
      authorSchema,
      { signal },
    );
  }

  create(input: AuthorInput, signal?: AbortSignal): Promise<AuthorEntry> {
    return this.transport.json("/api/v1/composer/authors", authorSchema, {
      method: "POST",
      csrf: true,
      body: JSON.stringify(input),
      signal,
    });
  }

  update(
    id: string,
    etag: string,
    input: AuthorInput,
    signal?: AbortSignal,
  ): Promise<JsonResult<AuthorEntry>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/authors/${encodeURIComponent(id)}`,
      authorSchema,
      {
        method: "PATCH",
        csrf: true,
        etag,
        body: JSON.stringify(input),
        signal,
      },
    );
  }

  grant(
    id: string,
    userId: string,
    etag: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<AuthorEntry>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/authors/${encodeURIComponent(id)}/grants/${encodeURIComponent(userId)}`,
      authorSchema,
      { method: "PUT", csrf: true, etag, signal },
    );
  }

  revoke(
    id: string,
    userId: string,
    etag: string,
    signal?: AbortSignal,
  ): Promise<JsonResult<AuthorEntry>> {
    return this.transport.jsonWithMetadata(
      `/api/v1/composer/authors/${encodeURIComponent(id)}/grants/${encodeURIComponent(userId)}`,
      authorSchema,
      { method: "DELETE", csrf: true, etag, signal },
    );
  }
}
