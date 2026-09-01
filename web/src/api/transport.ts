import type { ErrorResponse } from "./generated";

export const CSRF_COOKIE = "__Host-md_converter_csrf";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface RequestOptions {
  body?: BodyInit;
  csrf?: boolean;
  etag?: string;
  idempotencyKey?: string;
  method?: "DELETE" | "GET" | "PATCH" | "POST" | "PUT";
  signal?: AbortSignal;
}

export interface JsonResult<T> {
  data: T;
  etag?: string;
}

function cookieValue(cookie: string, name: string): string | undefined {
  for (const part of cookie.split(";")) {
    const separator = part.indexOf("=");
    if (separator < 0 || part.slice(0, separator).trim() !== name) continue;
    try {
      return decodeURIComponent(part.slice(separator + 1));
    } catch {
      return undefined;
    }
  }
  return undefined;
}

function isErrorEnvelope(value: unknown): value is ErrorResponse {
  if (typeof value !== "object" || value === null || !("error" in value))
    return false;
  const error = value.error;
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    typeof error.code === "string" &&
    "message" in error &&
    typeof error.message === "string"
  );
}

export class ApiTransport {
  constructor(
    private readonly fetcher: typeof fetch = fetch,
    private readonly readCookie: () => string = () => document.cookie,
  ) {}

  async json<T>(
    path: `/api/v1${string}`,
    options: RequestOptions = {},
  ): Promise<T> {
    return (await this.jsonWithMetadata<T>(path, options)).data;
  }

  async jsonWithMetadata<T>(
    path: `/api/v1${string}`,
    options: RequestOptions = {},
  ): Promise<JsonResult<T>> {
    const response = await this.request(path, options, "application/json");
    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.toLowerCase().startsWith("application/json")) {
      throw new ApiError(
        response.status,
        "UNEXPECTED_RESPONSE",
        "The service returned an unexpected response.",
      );
    }
    const etag = response.headers.get("etag") ?? undefined;
    return { data: (await response.json()) as T, ...(etag ? { etag } : {}) };
  }

  async multipart<T>(
    path: `/api/v1${string}`,
    form: FormData,
    options: Omit<RequestOptions, "body"> = {},
  ): Promise<T> {
    return this.json<T>(path, {
      ...options,
      body: form,
      method: options.method ?? "POST",
    });
  }

  async download(
    path: `/api/v1${string}`,
    options: RequestOptions = {},
  ): Promise<Response> {
    return this.request(path, options, "application/octet-stream");
  }

  async cancel<T>(path: `/api/v1${string}`, signal?: AbortSignal): Promise<T> {
    return this.json<T>(path, { csrf: true, method: "DELETE", signal });
  }

  private async request(
    path: `/api/v1${string}`,
    options: RequestOptions,
    accept: string,
  ): Promise<Response> {
    const headers = new Headers({ Accept: accept });
    if (options.csrf) {
      const csrf = cookieValue(this.readCookie(), CSRF_COOKIE);
      if (!csrf)
        throw new ApiError(
          0,
          "CSRF_MISSING",
          "Your secure session is unavailable. Please sign in again.",
        );
      headers.set("X-CSRF-Token", csrf);
    }
    if (options.etag) headers.set("If-Match", options.etag);
    if (options.idempotencyKey)
      headers.set("Idempotency-Key", options.idempotencyKey);
    if (typeof options.body === "string")
      headers.set("Content-Type", "application/json");

    const response = await this.fetcher(path, {
      body: options.body,
      cache: "no-store",
      credentials: "same-origin",
      headers,
      method: options.method ?? "GET",
      redirect: "error",
      signal: options.signal,
    });
    if (response.ok) return response;

    const contentType = response.headers.get("content-type") ?? "";
    if (contentType.toLowerCase().startsWith("application/json")) {
      const envelope: unknown = await response.json();
      if (isErrorEnvelope(envelope)) {
        throw new ApiError(
          response.status,
          envelope.error.code,
          envelope.error.message,
        );
      }
    }
    throw new ApiError(
      response.status,
      "UNEXPECTED_RESPONSE",
      "The service returned an unexpected response.",
    );
  }
}
