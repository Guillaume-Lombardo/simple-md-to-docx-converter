import * as v from "valibot";
import { ApiTransport, type JsonResult } from "../api/transport";
import type { OfficeEditRevision, OfficeFormat } from "./office-edit-api";

const structureSchema = v.object({
  format: v.picklist(["docx", "pptx"]),
  revision_id: v.string(),
  download_sha256: v.string(),
  engine_version: v.number(),
  paragraphs: v.array(
    v.object({
      target: v.string(),
      paragraph: v.number(),
      in_table: v.boolean(),
      style: v.nullable(v.string()),
      text: v.string(),
    }),
  ),
  paragraph_styles: v.array(
    v.object({ style_id: v.string(), name: v.string() }),
  ),
  slides: v.array(
    v.object({ slide_id: v.number(), position: v.number(), title: v.string() }),
  ),
  images: v.array(
    v.object({
      target: v.string(),
      media_type: v.string(),
      sha256: v.string(),
      size: v.number(),
      used_by: v.array(v.string()),
    }),
  ),
});
const revisionSchema = v.object({
  id: v.string(),
  draft_id: v.string(),
  number: v.number(),
  operation: v.string(),
});

export type OfficeStructure = v.InferOutput<typeof structureSchema>;
export type OfficeStructureOperation =
  | {
      op: "set_paragraph_style";
      target: string;
      expected_style: string | null;
      style: string;
    }
  | {
      op: "move_slide";
      slide_id: number;
      expected_position: number;
      position: number;
    }
  | {
      op: "replace_image";
      target: string;
      expected_sha256: string;
      filename: string;
      content_base64: string;
    };

function revisionPath(draftId: string, revisionId: string) {
  return `/api/v1/composer/drafts/${encodeURIComponent(draftId)}/revisions/${encodeURIComponent(revisionId)}` as const;
}

/** Encode a replacement image for the JSON structure-edit request. */
export async function imageBase64(file: Blob): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let index = 0; index < bytes.length; index += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  return btoa(binary);
}

export class OfficeStructureApi {
  constructor(private readonly transport = new ApiTransport()) {}

  structure(
    draftId: string,
    revisionId: string,
    format: OfficeFormat,
    signal?: AbortSignal,
  ): Promise<OfficeStructure> {
    return this.transport.json(
      `${revisionPath(draftId, revisionId)}/${format}-structure`,
      structureSchema,
      { signal },
    );
  }

  edit(
    draftId: string,
    revisionId: string,
    format: OfficeFormat,
    etag: string,
    key: string,
    operations: OfficeStructureOperation[],
    signal?: AbortSignal,
  ): Promise<JsonResult<OfficeEditRevision>> {
    return this.transport.jsonWithMetadata(
      `${revisionPath(draftId, revisionId)}/${format}-structure-edits`,
      revisionSchema,
      {
        body: JSON.stringify({ operations }),
        csrf: true,
        etag,
        idempotencyKey: key,
        method: "POST",
        signal,
      },
    );
  }
}
