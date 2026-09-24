"use client";

import {
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { PresentationControls } from "./presentation-controls";
import { Alert, AppShell, Progress } from "../../components/primitives";
import { useAuth } from "../auth/context";
import {
  ConversionController,
  isCancellable,
  statusPresentation,
} from "./controller";
import {
  ownerStorageEpoch,
  readConversionInputs,
  writeConversionInputs,
} from "./persistence";

export function saveDownload(
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

export function ConversionWorkspace({
  controller: supplied,
  presentation = false,
}: {
  controller?: ConversionController;
  presentation?: boolean;
}) {
  const { controller: auth, state: authState } = useAuth();
  const ownerId =
    authState.phase === "authenticated" ? authState.user.id : null;
  const storageEpoch = useMemo(
    () => (ownerId ? ownerStorageEpoch(ownerId) : null),
    [ownerId],
  );
  const [controller] = useState(
    () =>
      supplied ??
      new ConversionController(
        undefined,
        () => auth.expire(),
        undefined,
        undefined,
        undefined,
        presentation,
      ),
  );
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.snapshot,
    controller.snapshot,
  );
  const { source, output, selection, dialect, slideLevel, active } = state;
  const activeJobId = active?.id;
  const [query, setQuery] = useState("");
  const [dragging, setDragging] = useState(false);
  const [restored, setRestored] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [readError, setReadError] = useState(false);
  const [reading, setReading] = useState(false);
  const [readAttempt, setReadAttempt] = useState(0);
  const [storageError, setStorageError] = useState(false);
  const saveQueue = useRef<Promise<void>>(Promise.resolve());
  const queryVersion = useRef(0);
  const initialInputVersion = useRef(0);
  const initialQueryVersion = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!ownerId) return;
    let current = true;
    void (async () => {
      await controller.load();
      if (!current) return;
      initialInputVersion.current = controller.inputVersion();
      initialQueryVersion.current = queryVersion.current;
      setLoaded(true);
    })();
    return () => {
      current = false;
      controller.dispose();
    };
  }, [controller, ownerId]);

  useEffect(() => {
    if (!loaded || !ownerId) return;
    let current = true;
    void (async () => {
      try {
        const saved = await readConversionInputs(ownerId, presentation);
        if (!current) return;
        if (
          saved &&
          controller.restoreInputs(saved, initialInputVersion.current)
        ) {
          if (queryVersion.current === initialQueryVersion.current)
            setQuery(saved.query);
        }
        setReadError(false);
        setRestored(true);
      } catch {
        if (current) setReadError(true);
      } finally {
        if (current) setReading(false);
      }
    })();
    return () => {
      current = false;
    };
  }, [controller, loaded, ownerId, presentation, readAttempt]);

  useEffect(() => {
    if (!restored || !ownerId || storageEpoch === null) return;
    saveQueue.current = saveQueue.current
      .catch(() => undefined)
      .then(() =>
        writeConversionInputs(
          ownerId,
          presentation,
          { source, output, selection, dialect, slideLevel, activeJobId },
          query,
          storageEpoch,
        ),
      )
      .then(
        () => setStorageError(false),
        () => setStorageError(true),
      );
  }, [
    restored,
    ownerId,
    storageEpoch,
    presentation,
    source,
    output,
    selection,
    dialect,
    slideLevel,
    activeJobId,
    query,
  ]);

  async function openComposer(create: () => Promise<string | undefined>) {
    if (!ownerId) return;
    if (!restored && state.source) return;
    try {
      await saveQueue.current;
      if (restored && storageEpoch !== null) {
        await writeConversionInputs(
          ownerId,
          presentation,
          { source, output, selection, dialect, slideLevel, activeJobId },
          query,
          storageEpoch,
        );
        setStorageError(false);
      }
    } catch {
      setStorageError(true);
      if (state.source) return;
    }
    const id = await create();
    if (id) {
      const link = document.createElement("a");
      link.href = `/composer?draft=${encodeURIComponent(id)}`;
      link.click();
    }
  }

  useEffect(() => {
    if (state.phase !== "ready") return;
    const timer = setTimeout(() => void controller.searchTemplates(query), 250);
    return () => clearTimeout(timer);
  }, [controller, query, state.phase]);

  if (authState.phase !== "authenticated") return null;
  return (
    <AppShell
      current={presentation ? "Presentations" : "Convert"}
      user={authState.user}
      pending={authState.pending}
      onLogout={() => {
        void auth.logout();
      }}
    >
      <h1 className="text-3xl font-semibold">
        {presentation ? "Create a PowerPoint presentation" : "Convert Markdown"}
      </h1>
      {readError && (
        <Alert tone="danger">
          Saved browser inputs could not be loaded. Your current selection is
          still available. Try loading them again before opening Composer.
          <button
            disabled={reading}
            onClick={() => {
              setReading(true);
              setReadAttempt((value) => value + 1);
            }}
            type="button"
          >
            {reading ? "Loading saved inputs…" : "Try loading saved inputs"}
          </button>
        </Alert>
      )}
      {storageError && state.source && (
        <Alert tone="danger">
          Your selected file could not be saved in this browser. Free browser
          storage and choose the file again before opening Composer.
        </Alert>
      )}
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
                className="grid gap-2 rounded-control border border-muted p-4 font-medium focus-within:outline-2 focus-within:outline-accent"
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
                  className="sr-only"
                  type="file"
                />
                <span className="py-4 text-center text-lg font-semibold wrap-anywhere">
                  {dragging
                    ? "Drop the file now."
                    : state.source
                      ? `Selected ${state.source.name} (${state.source.size} bytes).`
                      : `Choose or drop exactly one .md or .zip file (maximum ${state.maximumBytes} bytes).`}
                </span>
                <span
                  aria-hidden="true"
                  className="w-fit cursor-pointer rounded-control border border-muted px-2 py-1 text-xs font-normal text-muted"
                >
                  {state.source ? "Change file" : "Choose file"}
                </span>
              </label>
              {presentation && (
                <PresentationControls controller={controller} state={state} />
              )}
              <fieldset className="space-y-2">
                <legend className="font-semibold">Output</legend>
                {(presentation
                  ? (["pptx", "pptx-bundle"] as const)
                  : (["docx", "pdf", "both"] as const)
                ).map((output) => (
                  <label className="mr-5 inline-flex gap-2" key={output}>
                    <input
                      checked={state.output === output}
                      name="output"
                      onChange={() => controller.setOutput(output)}
                      type="radio"
                      value={output}
                    />
                    {output === "pptx"
                      ? "PowerPoint (PPTX)"
                      : output === "pptx-bundle"
                        ? "PowerPoint and original source (ZIP)"
                        : output === "docx"
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
                    onChange={(event) => {
                      queryVersion.current += 1;
                      setQuery(event.target.value);
                    }}
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
              {state.source && (
                <button
                  disabled={state.composerPending || storageError || !restored}
                  onClick={() =>
                    void openComposer(() => controller.createComposerDraft())
                  }
                  type="button"
                >
                  {state.composerPending
                    ? "Creating Composer draft…"
                    : "Open selected source in Composer"}
                </button>
              )}
              {state.notice && <p aria-live="polite">{state.notice}</p>}
            </form>
          </section>
          <aside className="space-y-6">
            <section aria-labelledby="status-heading" className="space-y-3">
              <h2 className="text-xl font-semibold" id="status-heading">
                Conversion status
              </h2>
              <div aria-live="polite" className="min-h-12">
                {state.active
                  ? statusPresentation(state.active)
                  : "Submit a conversion or choose a recent one."}
              </div>
              <div className="min-h-24 space-y-3">
                {state.active && state.active.state !== "succeeded" && (
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
                  <>
                    <button
                      className="primary-button w-full"
                      type="button"
                      onClick={() =>
                        void controller.download().then((download) => {
                          if (!download) return;
                          saveDownload(download);
                        })
                      }
                    >
                      Download result
                    </button>
                    {["docx", "pdf", "pptx"].includes(state.active.output) && (
                      <button
                        disabled={
                          state.composerPending ||
                          ((storageError || !restored) && Boolean(state.source))
                        }
                        onClick={() =>
                          void openComposer(() =>
                            controller.handoffActiveResult(),
                          )
                        }
                        type="button"
                      >
                        {state.composerPending
                          ? "Creating Composer draft…"
                          : "Open result in Composer"}
                      </button>
                    )}
                  </>
                )}
              </div>
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
                        title={job.id}
                        onClick={() => void controller.openJob(job.id)}
                      >
                        {job.source_filename || "Untitled document"} ·{" "}
                        {job.state}
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
