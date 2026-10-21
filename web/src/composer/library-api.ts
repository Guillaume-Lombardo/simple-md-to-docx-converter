import * as v from "valibot";
import { ApiTransport, type JsonResult } from "../api/transport";

const BASE = "/api/v1/composer/library";

const documentSchema = v.object({
  id: v.string(),
  owner_id: v.string(),
  title: v.string(),
  version: v.number(),
  etag: v.string(),
  current_version_id: v.string(),
  shared_with: v.array(v.string()),
  created_at: v.string(),
  updated_at: v.string(),
});
const documentsSchema = v.object({
  documents: v.array(documentSchema),
  limit: v.number(),
  offset: v.number(),
});
const versionSchema = v.object({
  id: v.string(),
  document_id: v.string(),
  number: v.number(),
  sha256: v.string(),
  size: v.number(),
  media_type: v.string(),
  chunk_count: v.number(),
  created_at: v.string(),
});
const versionsSchema = v.object({
  versions: v.array(versionSchema),
  limit: v.number(),
  offset: v.number(),
});
const hitSchema = v.object({
  document_id: v.string(),
  title: v.string(),
  version_id: v.string(),
  version_number: v.number(),
  chunk: v.number(),
  score: v.number(),
  snippet: v.string(),
});
export const libraryExcerptSchema = v.object({
  document_id: v.string(),
  title: v.string(),
  version_id: v.string(),
  version_number: v.number(),
  version_sha256: v.string(),
  chunk: v.number(),
  text: v.string(),
});
export const libraryCitationSchema = v.object({
  version_id: v.string(),
  chunk: v.number(),
});

export type LibraryDocument = v.InferOutput<typeof documentSchema>;
export type LibraryVersion = v.InferOutput<typeof versionSchema>;
export type LibrarySearchHit = v.InferOutput<typeof hitSchema>;
export type LibraryExcerpt = v.InferOutput<typeof libraryExcerptSchema>;
export type LibraryCitation = v.InferOutput<typeof libraryCitationSchema>;

export const LIBRARY_ACCEPT = ".md,.txt,.docx,.pptx";
export const MAXIMUM_CITATIONS = 20;

const path = (id: string) => `${BASE}/${encodeURIComponent(id)}` as const;

/** Owner-scoped library client; every read is authorized by the service. */
export class LibraryApi {
  constructor(private readonly transport = new ApiTransport()) {}

  async list(
    query = "",
    offset = 0,
    signal?: AbortSignal,
  ): Promise<LibraryDocument[]> {
    const search = new URLSearchParams({
      limit: "100",
      offset: String(offset),
      ...(query.trim() ? { query: query.trim() } : {}),
    });
    const result = await this.transport.json(
      `${BASE}?${search.toString()}`,
      documentsSchema,
      { signal },
    );
    return result.documents;
  }

  upload(file: File, title: string, signal?: AbortSignal) {
    const form = new FormData();
    if (title.trim()) form.set("title", title.trim());
    form.set("file", file);
    return this.transport.multipart(BASE, form, documentSchema, {
      csrf: true,
      signal,
    });
  }

  replace(
    document: LibraryDocument,
    file: File,
    signal?: AbortSignal,
  ): Promise<JsonResult<LibraryDocument>> {
    const form = new FormData();
    form.set("file", file);
    return this.transport.multipartWithMetadata(
      `${path(document.id)}/versions`,
      form,
      documentSchema,
      { csrf: true, etag: document.etag, signal },
    );
  }

  async versions(id: string, signal?: AbortSignal): Promise<LibraryVersion[]> {
    const result = await this.transport.json(
      `${path(id)}/versions?limit=100&offset=0`,
      versionsSchema,
      { signal },
    );
    return result.versions;
  }

  download(id: string, versionId: string, signal?: AbortSignal) {
    return this.transport.download(
      `${path(id)}/versions/${encodeURIComponent(versionId)}/content`,
      { signal },
    );
  }

  grant(document: LibraryDocument, userId: string, signal?: AbortSignal) {
    return this.transport.json(
      `${path(document.id)}/grants/${encodeURIComponent(userId)}`,
      documentSchema,
      { method: "PUT", csrf: true, etag: document.etag, signal },
    );
  }

  revoke(document: LibraryDocument, userId: string, signal?: AbortSignal) {
    return this.transport.json(
      `${path(document.id)}/grants/${encodeURIComponent(userId)}`,
      documentSchema,
      { method: "DELETE", csrf: true, etag: document.etag, signal },
    );
  }

  async remove(document: LibraryDocument, signal?: AbortSignal) {
    await this.transport.json(path(document.id), v.undefined(), {
      method: "DELETE",
      csrf: true,
      etag: document.etag,
      signal,
    });
  }

  async search(
    query: string,
    limit = 10,
    signal?: AbortSignal,
  ): Promise<LibrarySearchHit[]> {
    const result = await this.transport.json(
      `${BASE}/search`,
      v.object({ results: v.array(hitSchema) }),
      {
        method: "POST",
        csrf: true,
        body: JSON.stringify({ query, limit }),
        signal,
      },
    );
    return result.results;
  }

  async excerpts(
    references: LibraryCitation[],
    signal?: AbortSignal,
  ): Promise<LibraryExcerpt[]> {
    const result = await this.transport.json(
      `${BASE}/excerpts`,
      v.object({ excerpts: v.array(libraryExcerptSchema) }),
      {
        method: "POST",
        csrf: true,
        body: JSON.stringify({ references }),
        signal,
      },
    );
    return result.excerpts;
  }
}

/** Accept only an exact, non-sniffable library download with a safe filename. */
export async function readLibraryDownload(
  response: Response,
): Promise<{ blob: Blob; filename: string }> {
  const media = response.headers
    .get("content-type")
    ?.split(";", 1)[0]
    ?.trim()
    .toLowerCase();
  const extension = {
    "text/markdown": ".md",
    "text/plain": ".txt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
      ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation":
      ".pptx",
  }[media ?? ""];
  const disposition = response.headers.get("content-disposition") ?? "";
  const filename = /filename="([^"/\\]+)"/.exec(disposition)?.[1];
  if (
    !extension ||
    !filename?.toLowerCase().endsWith(extension) ||
    response.headers.get("x-content-type-options")?.toLowerCase() !== "nosniff"
  )
    throw new TypeError("Unexpected library download response.");
  return { blob: await response.blob(), filename };
}
