"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  assertRevisionUrl,
  DEFAULT_PREVIEW_MAX_BYTES,
  readBoundedResponse,
  revisionArtifactPath,
  verifySha256,
} from "./bytes";
import { PdfPreview } from "./pdf-preview";

type PreviewFormat = "docx" | "pptx" | "pdf";
export type ActivePreviewRevision = {
  draftId: string;
  revisionId: string;
  format: PreviewFormat;
  downloadUrl: string;
};
type PreviewArtifact = ActivePreviewRevision & {
  requestKey: string;
  previewUrl: string;
  previewSha256?: string;
  maxBytes: number;
};
type OfficeFrame = {
  token: string;
  draftId: string;
  revisionId: string;
  format: "docx" | "pptx";
  previewUrl: string;
  downloadUrl: string;
  previewSha256?: string;
};

export interface ComposerPreviewProps {
  draftId: string;
  revisionId: string;
  format: PreviewFormat;
  previewUrl?: string;
  downloadUrl?: string;
  previewSha256?: string;
  maxBytes?: number;
  maxSerializedMarkupBytes?: number;
  className?: string;
  onActiveRevisionChange?: (active: ActivePreviewRevision | null) => void;
  onUnauthorized?: () => void;
  onDownload?: (artifact: ActivePreviewRevision) => void | Promise<void>;
}

const MIME: Record<"docx" | "pptx", string> = {
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
};

export function ComposerPreview({
  draftId,
  revisionId,
  format,
  previewUrl,
  downloadUrl,
  previewSha256,
  maxBytes = DEFAULT_PREVIEW_MAX_BYTES,
  maxSerializedMarkupBytes,
  className,
  onActiveRevisionChange,
  onUnauthorized,
  onDownload,
}: ComposerPreviewProps) {
  const [frames, setFrames] = useState<OfficeFrame[]>([]);
  const [activeToken, setActiveToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [activeArtifact, setActiveArtifact] = useState<PreviewArtifact | null>(
    null,
  );
  const activeForDraft =
    activeArtifact?.draftId === draftId ? activeArtifact : null;
  const activeRef = useRef<string | null>(null);
  const pendingRef = useRef<OfficeFrame | null>(null);
  const controllers = useRef(new Map<string, AbortController>());
  const started = useRef(new Set<string>());
  const frameElements = useRef(new Map<string, HTMLIFrameElement>());
  const activeChangeRef = useRef(onActiveRevisionChange);
  const unauthorizedRef = useRef(onUnauthorized);
  const reportedActiveRef = useRef<ActivePreviewRevision | null>(null);
  const position = useRef(0);
  const zoomRef = useRef(zoom);
  useEffect(() => {
    zoomRef.current = zoom;
  }, [zoom]);
  useEffect(() => {
    activeChangeRef.current = onActiveRevisionChange;
  }, [onActiveRevisionChange]);
  useEffect(() => {
    if (activeForDraft) {
      const active = {
        draftId: activeForDraft.draftId,
        revisionId: activeForDraft.revisionId,
        format: activeForDraft.format,
        downloadUrl: activeForDraft.downloadUrl,
      };
      reportedActiveRef.current = active;
      activeChangeRef.current?.(active);
    } else if (reportedActiveRef.current) {
      reportedActiveRef.current = null;
      activeChangeRef.current?.(null);
    }
  }, [activeForDraft]);
  useEffect(
    () => () => {
      if (reportedActiveRef.current) {
        reportedActiveRef.current = null;
        activeChangeRef.current?.(null);
      }
    },
    [],
  );

  let expectedPreview = "";
  let expectedDownload = "";
  let pathError: string | null = null;
  try {
    expectedPreview = assertRevisionUrl(
      previewUrl ?? revisionArtifactPath(draftId, revisionId, "preview"),
      revisionArtifactPath(draftId, revisionId, "preview"),
    );
    expectedDownload = assertRevisionUrl(
      downloadUrl ?? revisionArtifactPath(draftId, revisionId, "download"),
      revisionArtifactPath(draftId, revisionId, "download"),
    );
  } catch (caught) {
    pathError = caught instanceof Error ? caught.message : "Invalid revision.";
  }

  const requestKey = `${draftId}:${revisionId}:${format}:${expectedPreview}:${expectedDownload}:${previewSha256 ?? ""}:${maxSerializedMarkupBytes ?? "default"}`;
  const requestKeyRef = useRef(requestKey);
  useLayoutEffect(() => {
    requestKeyRef.current = requestKey;
    unauthorizedRef.current = onUnauthorized;
  }, [requestKey, onUnauthorized]);
  const requested: PreviewArtifact = {
    draftId,
    revisionId,
    format,
    requestKey,
    previewUrl: expectedPreview,
    downloadUrl: expectedDownload,
    previewSha256,
    maxBytes,
  };
  useEffect(() => {
    if (pathError) return;
    if (format === "pdf") {
      let cancelled = false;
      queueMicrotask(() => {
        if (!cancelled) {
          setLoading(true);
          setError(null);
        }
      });
      return () => {
        cancelled = true;
      };
    }
    const frame: OfficeFrame = {
      token: crypto.randomUUID(),
      draftId,
      revisionId,
      format,
      previewUrl: expectedPreview,
      downloadUrl: expectedDownload,
      previewSha256,
    };
    pendingRef.current = frame;
    setFrames((current) => [
      ...current.filter(
        (item) => item.token === activeRef.current && item.draftId === draftId,
      ),
      frame,
    ]);
    setLoading(true);
    setError(null);
    const activeControllers = controllers.current;
    const activeStarted = started.current;
    return () => {
      activeControllers.get(frame.token)?.abort();
      activeControllers.delete(frame.token);
      activeStarted.delete(frame.token);
      if (pendingRef.current?.token === frame.token) pendingRef.current = null;
    };
    // Each request key represents one immutable revision artifact.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== "null") return;
      const data = event.data as Record<string, unknown> | null;
      if (!data || typeof data !== "object") return;
      const pending = pendingRef.current;
      const frame = pending && frameElements.current.get(pending.token);
      if (
        data.type === "composer-preview-ready" &&
        pending &&
        frame?.contentWindow === event.source &&
        !started.current.has(pending.token)
      ) {
        started.current.add(pending.token);
        const controller = new AbortController();
        controllers.current.set(pending.token, controller);
        void (async () => {
          try {
            const response = await fetch(pending.previewUrl, {
              cache: "no-store",
              credentials: "same-origin",
              signal: controller.signal,
            });
            if (
              response.status === 401 &&
              !controller.signal.aborted &&
              pendingRef.current?.token === pending.token &&
              requestKeyRef.current === requestKey
            )
              unauthorizedRef.current?.();
            const bytes = await readBoundedResponse(
              response,
              maxBytes,
              MIME[pending.format],
            );
            await verifySha256(bytes, pending.previewSha256);
            if (
              pendingRef.current?.token !== pending.token ||
              controller.signal.aborted
            )
              return;
            frame.contentWindow?.postMessage(
              {
                type: "composer-preview-render",
                token: pending.token,
                draftId: pending.draftId,
                revisionId: pending.revisionId,
                format: pending.format,
                bytes,
                maxSerializedMarkupBytes,
                zoom: zoomRef.current,
                position: position.current,
              },
              "*",
              [bytes],
            );
          } catch (caught) {
            if (
              controller.signal.aborted ||
              pendingRef.current?.token !== pending.token
            )
              return;
            setLoading(false);
            setError(
              caught instanceof Error ? caught.message : "The preview failed.",
            );
            setFrames((current) =>
              current.filter((item) => item.token !== pending.token),
            );
            pendingRef.current = null;
          }
        })();
        return;
      }
      const matching = [...frames].find(
        (item) =>
          frameElements.current.get(item.token)?.contentWindow === event.source,
      );
      if (
        !matching ||
        data.token !== matching.token ||
        data.draftId !== matching.draftId ||
        data.revisionId !== matching.revisionId
      )
        return;
      if (
        data.type === "composer-preview-position" &&
        matching.token === activeRef.current
      ) {
        if (typeof data.position === "number" && Number.isFinite(data.position))
          position.current = Math.max(0, data.position);
        return;
      }
      if (
        data.type === "composer-preview-runtime-error" &&
        matching.token === activeRef.current
      ) {
        setError("This document cannot be shown safely in the native preview.");
        return;
      }
      if (
        data.type !== "composer-preview-result" ||
        pendingRef.current?.token !== matching.token
      )
        return;
      setLoading(false);
      pendingRef.current = null;
      controllers.current.delete(matching.token);
      if (data.status === "ready") {
        activeRef.current = matching.token;
        setActiveToken(matching.token);
        setActiveArtifact({
          draftId: matching.draftId,
          revisionId: matching.revisionId,
          format: matching.format,
          requestKey,
          previewUrl: matching.previewUrl,
          downloadUrl: matching.downloadUrl,
          previewSha256: matching.previewSha256,
          maxBytes,
        });
        setFrames([matching]);
        setError(null);
      } else {
        setFrames((current) =>
          current.filter((item) => item.token !== matching.token),
        );
        setError("This document cannot be shown safely in the native preview.");
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [frames, maxBytes, maxSerializedMarkupBytes, requestKey]);

  const changeZoom = (next: number) => {
    const bounded = Math.min(2, Math.max(0.5, next));
    setZoom(bounded);
    for (const frame of frames) {
      frameElements.current.get(frame.token)?.contentWindow?.postMessage(
        {
          type: "composer-preview-zoom",
          token: frame.token,
          draftId: frame.draftId,
          revisionId: frame.revisionId,
          zoom: bounded,
        },
        "*",
      );
    }
  };

  const pdfSource =
    format === "pdf" && !pathError
      ? requested
      : activeForDraft?.format === "pdf"
        ? activeForDraft
        : null;
  const pdfVisible =
    activeForDraft?.format === "pdf" || (!activeForDraft && format === "pdf");
  const officeVisible =
    (activeForDraft !== null && activeForDraft.format !== "pdf") ||
    (!activeForDraft && format !== "pdf");
  const displayFormat = activeForDraft?.format ?? format;

  const onPdfReady = (key: string) => {
    if (format !== "pdf" || pathError || key !== requestKey) return;
    activeRef.current = null;
    setActiveToken(null);
    setFrames([]);
    setActiveArtifact(requested);
    setLoading(false);
    setError(null);
  };

  const onPdfError = (key: string, message: string) => {
    if (format !== "pdf" || pathError || key !== requestKey) return;
    setLoading(false);
    setError(message);
  };

  const onPdfUnauthorized = (key: string) => {
    if (format !== "pdf" || pathError || key !== requestKey) return;
    if (requestKeyRef.current !== key) return;
    unauthorizedRef.current?.();
  };

  return (
    <section className={className} aria-label="Revision preview">
      <div className="flex flex-wrap items-center justify-between gap-2 pb-3">
        <div className="flex items-center gap-2">
          <strong className="text-sm">
            {displayFormat.toUpperCase()}
            {activeForDraft
              ? ` revision ${activeForDraft.revisionId}`
              : ""}{" "}
            preview
          </strong>
          {loading && <span role="status">Preparing revision…</span>}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => changeZoom(zoom - 0.25)}
            aria-label="Zoom out"
          >
            −
          </button>
          <span aria-live="polite">{Math.round(zoom * 100)}%</span>
          <button
            type="button"
            onClick={() => changeZoom(zoom + 0.25)}
            aria-label="Zoom in"
          >
            +
          </button>
          {activeForDraft &&
            (onDownload ? (
              <button
                type="button"
                onClick={() => void onDownload(activeForDraft)}
                className="underline"
              >
                Download this revision
              </button>
            ) : (
              <a
                href={activeForDraft.downloadUrl}
                download
                className="underline"
              >
                Download this revision
              </a>
            ))}
        </div>
      </div>
      {(pathError || error) && <p role="alert">{pathError || error}</p>}
      {!pathError &&
        !activeForDraft &&
        (onDownload ? (
          <button
            type="button"
            onClick={() => void onDownload(requested)}
            className="underline"
          >
            Download requested revision {revisionId}
          </button>
        ) : (
          <a href={expectedDownload} download className="underline">
            Download requested revision {revisionId}
          </a>
        ))}
      {pdfSource && (
        <div aria-hidden={!pdfVisible} className={pdfVisible ? "" : "hidden"}>
          <PdfPreview
            artifactUrl={pdfSource.previewUrl}
            downloadUrl={pdfSource.downloadUrl}
            maxBytes={pdfSource.maxBytes}
            revisionKey={pdfSource.requestKey}
            sha256={pdfSource.previewSha256}
            zoom={zoom}
            onReady={onPdfReady}
            onError={onPdfError}
            onUnauthorized={onPdfUnauthorized}
          />
        </div>
      )}
      <div
        aria-hidden={!officeVisible}
        className={
          officeVisible
            ? "relative min-h-[34rem] overflow-hidden rounded border border-slate-300 bg-slate-100"
            : "hidden"
        }
      >
        {frames
          .filter((frame) => frame.draftId === draftId)
          .map((frame) => (
            <iframe
              key={frame.token}
              ref={(element) => {
                if (element) frameElements.current.set(frame.token, element);
                else frameElements.current.delete(frame.token);
              }}
              title={`${frame.format.toUpperCase()} revision ${frame.revisionId} preview`}
              src="/composer-preview"
              sandbox="allow-scripts"
              aria-hidden={frame.token !== activeToken || !officeVisible}
              className={
                frame.token === activeToken
                  ? "relative h-[34rem] w-full border-0"
                  : "pointer-events-none invisible absolute inset-0 h-[34rem] w-full border-0"
              }
              onLoad={(event) =>
                event.currentTarget.contentWindow?.postMessage(
                  { type: "composer-preview-ping" },
                  "*",
                )
              }
            />
          ))}
      </div>
      {displayFormat === "docx" && (
        <p className="pt-2 text-xs text-slate-600">
          Native Word preview shows explicit page breaks. Natural Word
          pagination, some lists, footnotes, and fonts may differ. Download the
          revision for final review.
        </p>
      )}
      {displayFormat === "pptx" && (
        <p className="pt-2 text-xs text-slate-600">
          Native PowerPoint preview may differ for grouped shapes, unsupported
          effects, and fonts. Download the revision for final review.
        </p>
      )}
    </section>
  );
}
