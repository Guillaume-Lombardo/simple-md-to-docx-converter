"use client";

import {
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { Alert, AppShell, Progress } from "../../components/primitives";
import { useAuth } from "../auth/context";
import {
  ConversionController,
  isCancellable,
  statusPresentation,
} from "./controller";

export function ConversionWorkspace({
  controller: supplied,
}: {
  controller?: ConversionController;
}) {
  const { controller: auth, state: authState } = useAuth();
  const [controller] = useState(
    () => supplied ?? new ConversionController(undefined, () => auth.expire()),
  );
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.snapshot,
    controller.snapshot,
  );
  const [query, setQuery] = useState("");
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void controller.load();
    return () => controller.dispose();
  }, [controller]);

  useEffect(() => {
    if (state.phase !== "ready") return;
    const timer = setTimeout(() => void controller.searchTemplates(query), 250);
    return () => clearTimeout(timer);
  }, [controller, query, state.phase]);

  if (authState.phase !== "authenticated") return null;
  return (
    <AppShell
      current="Convert"
      user={authState.user}
      pending={authState.pending}
      onLogout={() => void auth.logout()}
    >
      <h1 className="text-3xl font-semibold">Convert Markdown</h1>
      {state.phase === "loading" && (
        <p aria-live="polite">Loading conversion options…</p>
      )}
      {state.phase === "unavailable" && (
        <section aria-labelledby="conversion-unavailable">
          <h2 id="conversion-unavailable">Conversion is unavailable</h2>
          <Alert tone="danger">
            Conversion options could not be loaded. Try again shortly.
          </Alert>
          <button type="button" onClick={() => void controller.load()}>
            Try again
          </button>
        </section>
      )}
      {state.phase === "ready" && (
        <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.7fr)]">
          <section
            aria-labelledby="new-conversion-heading"
            className="space-y-5"
          >
            <h2 className="text-xl font-semibold" id="new-conversion-heading">
              New conversion
            </h2>
            <p>
              Upload one Markdown file, or a ZIP package containing Markdown and
              its local assets.
            </p>
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
                Source file
                <input
                  accept=".md,.zip,text/markdown,application/zip"
                  name="source"
                  aria-required="true"
                  onChange={(event: ChangeEvent<HTMLInputElement>) =>
                    controller.setSource(event.target.files)
                  }
                  ref={fileInput}
                  type="file"
                />
                <span className="text-sm text-muted">
                  {dragging
                    ? "Drop the file now."
                    : `Choose or drop exactly one .md or .zip file (maximum ${state.maximumBytes} bytes).`}
                </span>
              </label>
              <fieldset className="space-y-2">
                <legend className="font-semibold">Output</legend>
                {(["docx", "pdf", "both"] as const).map((output) => (
                  <label className="mr-5 inline-flex gap-2" key={output}>
                    <input
                      checked={state.output === output}
                      name="output"
                      onChange={() => controller.setOutput(output)}
                      type="radio"
                      value={output}
                    />
                    {output === "docx"
                      ? "DOCX"
                      : output === "pdf"
                        ? "PDF"
                        : "DOCX and PDF (ZIP)"}
                  </label>
                ))}
              </fieldset>
              <section aria-labelledby="template-heading" className="space-y-3">
                <h3 className="font-semibold" id="template-heading">
                  Document styling
                </h3>
                <p aria-live="polite">
                  <span className="block text-sm text-muted">
                    {state.selection?.source === "preferred"
                      ? "Preferred template"
                      : state.selection?.source === "system_fallback"
                        ? "System fallback template"
                        : state.selection
                          ? "Selected template"
                          : "Document styling"}
                  </span>
                  <strong>{state.selection?.name ?? "Pandoc default"}</strong>
                  {state.selection?.description && (
                    <span className="block">{state.selection.description}</span>
                  )}
                </p>
                <button
                  type="button"
                  onClick={() => controller.choosePandocDefault()}
                >
                  Use Pandoc default
                </button>
                <label className="grid gap-2 font-medium">
                  Search active templates
                  <input
                    autoComplete="off"
                    className="rounded-control border border-muted px-3 py-2"
                    onChange={(event) => setQuery(event.target.value)}
                    type="search"
                    value={query}
                  />
                </label>
                <div aria-live="polite" aria-busy={state.searching}>
                  {state.searching ? "Searching templates…" : null}
                </div>
                {!state.searching && state.templates.length === 0 && query && (
                  <p>No active templates match your search.</p>
                )}
                <ul aria-label="Template search results" className="space-y-2">
                  {state.templates.map((template) => (
                    <li key={template.id}>
                      <button
                        className="w-full rounded-control border border-muted p-3 text-left"
                        disabled={!template.current_version_id}
                        onClick={() => controller.chooseTemplate(template)}
                        type="button"
                      >
                        <strong className="block">{template.name}</strong>
                        <span>{template.description || "No description"}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
              <button
                className="primary-button"
                disabled={state.submitting}
                type="submit"
              >
                {state.submitting
                  ? "Submitting conversion…"
                  : "Start conversion"}
              </button>
              {state.notice && <p aria-live="polite">{state.notice}</p>}
            </form>
          </section>
          <aside className="space-y-6">
            <section aria-labelledby="status-heading" className="space-y-3">
              <h2 className="text-xl font-semibold" id="status-heading">
                Conversion status
              </h2>
              <div aria-live="polite">
                {state.active
                  ? statusPresentation(state.active)
                  : "Submit a conversion or choose a recent one."}
              </div>
              {state.active && (
                <Progress
                  label="Conversion progress"
                  value={state.active.progress}
                />
              )}
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
                    void controller.download().then(async (download) => {
                      if (!download) return;
                      const blob = await download.response.blob();
                      const url = URL.createObjectURL(blob);
                      const link = document.createElement("a");
                      link.href = url;
                      link.download = download.filename;
                      link.click();
                      URL.revokeObjectURL(url);
                    })
                  }
                >
                  Download result
                </button>
              )}
            </section>
            <section aria-labelledby="recent-heading">
              <h2 className="text-xl font-semibold" id="recent-heading">
                Recent conversions
              </h2>
              {state.recent.length === 0 ? (
                <p>No recent conversions.</p>
              ) : (
                <ul aria-label="Recent conversions" className="space-y-2">
                  {state.recent.map((job) => (
                    <li key={job.id}>
                      <button
                        type="button"
                        onClick={() => void controller.openJob(job.id)}
                      >
                        Conversion {job.id.slice(0, 8)} · {job.state}
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
