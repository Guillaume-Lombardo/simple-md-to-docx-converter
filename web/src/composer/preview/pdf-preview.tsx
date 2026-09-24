"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type {
  PDFDocumentLoadingTask,
  PDFDocumentProxy,
  RenderTask,
} from "pdfjs-dist";
import { readBoundedResponse, verifySha256 } from "./bytes";

type PdfDocument = PDFDocumentProxy;

interface PdfPreviewProps {
  artifactUrl: string;
  downloadUrl: string;
  maxBytes: number;
  revisionKey: string;
  sha256?: string;
  zoom: number;
  onReady?: (revisionKey: string) => void;
  onError?: (revisionKey: string, message: string) => void;
  onUnauthorized?: (revisionKey: string) => void;
}

export function PdfPreview({
  artifactUrl,
  downloadUrl,
  maxBytes,
  revisionKey,
  sha256,
  zoom,
  onReady,
  onError,
  onUnauthorized,
}: PdfPreviewProps) {
  const [document, setDocument] = useState<PdfDocument | null>(null);
  const [committedKey, setCommittedKey] = useState<string | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [thumbnails, setThumbnails] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const cache = useRef(new Map<number, string>());
  const activeTask = useRef<PDFDocumentLoadingTask | null>(null);
  const onReadyRef = useRef(onReady);
  const onErrorRef = useRef(onError);
  const onUnauthorizedRef = useRef(onUnauthorized);
  const revisionKeyRef = useRef(revisionKey);
  useLayoutEffect(() => {
    onUnauthorizedRef.current = onUnauthorized;
    revisionKeyRef.current = revisionKey;
  }, [onUnauthorized, revisionKey]);
  const preparedCanvas = useRef<{
    key: string;
    canvas: HTMLCanvasElement;
  } | null>(null);
  const pageRef = useRef(pageNumber);
  const zoomRef = useRef(zoom);
  useEffect(() => {
    onReadyRef.current = onReady;
    onErrorRef.current = onError;
  }, [onReady, onError]);
  useEffect(() => {
    pageRef.current = pageNumber;
    zoomRef.current = zoom;
  }, [pageNumber, zoom]);

  useLayoutEffect(() => {
    if (!document || !committedKey || !canvas.current) return;
    const prepared = preparedCanvas.current;
    if (!prepared || prepared.key !== committedKey) return;
    const context = canvas.current.getContext("2d");
    if (!context) return;
    canvas.current.width = prepared.canvas.width;
    canvas.current.height = prepared.canvas.height;
    context.drawImage(prepared.canvas, 0, 0);
    preparedCanvas.current = null;
    onReadyRef.current?.(committedKey);
  }, [committedKey, document]);

  useEffect(() => {
    const controller = new AbortController();
    let task: PDFDocumentLoadingTask | null = null;
    let committed = false;
    queueMicrotask(() => {
      if (!controller.signal.aborted) {
        setLoading(true);
        setError(null);
      }
    });
    void (async () => {
      try {
        const response = await fetch(artifactUrl, {
          cache: "no-store",
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (
          response.status === 401 &&
          !controller.signal.aborted &&
          revisionKeyRef.current === revisionKey
        )
          onUnauthorizedRef.current?.(revisionKey);
        const bytes = await readBoundedResponse(
          response,
          maxBytes,
          "application/pdf",
        );
        await verifySha256(bytes, sha256);
        if (controller.signal.aborted) return;
        // Register PDF.js's main-thread worker handler. The parent CSP forbids workers.
        await import("pdfjs-dist/build/pdf.worker.mjs");
        const pdfjs = await import("pdfjs-dist");
        task = pdfjs.getDocument({
          data: new Uint8Array(bytes),
          useSystemFonts: false,
          enableXfa: false,
        });
        const loaded = await task.promise;
        if (controller.signal.aborted) {
          await task.destroy();
          return;
        }
        const firstPageNumber = Math.min(
          Math.max(1, pageRef.current),
          loaded.numPages,
        );
        const firstPage = await loaded.getPage(firstPageNumber);
        const firstViewport = firstPage.getViewport({ scale: zoomRef.current });
        if (firstViewport.width * firstViewport.height > 16_000_000)
          throw new Error("The PDF page exceeds the preview canvas limit.");
        const firstCanvas = window.document.createElement("canvas");
        firstCanvas.width = Math.ceil(firstViewport.width);
        firstCanvas.height = Math.ceil(firstViewport.height);
        const firstContext = firstCanvas.getContext("2d");
        if (!firstContext) throw new Error("Canvas is unavailable.");
        await firstPage.render({
          canvas: firstCanvas,
          canvasContext: firstContext,
          viewport: firstViewport,
        }).promise;
        if (controller.signal.aborted) {
          await task.destroy();
          return;
        }
        cache.current.clear();
        setThumbnails({});
        const previousTask = activeTask.current;
        activeTask.current = task;
        committed = true;
        preparedCanvas.current = { key: revisionKey, canvas: firstCanvas };
        setDocument(loaded);
        setCommittedKey(revisionKey);
        setPageNumber(firstPageNumber);
        if (previousTask) void previousTask.destroy();
        setLoading(false);
      } catch (caught) {
        if (controller.signal.aborted) return;
        setLoading(false);
        const message =
          caught instanceof Error ? caught.message : "The PDF preview failed.";
        setError(message);
        onErrorRef.current?.(revisionKey, message);
      }
    })();
    return () => {
      controller.abort();
      if (task && !committed) void task.destroy();
    };
    // A revision change starts a separate immutable PDF load.
  }, [revisionKey, artifactUrl, downloadUrl, maxBytes, sha256]);

  useEffect(
    () => () => {
      if (activeTask.current) void activeTask.current.destroy();
    },
    [],
  );

  useEffect(() => {
    if (!document || !canvas.current) return;
    let stopped = false;
    let rendering: RenderTask | null = null;
    void (async () => {
      try {
        const page = await document.getPage(pageNumber);
        if (stopped || !canvas.current) return;
        const viewport = page.getViewport({ scale: zoom });
        if (viewport.width * viewport.height > 16_000_000)
          throw new Error("The PDF page exceeds the preview canvas limit.");
        const context = canvas.current.getContext("2d");
        if (!context) throw new Error("Canvas is unavailable.");
        canvas.current.width = Math.ceil(viewport.width);
        canvas.current.height = Math.ceil(viewport.height);
        const task = page.render({
          canvas: canvas.current,
          canvasContext: context,
          viewport,
        });
        rendering = task;
        await task.promise;
        if (stopped) return;
        for (const neighbour of [pageNumber - 1, pageNumber + 1]) {
          if (neighbour >= 1 && neighbour <= document.numPages)
            void document.getPage(neighbour);
        }
      } catch (caught) {
        if (!stopped)
          setError(
            caught instanceof Error ? caught.message : "The PDF page failed.",
          );
      }
    })();
    return () => {
      stopped = true;
      rendering?.cancel();
    };
  }, [document, pageNumber, zoom]);

  useEffect(() => {
    if (!document) return;
    let stopped = false;
    const nearby = [
      pageNumber - 2,
      pageNumber - 1,
      pageNumber,
      pageNumber + 1,
      pageNumber + 2,
    ].filter((number) => number >= 1 && number <= document.numPages);
    void (async () => {
      for (const number of nearby) {
        if (stopped || cache.current.has(number)) continue;
        try {
          const page = await document.getPage(number);
          if (stopped) return;
          const viewport = page.getViewport({ scale: 0.16 });
          if (viewport.width * viewport.height > 16_000_000) continue;
          const image = window.document.createElement("canvas");
          image.width = Math.ceil(viewport.width);
          image.height = Math.ceil(viewport.height);
          const context = image.getContext("2d");
          if (!context) return;
          await page.render({ canvas: image, canvasContext: context, viewport })
            .promise;
          if (stopped) return;
          const thumbnail = image.toDataURL("image/png");
          // Keep encoded thumbnails within the artifact's configured byte budget.
          if (thumbnail.length * 2 > maxBytes) continue;
          cache.current.set(number, thumbnail);
          while (cache.current.size > 10) {
            const oldest = cache.current.keys().next().value;
            if (oldest !== undefined) cache.current.delete(oldest);
          }
          while (
            [...cache.current.values()].reduce(
              (sum, item) => sum + item.length * 2,
              0,
            ) > maxBytes
          ) {
            const oldest = cache.current.keys().next().value;
            if (oldest === undefined) break;
            cache.current.delete(oldest);
          }
          setThumbnails(Object.fromEntries(cache.current));
        } catch {
          // A failed thumbnail does not hide the complete page.
        }
      }
    })();
    return () => {
      stopped = true;
    };
  }, [document, pageNumber, maxBytes]);

  return (
    <div className="rounded border border-slate-300 bg-slate-100 p-3">
      {loading && <p role="status">Preparing PDF revision…</p>}
      {error && <p role="alert">{error}</p>}
      {document && (
        <>
          <div className="mb-3 flex items-center gap-3">
            <button
              type="button"
              disabled={pageNumber <= 1}
              onClick={() => setPageNumber((number) => number - 1)}
            >
              Previous page
            </button>
            <span>
              Page {pageNumber} of {document.numPages}
            </span>
            <button
              type="button"
              disabled={pageNumber >= document.numPages}
              onClick={() => setPageNumber((number) => number + 1)}
            >
              Next page
            </button>
          </div>
          <div className="max-h-[34rem] overflow-auto">
            <canvas
              ref={canvas}
              aria-label={`PDF page ${pageNumber}`}
              className="mx-auto max-w-full bg-white shadow"
            />
          </div>
          <nav
            aria-label="Nearby PDF pages"
            className="mt-3 flex gap-2 overflow-x-auto"
          >
            {Object.entries(thumbnails)
              .map(([number, source]) => [Number(number), source] as const)
              .filter(([number]) => Math.abs(number - pageNumber) <= 2)
              .map(([number, source]) => (
                <button
                  key={number}
                  type="button"
                  aria-label={`Show PDF page ${number}`}
                  aria-current={number === pageNumber ? "page" : undefined}
                  onClick={() => setPageNumber(number)}
                >
                  {/* Local canvas output only; no remote image source exists. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={source}
                    alt=""
                    className="h-16 max-w-14 object-contain"
                  />
                </button>
              ))}
          </nav>
        </>
      )}
    </div>
  );
}
