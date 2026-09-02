import { ApiError } from "../api/transport";
import type { IdleSessionPolicyDurationBoundsResponse } from "../api/generated/types.gen";

export const SESSION_ENDED = "Your session ended. Please sign in again.";
const DOCX_MEDIA_TYPE =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export interface TemplateDownload {
  blob: Blob;
  filename: string;
}

export class RequestFence {
  private generation = 0;
  private request?: AbortController;
  private mutationPending = false;

  startRead(): { controller: AbortController; generation: number } {
    this.request?.abort();
    this.request = new AbortController();
    this.generation += 1;
    return { controller: this.request, generation: this.generation };
  }

  startMutation():
    | { controller: AbortController; generation: number }
    | undefined {
    if (this.mutationPending) return undefined;
    this.mutationPending = true;
    return this.startRead();
  }

  current(generation: number): boolean {
    return generation === this.generation;
  }

  finishMutation(generation: number): boolean {
    if (!this.current(generation)) return false;
    this.mutationPending = false;
    return true;
  }

  dispose(): void {
    this.generation += 1;
    this.request?.abort();
    this.mutationPending = false;
  }
}

export function expectedFonts(value: string): string[] {
  return value
    .split(",")
    .map((font) => font.trim())
    .filter(Boolean);
}

export function appendExpectedFonts(form: FormData, value: string): void {
  const fonts = expectedFonts(value);
  if (fonts.length === 0) {
    form.append("expected_fonts", "");
    return;
  }
  for (const font of fonts) form.append("expected_fonts", font);
}

function downloadFilename(response: Response): string | undefined {
  const disposition = response.headers.get("content-disposition");
  if (!disposition) return undefined;
  const encoded = /(?:^|;)\s*filename\*=UTF-8''([^;]+)(?:;|$)/i.exec(
    disposition,
  )?.[1];
  const quoted = /(?:^|;)\s*filename="([^"\\\r\n]+)"(?:;|$)/i.exec(
    disposition,
  )?.[1];
  let filename = quoted;
  if (encoded) {
    try {
      filename = decodeURIComponent(encoded);
    } catch {
      return undefined;
    }
  }
  if (
    !filename ||
    filename.includes("/") ||
    filename.includes("\\") ||
    /[\r\n]/.test(filename)
  )
    return undefined;
  return filename;
}

export async function readTemplateDownload(
  response: Response,
): Promise<TemplateDownload> {
  if (
    response.headers
      .get("content-type")
      ?.split(";", 1)[0]
      ?.trim()
      .toLowerCase() !== DOCX_MEDIA_TYPE ||
    response.headers.get("cache-control")?.toLowerCase() !==
      "private, no-store" ||
    response.headers.get("x-content-type-options")?.toLowerCase() !== "nosniff"
  )
    throw new TypeError("Unexpected template download response.");
  const filename = downloadFilename(response);
  if (!filename?.toLowerCase().endsWith(".docx"))
    throw new TypeError("Unexpected template download filename.");
  return { blob: await response.blob(), filename };
}

export function saveTemplateDownload(
  download: TemplateDownload,
  defer: (callback: () => void) => void = (callback) =>
    window.setTimeout(callback, 0),
): void {
  const url = URL.createObjectURL(download.blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = download.filename;
  try {
    anchor.click();
  } finally {
    defer(() => URL.revokeObjectURL(url));
  }
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function administrationError(
  error: unknown,
  expire: () => void,
  fallback: string,
): string | undefined {
  if (isAbort(error)) return undefined;
  if (
    error instanceof ApiError &&
    (error.status === 401 || error.code === "CSRF_MISSING")
  ) {
    expire();
    return SESSION_ENDED;
  }
  if (error instanceof ApiError && error.status === 412)
    return "This item changed on the server. Review the latest version and try again.";
  if (error instanceof ApiError && error.status === 428)
    return "The current server revision is required. Reload and try again.";
  if (error instanceof ApiError && error.status === 403)
    return "You are not allowed to perform this action.";
  if (error instanceof ApiError && error.status === 413)
    return "The selected file exceeds the configured upload limit.";
  if (error instanceof ApiError && error.status === 409)
    return "The requested value already exists.";
  if (error instanceof ApiError && error.status === 422) return error.message;
  return fallback;
}

export function idleMinutesError(
  label: string,
  raw: string,
  bounds: IdleSessionPolicyDurationBoundsResponse,
  granularity: number,
  absoluteLifetimeSeconds: number,
): string | undefined {
  if (!/^[0-9]+$/.test(raw))
    return `${label} must be a whole number of minutes.`;
  const value = Number(raw);
  if (!Number.isSafeInteger(value))
    return `${label} must be a whole number of minutes.`;
  const effectiveMaximum = effectiveIdleMaximum(
    bounds,
    absoluteLifetimeSeconds,
  );
  if (value < bounds.minimum_minutes || value > effectiveMaximum)
    return `${label} must be between ${bounds.minimum_minutes} and ${effectiveMaximum} minutes.`;
  if ((value - bounds.minimum_minutes) % granularity !== 0)
    return `${label} must use ${granularity}-minute increments starting at ${bounds.minimum_minutes}.`;
  return undefined;
}

export function effectiveIdleMaximum(
  bounds: IdleSessionPolicyDurationBoundsResponse,
  absoluteLifetimeSeconds: number,
): number {
  return Math.min(
    bounds.maximum_minutes,
    Math.floor(absoluteLifetimeSeconds / 60),
  );
}
