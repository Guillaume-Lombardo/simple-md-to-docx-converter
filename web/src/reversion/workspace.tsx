"use client";

import {
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
  useEffect,
  useState,
  useSyncExternalStore,
} from "react";
import { Alert, AppShell } from "../../components/primitives";
import { useAuth } from "../auth/context";
import {
  isCancellable,
  ReversionController,
  statusPresentation,
} from "./controller";

export function saveReversionDownload(
  download: { blob: Blob; filename: string },
  defer: (callback: () => void) => void = (callback) => setTimeout(callback, 0),
): void {
  const url = URL.createObjectURL(download.blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = download.filename;
  link.click();
  defer(() => URL.revokeObjectURL(url));
}

export function ReversionWorkspace({
  controller: supplied,
}: {
  controller?: ReversionController;
}) {
  const { controller: auth, state: authState } = useAuth();
  const [controller] = useState(
    () => supplied ?? new ReversionController(undefined, () => auth.expire()),
  );
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.snapshot,
    controller.snapshot,
  );
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    void controller.load();
    return () => controller.dispose();
  }, [controller]);

  if (authState.phase !== "authenticated") return null;
  const extensionHint = state.extensions.join(", ");
  return (
    <AppShell
      current="Revert"
      user={authState.user}
      pending={authState.pending}
      onLogout={() => void auth.logout()}
    >
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-3xl font-semibold">Revert to Markdown</h1>
          <span className="experimental-stamp">Experimental</span>
        </div>
        <p>
          Convert a supported document locally to Markdown using a CPU-only,
          low-compute workflow.
        </p>
        <p className="text-sm text-muted">
          OCR is not available. Scanned or image-only PDFs cannot be converted,
          and no document is sent to a hosted fallback service.
        </p>
      </header>
      {state.phase === "loading" && (
        <p aria-live="polite">Loading supported document types…</p>
      )}
      {state.phase === "unavailable" && (
        <section aria-labelledby="reversion-unavailable" className="space-y-3">
          <h2 id="reversion-unavailable">Revert is unavailable</h2>
          <Alert tone="danger">
            Supported document types could not be loaded safely. Submission is
            disabled.
          </Alert>
          <button type="button" onClick={() => void controller.load()}>
            Try again
          </button>
        </section>
      )}
      {state.phase === "ready" && (
        <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.7fr)]">
          <section
            aria-labelledby="new-reversion-heading"
            className="space-y-5"
          >
            <h2 className="text-xl font-semibold" id="new-reversion-heading">
              New conversion to Markdown
            </h2>
            <p>
              The server currently accepts {extensionHint}. Extensions are a
              selection hint; the server verifies the document content.
            </p>
            {state.capabilities?.pdf.warning && (
              <p className="text-sm text-muted">
                PDF limitation: {state.capabilities.pdf.warning}.
              </p>
            )}
            {state.error && <Alert tone="danger">{state.error}</Alert>}
            <form
              aria-busy={state.submitting}
              className="space-y-5"
              onSubmit={(event: FormEvent) => {
                event.preventDefault();
                void controller.submit();
              }}
            >
              <label
                className="grid gap-2 rounded-control border border-muted p-4 font-medium"
                onDragEnter={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={(event) => {
                  event.preventDefault();
                  setDragging(false);
                }}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event: DragEvent<HTMLLabelElement>) => {
                  event.preventDefault();
                  setDragging(false);
                  controller.setSource(event.dataTransfer.files);
                }}
              >
                Source document
                <input
                  accept={state.extensions.join(",")}
                  aria-required="true"
                  name="source"
                  onChange={(event: ChangeEvent<HTMLInputElement>) =>
                    controller.setSource(event.target.files)
                  }
                  type="file"
                />
                <span className="text-sm text-muted">
                  {dragging
                    ? "Drop the document now."
                    : state.source
                      ? `Selected ${state.source.name} (${state.source.size} bytes).`
                      : `Choose or drop exactly one supported document (maximum ${state.capabilities?.maximum_upload_bytes} bytes).`}
                </span>
              </label>
              <button
                className="primary-button"
                disabled={state.submitting}
                type="submit"
              >
                {state.submitting ? "Submitting document…" : "Start conversion"}
              </button>
              {state.notice && <p aria-live="polite">{state.notice}</p>}
            </form>
          </section>
          <aside className="space-y-6">
            <section aria-labelledby="reversion-status" className="space-y-3">
              <h2 className="text-xl font-semibold" id="reversion-status">
                Conversion status
              </h2>
              <p aria-live="polite">
                {state.active
                  ? statusPresentation(state.active)
                  : "Submit a document or choose a recent conversion."}
              </p>
              {state.active && isCancellable(state.active) && (
                <button
                  disabled={state.cancelling}
                  onClick={() => void controller.cancel()}
                  type="button"
                >
                  {state.cancelling
                    ? "Requesting cancellation…"
                    : "Cancel conversion"}
                </button>
              )}
              {state.active?.state === "succeeded" && (
                <button
                  type="button"
                  onClick={() =>
                    void controller.download().then((download) => {
                      if (download) saveReversionDownload(download);
                    })
                  }
                >
                  Download Markdown result
                </button>
              )}
            </section>
            <section aria-labelledby="recent-reversions">
              <h2 className="text-xl font-semibold" id="recent-reversions">
                Recent conversions
              </h2>
              {state.recent.length === 0 ? (
                <p>No recent document conversions.</p>
              ) : (
                <ul
                  aria-label="Recent document conversions"
                  className="space-y-2"
                >
                  {state.recent.map((job) => (
                    <li key={job.id}>
                      <button
                        type="button"
                        onClick={() => void controller.openJob(job.id)}
                      >
                        {job.source_stem}
                        {job.source_extension} · {job.state}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </aside>
        </div>
      )}
    </AppShell>
  );
}
