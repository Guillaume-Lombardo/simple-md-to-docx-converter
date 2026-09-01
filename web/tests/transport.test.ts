import { ApiError, ApiTransport, CSRF_COOKIE } from "../src/api/transport";
import { boolean, object } from "valibot";
import { vApiLogoutApiV1LogoutPostResponse } from "../src/api/generated/valibot.gen";

const okSchema = object({ ok: boolean() });

function response(body: BodyInit | null, init: ResponseInit = {}) {
  return new Response(body, init);
}

test("JSON transport sends contract headers and parses a typed response", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    response('{"ok":true}', {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  const api = new ApiTransport(fetcher, () => `${CSRF_COOKIE}=safe%20token`);
  await expect(
    api.json("/api/v1/test", okSchema, {
      body: "{}",
      csrf: true,
      etag: '"v1"',
      idempotencyKey: "key",
      method: "PATCH",
    }),
  ).resolves.toEqual({ ok: true });
  const [, options] = fetcher.mock.calls[0]!;
  const headers = new Headers(options?.headers);
  expect(headers.get("X-CSRF-Token")).toBe("safe token");
  expect(headers.get("If-Match")).toBe('"v1"');
  expect(headers.get("Idempotency-Key")).toBe("key");
  expect(options).toMatchObject({
    cache: "no-store",
    credentials: "same-origin",
    redirect: "error",
  });
});

test("JSON metadata preserves the server ETag", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    response('{"ok":true}', {
      status: 200,
      headers: { "content-type": "application/json", etag: '"v2"' },
    }),
  );
  await expect(
    new ApiTransport(fetcher).jsonWithMetadata("/api/v1/test", okSchema),
  ).resolves.toEqual({ data: { ok: true }, etag: '"v2"' });
});

test("multipart leaves content type to the browser", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    response('{"ok":true}', {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  await new ApiTransport(fetcher).multipart(
    "/api/v1/conversions",
    new FormData(),
    okSchema,
  );
  expect(
    new Headers(fetcher.mock.calls[0]![1]?.headers).has("Content-Type"),
  ).toBe(false);
});

test("download preserves response metadata and accepts abort signals", async () => {
  const result = response("file", {
    status: 200,
    headers: { etag: '"result"' },
  });
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(result);
  const controller = new AbortController();
  await expect(
    new ApiTransport(fetcher).download("/api/v1/file", {
      signal: controller.signal,
    }),
  ).resolves.toBe(result);
  expect(fetcher.mock.calls[0]![1]?.signal).toBe(controller.signal);
});

test("cancellation is a CSRF-protected abortable DELETE", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    response('{"ok":true}', {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  const signal = new AbortController().signal;
  await new ApiTransport(fetcher, () => `${CSRF_COOKIE}=token`).cancel(
    "/api/v1/conversions/job",
    okSchema,
    signal,
  );
  expect(fetcher.mock.calls[0]![1]).toMatchObject({ method: "DELETE", signal });
});

test("stable error envelopes become typed errors", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    response('{"error":{"code":"DENIED","message":"Not allowed"}}', {
      status: 403,
      headers: { "content-type": "application/json" },
    }),
  );
  await expect(
    new ApiTransport(fetcher).json("/api/v1/test", okSchema),
  ).rejects.toMatchObject({
    status: 403,
    code: "DENIED",
    message: "Not allowed",
  });
});

test("malformed JSON envelopes are never reflected", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    response('{"error":{"code":4,"message":"unsafe"}}', {
      status: 400,
      headers: { "content-type": "application/json" },
    }),
  );
  await expect(
    new ApiTransport(fetcher).json("/api/v1/test", okSchema),
  ).rejects.toMatchObject({
    code: "UNEXPECTED_RESPONSE",
    message: "The service returned an unexpected response.",
  });
});

test.each([
  [
    response("oops", {
      status: 502,
      headers: { "content-type": "text/plain" },
    }),
    502,
  ],
  [
    response("not-json", {
      status: 200,
      headers: { "content-type": "text/plain" },
    }),
    200,
  ],
])("unexpected responses fail with a fixed message", async (result, status) => {
  const api = new ApiTransport(vi.fn<typeof fetch>().mockResolvedValue(result));
  await expect(api.json("/api/v1/test", okSchema)).rejects.toEqual(
    new ApiError(
      status,
      "UNEXPECTED_RESPONSE",
      "The service returned an unexpected response.",
    ),
  );
});

test.each(["", "flag", `${CSRF_COOKIE}=%GG`])(
  "missing or malformed CSRF cookies fail before fetch",
  async (cookie) => {
    const fetcher = vi.fn<typeof fetch>();
    await expect(
      new ApiTransport(fetcher, () => cookie).json("/api/v1/test", okSchema, {
        csrf: true,
        method: "DELETE",
      }),
    ).rejects.toMatchObject({ code: "CSRF_MISSING" });
    expect(fetcher).not.toHaveBeenCalled();
  },
);

test("generated void endpoints accept an empty 204 response", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(response(null, { status: 204 }));
  await expect(
    new ApiTransport(fetcher).json(
      "/api/v1/logout",
      vApiLogoutApiV1LogoutPostResponse,
      { method: "POST" },
    ),
  ).resolves.toBeUndefined();
});

test("JSON media types require an exact parsed essence", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    response('{"ok":true}', {
      status: 200,
      headers: { "content-type": "application/json-malicious" },
    }),
  );
  await expect(
    new ApiTransport(fetcher).json("/api/v1/test", okSchema),
  ).rejects.toMatchObject({ code: "UNEXPECTED_RESPONSE" });
});

test("runtime API path validation rejects lookalike prefixes before fetch", async () => {
  const fetcher = vi.fn<typeof fetch>();
  await expect(
    new ApiTransport(fetcher).json("/api/v12/test" as "/api/v1/test", okSchema),
  ).rejects.toMatchObject({ code: "INVALID_API_PATH", status: 0 });
  expect(fetcher).not.toHaveBeenCalled();
});

test.each([
  "/api/v1/../admin",
  "/api/v1/%2e%2e/admin",
  "/api/v1/%252e%252e/admin",
  "/api/v1/%5c..%5cadmin",
  "/api/v1/a/./b",
  "/api/v1/../../outside",
  "//attacker.invalid/api/v1/test",
  "https://attacker.invalid/api/v1/test",
])(
  "API paths reject traversal and origin changes before fetch: %s",
  async (path) => {
    const fetcher = vi.fn<typeof fetch>();
    await expect(
      new ApiTransport(fetcher).json(path as "/api/v1/test", okSchema),
    ).rejects.toMatchObject({ code: "INVALID_API_PATH" });
    expect(fetcher).not.toHaveBeenCalled();
  },
);

test("API paths are normalized before fetch without changing their origin", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    response('{"ok":true}', {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  await new ApiTransport(fetcher).json("/api/v1/test?name=a%20b", okSchema);
  expect(fetcher.mock.calls[0]![0]).toBe("/api/v1/test?name=a%20b");
});

test.each([
  ["not-json", 200],
  ['{"ok":"yes"}', 200],
  ["not-json", 400],
])(
  "malformed JSON or schema output is fixed and non-reflective",
  async (body, status) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      response(body, {
        status,
        headers: { "content-type": "application/json" },
      }),
    );
    await expect(
      new ApiTransport(fetcher).json("/api/v1/test", okSchema),
    ).rejects.toEqual(
      new ApiError(
        status,
        "UNEXPECTED_RESPONSE",
        "The service returned an unexpected response.",
      ),
    );
  },
);
