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
import { Alert, AppShell } from "../../components/primitives";
import { useAuth } from "../auth/context";
import {
  ownerStorageEpoch,
  readReversionInputs,
  writeReversionInputs,
} from "../conversion/persistence";
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
  const ownerId =
    authState.phase === "authenticated" ? authState.user.id : null;
  const storageEpoch = useMemo(
    () => (ownerId ? ownerStorageEpoch(ownerId) : null),
    [ownerId],
  );
  const [controller] = useState(
    () => supplied ?? new ReversionController(undefined, () => auth.expire()),
  );
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.snapshot,
    controller.snapshot,
  );
  const { source, options, active } = state;
  const activeJobId = active?.id;
  const [dragging, setDragging] = useState(false);
  const [restored, setRestored] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [readError, setReadError] = useState(false);
  const [reading, setReading] = useState(false);
  const [readAttempt, setReadAttempt] = useState(0);
  const [storageError, setStorageError] = useState(false);
  const saveQueue = useRef<Promise<void>>(Promise.resolve());
  const initialInputVersion = useRef(0);

  useEffect(() => {
    if (!ownerId) return;
    let current = true;
    void (async () => {
      await controller.load();
      if (!current) return;
      initialInputVersion.current = controller.inputVersion();
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
        const saved = await readReversionInputs(ownerId);
        if (!current) return;
        if (saved) controller.restoreInputs(saved, initialInputVersion.current);
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
  }, [controller, loaded, ownerId, readAttempt]);

  useEffect(() => {
    if (!restored || !ownerId || storageEpoch === null) return;
    saveQueue.current = saveQueue.current
      .catch(() => undefined)
      .then(() =>
        writeReversionInputs(
          ownerId,
          { source, options, activeJobId },
          storageEpoch,
        ),
      )
      .then(
        () => setStorageError(false),
        () => setStorageError(true),
      );
  }, [restored, ownerId, storageEpoch, source, options, activeJobId]);

  async function openComposer(create: () => Promise<string | undefined>) {
    if (!ownerId) return;
    if (!restored && state.source) return;
    try {
      await saveQueue.current;
      if (restored && storageEpoch !== null) {
        await writeReversionInputs(
          ownerId,
          { source, options, activeJobId },
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

  if (authState.phase !== "authenticated") return null;
  const extensionHint = state.extensions.join(", ");
  const supportsStructuredSource =
    state.source &&
    state.capabilities?.extraction.structured_extensions.some((extension) =>
      state.source?.name.toLowerCase().endsWith(extension),
    );
  return (
    <AppShell
      current="Revert"
      user={authState.user}
      pending={authState.pending}
      onLogout={() => {
        void auth.logout();
      }}
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
          Your selected document could not be saved in this browser. Free
          browser storage and choose the file again before opening Composer.
        </Alert>
      )}
      {state.phase === "loading" && (
        <p aria-live="polite">Loading supported document types…</p>
      )}
      {state.phase === "unavailable" && (
        <section aria-labelledby="reversion-unavailable" className="space-y-3">
          <h2 id="reversion-unavailable">Revert is unavailable</h2>
          <Alert tone="danger">
            The reverse-conversion service is unavailable or its configuration
            could not be loaded. Try again or contact your administrator.
            Submission is disabled.
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
                Source document
                <input
                  accept={state.extensions.join(",")}
                  aria-required="true"
                  name="source"
                  onChange={(event: ChangeEvent<HTMLInputElement>) =>
                    controller.setSource(event.target.files)
                  }
                  className="sr-only"
                  type="file"
                />
                <span className="py-4 text-center text-lg font-semibold wrap-anywhere">
                  {dragging
                    ? "Drop the document now."
                    : state.source
                      ? `Selected ${state.source.name} (${state.source.size} bytes).`
                      : `Choose or drop exactly one supported document (maximum ${state.capabilities?.maximum_upload_bytes} bytes).`}
                </span>
                <span
                  aria-hidden="true"
                  className="w-fit cursor-pointer rounded-control border border-muted px-2 py-1 text-xs font-normal text-muted"
                >
                  {state.source ? "Change file" : "Choose file"}
                </span>
              </label>
              {supportsStructuredSource && state.options && (
                <fieldset className="space-y-3 rounded-control border border-muted p-4">
                  <legend className="px-1 font-medium">
                    PowerPoint extraction
                  </legend>
                  <p className="text-sm text-muted">
                    Slide-oriented output preserves slide boundaries.
                    Unsupported meaningful content is marked with a clear
                    placeholder.
                  </p>
                  {state.capabilities?.extraction.modes.includes("anydoc") && (
                    <label className="flex items-center gap-2">
                      <input
                        checked={state.options.extraction === "anydoc"}
                        name="extraction"
                        onChange={() => controller.setExtraction("anydoc")}
                        type="radio"
                      />
                      Standard document extraction
                    </label>
                  )}
                  {state.capabilities?.extraction.modes.includes("slides") && (
                    <label className="flex items-center gap-2">
                      <input
                        checked={state.options.extraction === "slides"}
                        name="extraction"
                        onChange={() => controller.setExtraction("slides")}
                        type="radio"
                      />
                      Slide-oriented Markdown
                    </label>
                  )}
                  {state.capabilities?.extraction.modes.includes("marp") && (
                    <label className="flex items-center gap-2">
                      <input
                        checked={state.options.extraction === "marp"}
                        name="extraction"
                        onChange={() => controller.setExtraction("marp")}
                        type="radio"
                      />
                      Marp Markdown
                    </label>
                  )}
                  {state.options.extraction !== "anydoc" && (
                    <div className="grid gap-2 sm:grid-cols-2">
                      <label className="flex items-center gap-2">
                        <input
                          checked={state.options.include_notes}
                          onChange={(event) =>
                            controller.setIncludeNotes(event.target.checked)
                          }
                          type="checkbox"
                        />
                        Include presenter notes
                      </label>
                      <label className="flex items-center gap-2">
                        <input
                          checked={state.options.include_images}
                          onChange={(event) =>
                            controller.setIncludeImages(event.target.checked)
                          }
                          type="checkbox"
                        />
                        Include images
                      </label>
                    </div>
                  )}
                </fieldset>
              )}
              <button
                className="primary-button"
                disabled={state.submitting}
                type="submit"
              >
                {state.submitting ? "Submitting document…" : "Start conversion"}
              </button>
              {state.source && (
                <div className="space-y-2">
                  <button
                    disabled={
                      state.composerPending || storageError || !restored
                    }
                    onClick={() =>
                      void openComposer(() => controller.createComposerDraft())
                    }
                    type="button"
                  >
                    {state.composerPending
                      ? "Creating Composer draft…"
                      : "Open selected source in Composer"}
                  </button>
                  <p className="text-sm text-muted">
                    Composer accepts DOCX, PPTX, and PDF sources. Opening one
                    saves an unchanged copy as a draft. Office editing is not
                    yet available.
                  </p>
                </div>
              )}
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
                <>
                  <button
                    className="primary-button w-full"
                    type="button"
                    onClick={() =>
                      void controller.download().then((download) => {
                        if (download) saveReversionDownload(download);
                      })
                    }
                  >
                    Download result
                  </button>
                  <button
                    disabled={
                      state.composerPending ||
                      ((storageError || !restored) && Boolean(state.source))
                    }
                    onClick={() =>
                      void openComposer(() => controller.handoffActiveResult())
                    }
                    type="button"
                  >
                    {state.composerPending
                      ? "Creating Composer draft…"
                      : "Open Markdown result in Composer"}
                  </button>
                </>
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
                        title={job.id}
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
