import { ApiError } from "../api/transport";

export const SESSION_ENDED = "Your session ended. Please sign in again.";

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
