"use client";

import { useEffect, useRef, useState } from "react";
import { Alert } from "../../components/primitives";
import { ApiError } from "../api/transport";
import type { OfficeFormat } from "./office-edit-api";
import {
  imageBase64,
  type OfficeStructure,
  type OfficeStructureApi,
  type OfficeStructureOperation,
} from "./office-structure-api";

const paragraphPage = 50;

function errorMessage(reason: unknown, expire: () => void): string {
  if (reason instanceof ApiError) {
    if (reason.status === 401) {
      expire();
      return "Your session ended. Please sign in again.";
    }
    if (reason.status === 404)
      return "This revision is no longer available. Refresh the draft.";
    if ([409, 412].includes(reason.status))
      return "The draft or document changed. Reload the current revision before retrying.";
    if (reason.code.endsWith("_EDIT_TARGET_CHANGED"))
      return "A style, slide, or image changed since it was loaded. Reload the current revision.";
    if (reason.code.endsWith("_EDIT_INVALID_IMAGE"))
      return "The replacement must be a valid image in the same format as the original.";
    if (reason.code === "UPLOAD_MALWARE_DETECTED")
      return "The replacement image was rejected by the malware scanner.";
    if (reason.code.endsWith("_EDIT_LIMIT_EXCEEDED") || reason.status === 413)
      return "The document or image exceeds the configured editing limits.";
    if (reason.status === 422) return "This structure cannot be edited safely.";
    if (reason.status >= 500)
      return "The edit could not be completed. Try again.";
    return reason.message;
  }
  return "The edit could not be completed. Try again.";
}

/** Moves that turn `original` into `desired`, each slide moved at most once. */
export function slideMoves(
  original: number[],
  desired: number[],
): OfficeStructureOperation[] {
  const order = [...original];
  const moves: OfficeStructureOperation[] = [];
  desired.forEach((slideId, index) => {
    const current = order.indexOf(slideId);
    if (current === index) return;
    order.splice(current, 1);
    order.splice(index, 0, slideId);
    moves.push({
      op: "move_slide",
      slide_id: slideId,
      expected_position: current + 1,
      position: index + 1,
    });
  });
  return moves;
}

export function ComposerOfficeStructurePanel({
  api,
  draftEtag,
  draftId,
  expire,
  format,
  onPublished,
  revisionId,
}: {
  api: OfficeStructureApi;
  draftEtag: string;
  draftId: string;
  expire: () => void;
  format: OfficeFormat;
  onPublished: (revisionId: string) => void;
  revisionId: string;
}) {
  const [structure, setStructure] = useState<OfficeStructure | null>(null);
  const [styles, setStyles] = useState<Record<string, string>>({});
  const [order, setOrder] = useState<number[]>([]);
  const [images, setImages] = useState<Record<string, File>>({});
  const [shown, setShown] = useState(paragraphPage);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    api
      .structure(draftId, revisionId, format, controller.signal)
      .then((value) => {
        setStructure(value);
        setOrder(value.slides.map((item) => item.slide_id));
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(reason, expire));
      });
    return () => {
      mounted.current = false;
      controller.abort();
    };
  }, [api, draftId, expire, format, revisionId]);

  if (!structure)
    return (
      <section aria-label="Office structure editing">
        {error ? (
          <Alert tone="danger">{error}</Alert>
        ) : (
          <p>Loading document structure…</p>
        )}
      </section>
    );

  const styleChanges = structure.paragraphs.filter(
    (item) => styles[item.target] && styles[item.target] !== item.style,
  );
  const moves = slideMoves(
    structure.slides.map((item) => item.slide_id),
    order,
  );
  const imageChanges = structure.images.filter((item) => images[item.target]);
  const changes = styleChanges.length + moves.length + imageChanges.length;
  const titles = new Map(
    structure.slides.map((item) => [item.slide_id, item.title]),
  );

  function move(index: number, offset: number) {
    setOrder((previous) => {
      const next = [...previous];
      const [slide] = next.splice(index, 1);
      next.splice(index + offset, 0, slide as number);
      return next;
    });
  }

  async function publish() {
    if (!structure) return;
    setBusy(true);
    setError("");
    try {
      const operations: OfficeStructureOperation[] = [
        ...styleChanges.map(
          (item): OfficeStructureOperation => ({
            op: "set_paragraph_style",
            target: item.target,
            expected_style: item.style,
            style: styles[item.target] as string,
          }),
        ),
        ...moves,
      ];
      for (const item of imageChanges) {
        const file = images[item.target] as File;
        operations.push({
          op: "replace_image",
          target: item.target,
          expected_sha256: item.sha256,
          filename: file.name,
          content_base64: await imageBase64(file),
        });
      }
      const result = await api.edit(
        draftId,
        revisionId,
        format,
        draftEtag,
        crypto.randomUUID(),
        operations,
      );
      if (mounted.current) onPublished(result.data.id);
    } catch (reason) {
      if (mounted.current) setError(errorMessage(reason, expire));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  return (
    <section
      aria-label="Office structure editing"
      className="space-y-3 rounded-control border border-muted p-4"
    >
      <h3 className="font-semibold">Edit document structure</h3>
      <p>
        Apply existing paragraph styles, reorder slides, or replace images.
        Every change creates a new revision; nothing else in the file changes.
        Replacement images are scanned and re-encoded without metadata.
      </p>
      {error && <Alert tone="danger">{error}</Alert>}
      {structure.paragraphs.length > 0 && (
        <fieldset className="space-y-2">
          <legend>Paragraph styles</legend>
          <ol className="max-h-[24rem] space-y-2 overflow-auto">
            {structure.paragraphs.slice(0, shown).map((item) => (
              <li key={item.target}>
                <label className="grid gap-1">
                  <span>
                    Paragraph {item.paragraph + 1}
                    {item.in_table ? " (table)" : ""}: {item.text || "(empty)"}
                  </span>
                  <select
                    aria-label={`Style of paragraph ${item.paragraph + 1}: ${item.text}`}
                    disabled={busy}
                    value={styles[item.target] ?? item.style ?? ""}
                    onChange={(event) =>
                      setStyles((previous) => ({
                        ...previous,
                        [item.target]: event.target.value,
                      }))
                    }
                  >
                    {item.style === null && (
                      <option value="">Default paragraph style</option>
                    )}
                    {structure.paragraph_styles.map((style) => (
                      <option key={style.style_id} value={style.style_id}>
                        {style.name}
                      </option>
                    ))}
                  </select>
                </label>
              </li>
            ))}
          </ol>
          {shown < structure.paragraphs.length && (
            <button
              type="button"
              onClick={() => setShown((value) => value + paragraphPage)}
            >
              Show more paragraphs
            </button>
          )}
        </fieldset>
      )}
      {order.length > 0 && (
        <fieldset className="space-y-2">
          <legend>Slide order</legend>
          <ol aria-label="Slide order" className="space-y-1">
            {order.map((slideId, index) => {
              const title = titles.get(slideId) || `Slide ${slideId}`;
              return (
                <li key={slideId} className="flex flex-wrap items-center gap-2">
                  <span>
                    {index + 1}. {title}
                  </span>
                  <button
                    aria-label={`Move “${title}” up`}
                    disabled={busy || index === 0}
                    onClick={() => move(index, -1)}
                    type="button"
                  >
                    Up
                  </button>
                  <button
                    aria-label={`Move “${title}” down`}
                    disabled={busy || index === order.length - 1}
                    onClick={() => move(index, 1)}
                    type="button"
                  >
                    Down
                  </button>
                </li>
              );
            })}
          </ol>
        </fieldset>
      )}
      {structure.images.length > 0 && (
        <fieldset className="space-y-2">
          <legend>Images</legend>
          <ul className="space-y-2">
            {structure.images.map((item) => (
              <li key={item.target}>
                <label className="grid gap-1">
                  <span>
                    {item.target.split("/").pop()} ({item.media_type}, used by{" "}
                    {item.used_by.length} part
                    {item.used_by.length === 1 ? "" : "s"})
                  </span>
                  <input
                    accept={item.media_type}
                    aria-label={`Replacement for ${item.target}`}
                    disabled={busy}
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      setImages((previous) => {
                        const next = { ...previous };
                        if (file) next[item.target] = file;
                        else delete next[item.target];
                        return next;
                      });
                    }}
                    type="file"
                  />
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
      )}
      {structure.paragraphs.length === 0 &&
        order.length === 0 &&
        structure.images.length === 0 && (
          <p>This revision has no editable structure.</p>
        )}
      <button
        type="button"
        disabled={busy || changes === 0}
        onClick={() => void publish()}
      >
        Publish structure edits ({changes})
      </button>
    </section>
  );
}
