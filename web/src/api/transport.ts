import {
  safeParse,
  type BaseIssue,
  type BaseSchema,
  type InferOutput,
} from "valibot";
import { vErrorResponse } from "./generated/valibot.gen";

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

const unexpectedMessage = "The service returned an unexpected response.";

function unexpected(status: number): ApiError {
  return new ApiError(status, "UNEXPECTED_RESPONSE", unexpectedMessage);
}

async function parseJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw unexpected(response.status);
  }
}

export class ApiTransport {
  constructor(
    private readonly fetcher: typeof fetch = fetch,
    private readonly readCookie: () => string = () => document.cookie,
  ) {}

  async json<TSchema extends BaseSchema<unknown, unknown, BaseIssue<unknown>>>(
    path: `/api/v1${string}`,
    schema: TSchema,
    options: RequestOptions = {},
  ): Promise<InferOutput<TSchema>> {
    return (await this.jsonWithMetadata(path, schema, options)).data;
  }

  async jsonWithMetadata<
    TSchema extends BaseSchema<unknown, unknown, BaseIssue<unknown>>,
  >(
    path: `/api/v1${string}`,
    schema: TSchema,
    options: RequestOptions = {},
  ): Promise<JsonResult<InferOutput<TSchema>>> {
    const response = await this.request(path, options, "application/json");
    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.toLowerCase().startsWith("application/json")) {
      throw unexpected(response.status);
    }
    const parsed = safeParse(schema, await parseJson(response));
    if (!parsed.success) throw unexpected(response.status);
    const etag = response.headers.get("etag") ?? undefined;
    return { data: parsed.output, ...(etag ? { etag } : {}) };
  }

  async multipart<
    TSchema extends BaseSchema<unknown, unknown, BaseIssue<unknown>>,
  >(
    path: `/api/v1${string}`,
    form: FormData,
    schema: TSchema,
    options: Omit<RequestOptions, "body"> = {},
  ): Promise<InferOutput<TSchema>> {
    return this.json(path, schema, {
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

  async cancel<
    TSchema extends BaseSchema<unknown, unknown, BaseIssue<unknown>>,
  >(
    path: `/api/v1${string}`,
    schema: TSchema,
    signal?: AbortSignal,
  ): Promise<InferOutput<TSchema>> {
    return this.json(path, schema, { csrf: true, method: "DELETE", signal });
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
      const parsed = safeParse(vErrorResponse, await parseJson(response));
      if (parsed.success) {
        throw new ApiError(
          response.status,
          parsed.output.error.code,
          parsed.output.error.message,
        );
      }
    }
    throw unexpected(response.status);
  }
}
