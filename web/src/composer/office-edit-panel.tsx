"use client";

import { useEffect, useRef, useState } from "react";
import { Alert } from "../../components/primitives";
import { ApiError } from "../api/transport";
import {
  OfficeEditApi,
  type OfficeFormat,
  type OfficeStepPreview,
  type OfficeStepRequest,
  type OfficeTextTarget,
} from "./office-edit-api";

const stepPollMilliseconds = 500;
const stepPollAttempts = 120;

export interface OfficeModelChoice {
  connectionId: string;
  endpoint: string;
  model: string;
  maxOutputTokens: number;
}

function targetLabel(target: OfficeTextTarget): string {
  const place =
    target.slide === undefined
      ? `Paragraph ${target.paragraph + 1}`
      : `Slide ${target.slide}${target.notes ? " notes" : ""}, paragraph ${target.paragraph + 1}`;
  return target.in_table ? `${place} (table)` : place;
}

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
      return "A text changed since it was loaded. Reload the current revision.";
    if (reason.code.endsWith("_EDIT_LIMIT_EXCEEDED") || reason.status === 413)
      return "The document exceeds the configured editing limits.";
    if (reason.status === 422)
      return "This text cannot be edited safely. It may belong to a field, tracked change, or unsupported structure.";
    if (reason.status >= 500)
      return "The edit could not be completed. Try again.";
    return reason.message;
  }
  return "The edit could not be completed. Try again.";
}

function stepFailure(code: string | null): string {
  if (code === "provider_invalid")
    return "The model reply was not a valid text edit and was discarded.";
  if (code === "provider_unavailable" || code === "capacity_exhausted")
    return "The model is temporarily unavailable. Try again later.";
  return "The model step did not produce a suggestion.";
}

export function ComposerOfficeEditPanel({
  api,
  draftEtag,
  draftId,
  expire,
  format,
  model,
  onProposalReady,
  onPublished,
  revisionId,
}: {
  api: OfficeEditApi;
  draftEtag: string;
  draftId: string;
  expire: () => void;
  format: OfficeFormat;
  model: OfficeModelChoice | null;
  onProposalReady: () => void;
  onPublished: (revisionId: string) => void;
  revisionId: string;
}) {
  const [targets, setTargets] = useState<OfficeTextTarget[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [shared, setShared] = useState<string[]>([]);
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState<{
    fingerprint: string;
    value: OfficeStepPreview;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    api
      .targets(draftId, revisionId, format, 0, controller.signal)
      .then((page) => {
        setTargets(page.targets);
        setTotal(page.total);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(reason, expire));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      mounted.current = false;
      controller.abort();
    };
  }, [api, draftId, expire, format, revisionId]);

  const changed = targets.filter(
    (item) =>
      edits[item.target] !== undefined && edits[item.target] !== item.text,
  );
  const request: OfficeStepRequest | null =
    model && shared.length > 0 && instruction.trim()
      ? {
          connection_id: model.connectionId,
          approved_endpoint: model.endpoint,
          approved_model: model.model,
          instruction: instruction.trim(),
          targets: shared,
          max_output_tokens: model.maxOutputTokens,
        }
      : null;
  const fingerprint = JSON.stringify({ request, draftEtag, revisionId });
  const currentPreview =
    preview && preview.fingerprint === fingerprint ? preview.value : null;

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (reason) {
      if (mounted.current) setError(errorMessage(reason, expire));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  async function loadMore() {
    await run(async () => {
      const page = await api.targets(
        draftId,
        revisionId,
        format,
        targets.length,
      );
      setTargets((previous) => [...previous, ...page.targets]);
      setTotal(page.total);
    });
  }

  async function publish() {
    await run(async () => {
      const result = await api.edit(
        draftId,
        revisionId,
        format,
        draftEtag,
        crypto.randomUUID(),
        changed.map((item) => ({
          target: item.target,
          expected_text: item.text,
          text: edits[item.target] ?? item.text,
        })),
      );
      if (!mounted.current) return;
      setNotice(`Text edits published as revision ${result.data.number}.`);
      onPublished(result.data.id);
    });
  }

  async function previewRequest() {
    if (!request) return;
    await run(async () => {
      const value = await api.previewStep(
        draftId,
        revisionId,
        draftEtag,
        request,
      );
      if (mounted.current) setPreview({ fingerprint, value });
    });
  }

  async function send() {
    if (!request || !currentPreview) return;
    await run(async () => {
      let step = await api.startStep(
        draftId,
        revisionId,
        draftEtag,
        crypto.randomUUID(),
        { ...request, preview_digest: currentPreview.preview_digest },
      );
      setNotice("Model step queued for review.");
      for (
        let attempt = 0;
        step.status === "running" && attempt < stepPollAttempts;
        attempt += 1
      ) {
        await new Promise((resolve) =>
          setTimeout(resolve, stepPollMilliseconds),
        );
        if (!mounted.current) return;
        step = await api.step(draftId, step.id);
      }
      if (!mounted.current) return;
      if (step.status === "completed" && step.proposal_id) {
        setNotice(
          "The model suggestion is ready for review in the proposals list.",
        );
        onProposalReady();
      } else if (step.status === "running") {
        setNotice("The model step is still running. Refresh the draft later.");
      } else {
        setNotice("");
        setError(stepFailure(step.error_code));
      }
    });
  }

  return (
    <section
      aria-label="Office text editing"
      className="space-y-3 rounded-control border border-muted p-4"
    >
      <h3 className="font-semibold">
        Edit {format === "pptx" ? "slide and notes" : "document"} text
      </h3>
      <p>
        Each change replaces the whole text of one run and creates a new
        revision. Formatting, images, and every other part of the file stay
        unchanged. Fields, tracked changes, and content controls are not listed.
      </p>
      {error && <Alert tone="danger">{error}</Alert>}
      {notice && <p role="status">{notice}</p>}
      {loading ? (
        <p>Loading editable text…</p>
      ) : targets.length === 0 ? (
        <p>This revision has no editable text.</p>
      ) : (
        <ol className="max-h-[32rem] space-y-2 overflow-auto">
          {targets.map((item) => (
            <li key={item.target} className="grid gap-1">
              <label className="grid gap-1">
                <span>{targetLabel(item)}</span>
                <input
                  aria-label={`${targetLabel(item)}: ${item.text}`}
                  disabled={busy}
                  value={edits[item.target] ?? item.text}
                  onChange={(event) =>
                    setEdits((previous) => ({
                      ...previous,
                      [item.target]: event.target.value,
                    }))
                  }
                />
              </label>
              {model && (
                <label>
                  <input
                    checked={shared.includes(item.target)}
                    disabled={busy}
                    onChange={(event) =>
                      setShared((previous) =>
                        event.target.checked
                          ? [...previous, item.target]
                          : previous.filter((value) => value !== item.target),
                      )
                    }
                    type="checkbox"
                  />{" "}
                  Share “{item.text}” with the model
                </label>
              )}
            </li>
          ))}
        </ol>
      )}
      {targets.length < total && (
        <button type="button" disabled={busy} onClick={() => void loadMore()}>
          Load more text
        </button>
      )}
      <button
        type="button"
        disabled={busy || changed.length === 0}
        onClick={() => void publish()}
      >
        Publish text edits ({changed.length})
      </button>
      {model ? (
        <div className="grid gap-2">
          <label className="grid gap-1">
            Instruction for the model
            <textarea
              className="min-h-16 rounded-control border border-muted p-2"
              disabled={busy}
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy || !request}
              onClick={() => void previewRequest()}
            >
              Preview model request
            </button>
            <button
              type="button"
              disabled={busy || !currentPreview}
              onClick={() => void send()}
            >
              Send shared text to the model
            </button>
          </div>
          {currentPreview && (
            <>
              <p>Exact text selected for transmission:</p>
              <pre className="max-h-64 overflow-auto rounded-control border border-muted p-2 whitespace-pre-wrap">
                {currentPreview.transmitted_content}
              </pre>
            </>
          )}
        </div>
      ) : (
        <p>
          Select an available connection and model to ask for suggested text
          changes.
        </p>
      )}
    </section>
  );
}
