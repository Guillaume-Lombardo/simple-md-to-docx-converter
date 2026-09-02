import type {
  ConversionOptionsResponse,
  ConversionResponse,
  TemplateResponse,
} from "../src/api/generated/types.gen";
import { ApiError, type ApiTransport } from "../src/api/transport";
import {
  ConversionController,
  nextPollDelay,
  statusPresentation,
  validateSource,
} from "../src/conversion/controller";

const template = (
  overrides: Partial<TemplateResponse> = {},
): TemplateResponse => ({
  current_version_id: "00000000-0000-4000-8000-000000000102",
  description: "Quarterly report styles",
  id: "00000000-0000-4000-8000-000000000101",
  name: "Report",
  owner_id: "00000000-0000-4000-8000-000000000001",
  owner_username: "Alice",
  revision: 1,
  status: "active",
  ...overrides,
});

const options = (
  overrides: Partial<ConversionOptionsResponse> = {},
): ConversionOptionsResponse => ({
  conversion_upload_max_bytes: 1_000_000,
  resolved_template: null,
  selection_source: "pandoc_default",
  template_version_id: null,
  ...overrides,
});

const job = (
  overrides: Partial<ConversionResponse> = {},
): ConversionResponse => ({
  attempt: 0,
  cancel_requested: false,
  component_versions: [],
  correlation_id: "correlation",
  created_at: "2026-09-02T08:00:00Z",
  error_code: null,
  error_message: null,
  expires_at: null,
  id: "00000000-0000-4000-8000-000000000201",
  output: "docx",
  owner_id: "00000000-0000-4000-8000-000000000001",
  progress: 0,
  state: "queued",
  step: "queued",
  template_id: null,
  template_mode: "pandoc-default",
  template_version_id: null,
  updated_at: "2026-09-02T08:00:00Z",
  ...overrides,
});

const accepted = (data: ConversionResponse = job()) => ({
  data,
  location: `/api/v1/conversions/${data.id}`,
  retryAfterSeconds: 1,
  status: 202,
});

function api(overrides: Partial<ApiTransport> = {}): ApiTransport {
  return {
    json: vi.fn(),
    multipart: vi.fn(),
    multipartWithMetadata: vi.fn(),
    cancel: vi.fn(),
    download: vi.fn(),
    ...overrides,
  } as unknown as ApiTransport;
}

async function loadedController(
  transport: ApiTransport,
  extra: ConstructorParameters<typeof ConversionController> = [transport],
) {
  const controller = new ConversionController(...extra);
  await controller.load();
  return controller;
}

test("source validation and status presentation preserve all user-visible states", () => {
  expect(validateSource(undefined, 10)).toBe("Choose a Markdown or ZIP file.");
  expect(validateSource(new File(["x"], "source.txt"), 10)).toMatch(/ending/);
  expect(validateSource(new File([], "source.md"), 10)).toMatch(/empty/);
  expect(validateSource(new File(["long"], "source.zip"), 2)).toMatch(
    /upload limit/,
  );
  expect(validateSource(new File(["ok"], "SOURCE.MD"), 10)).toBeUndefined();
  expect(nextPollDelay(1_000)).toBe(1_600);
  expect(nextPollDelay(9_000)).toBe(10_000);
  expect(statusPresentation(job())).toBe("Your conversion is queued.");
  expect(
    statusPresentation(job({ state: "running", step: "docx", progress: 40 })),
  ).toBe("Creating the DOCX file (40%).");
  expect(
    statusPresentation(job({ state: "running", cancel_requested: true })),
  ).toMatch(/Cancellation requested/);
  expect(
    statusPresentation(
      job({ state: "failed", error_message: "Safe failure." }),
    ),
  ).toBe("Safe failure.");
  expect(statusPresentation(job({ state: "failed" }))).toBe(
    "The conversion failed.",
  );
  expect(
    statusPresentation(job({ state: "running", step: "unknown", progress: 3 })),
  ).toBe("Processing (3%).");
  expect(statusPresentation(job({ state: "cancelled" }))).toMatch(/cancelled/);
  expect(statusPresentation(job({ state: "expired" }))).toMatch(/expired/);
  expect(statusPresentation(job({ state: "succeeded" }))).toMatch(
    /ready to download/,
  );
});

test("request-defining input edge cases are explicit and subscribers can detach", async () => {
  const transport = api({
    json: vi
      .fn()
      .mockResolvedValueOnce(options())
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
  });
  const controller = await loadedController(transport);
  const listener = vi.fn();
  const unsubscribe = controller.subscribe(listener);
  controller.setSource([
    new File(["one"], "one.md"),
    new File(["two"], "two.md"),
  ]);
  expect(controller.snapshot().error).toMatch(/exactly one/);
  controller.setSource(null);
  expect(controller.snapshot().source).toBeUndefined();
  controller.setOutput("docx");
  controller.chooseTemplate(template({ current_version_id: null }));
  expect(controller.snapshot().error).toMatch(/no active version/);
  unsubscribe();
  controller.choosePandocDefault();
  expect(listener).toHaveBeenCalledTimes(3);
  controller.dispose();
});

test("load applies authoritative Pandoc, preferred, and fallback snapshots with recent jobs", async () => {
  const json = vi
    .fn()
    .mockResolvedValueOnce(options())
    .mockResolvedValueOnce({ items: [job()], limit: 10, offset: 0, total: 1 })
    .mockResolvedValueOnce(
      options({
        resolved_template: template(),
        selection_source: "preferred",
        template_version_id: template().current_version_id,
      }),
    )
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
    .mockResolvedValueOnce(
      options({
        resolved_template: template({ name: "Fallback" }),
        selection_source: "system_fallback",
        template_version_id: template().current_version_id,
      }),
    )
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 });
  const controller = new ConversionController(api({ json }));
  await controller.load();
  expect(controller.snapshot()).toMatchObject({
    phase: "ready",
    maximumBytes: 1_000_000,
    selection: undefined,
    recent: [{ state: "queued" }],
  });
  expect(json).toHaveBeenCalledWith(
    "/api/v1/conversions?offset=0&limit=10",
    expect.anything(),
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  await controller.load();
  expect(controller.snapshot().selection).toMatchObject({
    name: "Report",
    source: "preferred",
  });
  await controller.load();
  expect(controller.snapshot().selection).toMatchObject({
    name: "Fallback",
    source: "system_fallback",
  });
});

test("load fails closed for inconsistent template snapshots, backend loss, and session expiry", async () => {
  const expire = vi.fn();
  const json = vi
    .fn()
    .mockResolvedValueOnce(options({ selection_source: "preferred" }))
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
    .mockRejectedValueOnce(new TypeError("private network detail"))
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
    .mockRejectedValueOnce(
      new ApiError(401, "AUTHENTICATION_REQUIRED", "ignored"),
    )
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 });
  const controller = new ConversionController(api({ json }), expire);
  await controller.load();
  expect(controller.snapshot().phase).toBe("unavailable");
  await controller.load();
  expect(controller.snapshot().phase).toBe("unavailable");
  await controller.load();
  expect(expire).toHaveBeenCalledOnce();
});

test("template search fences late requests and selection changes invalidate request identity", async () => {
  let resolveFirst!: (value: unknown) => void;
  const json = vi
    .fn()
    .mockResolvedValueOnce(options())
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    )
    .mockResolvedValueOnce({
      items: [template({ name: "Newest" })],
      limit: 20,
      offset: 0,
      total: 1,
    });
  const controller = await loadedController(api({ json }));
  const first = controller.searchTemplates("Old");
  await controller.searchTemplates("Newest");
  resolveFirst({
    items: [template({ name: "Stale" })],
    limit: 20,
    offset: 0,
    total: 1,
  });
  await first;
  expect(controller.snapshot().templates.map((item) => item.name)).toEqual([
    "Newest",
  ]);
  controller.chooseTemplate(template({ name: "Chosen" }));
  expect(controller.snapshot().selection).toMatchObject({
    name: "Chosen",
    source: "selected",
  });
  controller.choosePandocDefault();
  expect(controller.snapshot().selection).toBeUndefined();
});

test("template search maps backend loss safely and delegates authoritative expiry", async () => {
  const expire = vi.fn();
  const json = vi
    .fn()
    .mockResolvedValueOnce(options())
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
    .mockRejectedValueOnce(new TypeError("private endpoint"))
    .mockRejectedValueOnce(
      new ApiError(401, "AUTHENTICATION_REQUIRED", "ignored"),
    );
  const transport = api({ json });
  const controller = await loadedController(transport, [transport, expire]);
  await controller.searchTemplates("");
  expect(controller.snapshot().error).toBe(
    "Templates could not be loaded. Try the search again.",
  );
  await controller.searchTemplates("again");
  expect(expire).toHaveBeenCalledOnce();
});

test("ambiguous and server-failed submissions reuse one key while client rejection resets it", async () => {
  const multipart = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("offline"))
    .mockRejectedValueOnce(
      new ApiError(503, "CONVERSION_STORAGE_UNAVAILABLE", "Try later."),
    )
    .mockResolvedValueOnce({ data: job(), status: 200 })
    .mockResolvedValueOnce(
      accepted(job({ state: "succeeded", progress: 100, step: "complete" })),
    )
    .mockRejectedValueOnce(
      new ApiError(422, "JOB_REQUEST_INVALID", "Choose another file."),
    )
    .mockResolvedValueOnce(
      accepted(
        job({
          id: "00000000-0000-4000-8000-000000000202",
          state: "succeeded",
        }),
      ),
    );
  const transport = api({
    json: vi
      .fn()
      .mockResolvedValueOnce(options())
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
    multipartWithMetadata:
      multipart as unknown as ApiTransport["multipartWithMetadata"],
  });
  let key = 0;
  const controller = await loadedController(transport, [
    transport,
    vi.fn(),
    () => `key-${++key}`,
  ]);
  controller.setSource([new File(["# source"], "source.md")]);
  await controller.submit();
  expect(controller.snapshot().error).toMatch(/same request key/);
  await controller.submit();
  expect(controller.snapshot().error).toBe(
    "Try later. Retrying will reuse the same request key.",
  );
  await controller.submit();
  await controller.submit();
  controller.setOutput("pdf");
  await controller.submit();
  await controller.submit();
  expect(multipart.mock.calls.map((call) => call[3].idempotencyKey)).toEqual([
    "key-1",
    "key-1",
    "key-1",
    "key-1",
    "key-2",
    "key-3",
  ]);
  expect(multipart.mock.calls[5]![1].get("output")).toBe("pdf");
});

test.each([
  [
    "owner quota 429",
    new ApiError(
      429,
      "CONVERSION_USER_QUOTA_EXCEEDED",
      "Too many active conversions.",
    ),
    false,
  ],
  [
    "global capacity 503",
    new ApiError(
      503,
      "CONVERSION_QUEUE_CAPACITY_EXCEEDED",
      "The conversion queue is at capacity.",
    ),
    false,
  ],
  [
    "source storage 503",
    new ApiError(
      503,
      "CONVERSION_STORAGE_UNAVAILABLE",
      "Source storage is unavailable.",
    ),
    true,
  ],
  [
    "unexpected server 500",
    new ApiError(500, "INTERNAL_SERVER_ERROR", "The service failed."),
    true,
  ],
] as const)(
  "%s uses the correct submission request identity",
  async (_label, rejection, reusesKey) => {
    const multipart = vi
      .fn()
      .mockRejectedValueOnce(rejection)
      .mockResolvedValueOnce(accepted(job({ state: "succeeded" })));
    const transport = api({
      json: vi
        .fn()
        .mockResolvedValueOnce(options())
        .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
      multipartWithMetadata: multipart,
    });
    let key = 0;
    const controller = await loadedController(transport, [
      transport,
      vi.fn(),
      () => `key-${++key}`,
    ]);
    controller.setSource([new File(["# source"], "source.md")]);

    await controller.submit();
    expect(controller.snapshot().error).toContain(rejection.message);
    expect(controller.snapshot().error?.includes("same request key")).toBe(
      reusesKey,
    );
    await controller.submit();

    expect(multipart.mock.calls.map((call) => call[3].idempotencyKey)).toEqual(
      reusesKey ? ["key-1", "key-1"] : ["key-1", "key-2"],
    );
  },
);

test("accepted Retry-After schedules the initial status poll", async () => {
  const scheduled: Array<number> = [];
  const transport = api({
    json: vi
      .fn()
      .mockResolvedValueOnce(options())
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
      .mockResolvedValueOnce(job({ state: "running", step: "validating" })),
    multipartWithMetadata: vi.fn().mockResolvedValue({
      data: job({ state: "queued" }),
      location: `/api/v1/conversions/${job().id}`,
      retryAfterSeconds: 5,
      status: 202,
    }),
  });
  const controller = await loadedController(transport, [
    transport,
    vi.fn(),
    () => "key",
    (_callback, delay) => {
      scheduled.push(delay);
      return 1 as unknown as ReturnType<typeof setTimeout>;
    },
  ]);
  controller.setSource([new File(["# source"], "source.md")]);
  await controller.submit();
  expect(scheduled).toEqual([5_000]);
  expect(transport.json).toHaveBeenCalledTimes(2);
});

test("the scheduled initial poll publishes a terminal conversion failure", async () => {
  vi.useFakeTimers();
  try {
    const failed = job({
      error_code: "PDF_LIMIT_EXCEEDED",
      error_message: "The PDF exceeds configured limits.",
      state: "failed",
      step: "validating",
    });
    const json = vi
      .fn()
      .mockResolvedValueOnce(options())
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
      .mockResolvedValueOnce(failed);
    const transport = api({
      json,
      multipartWithMetadata: vi
        .fn()
        .mockResolvedValue(accepted(job({ state: "queued" }))),
    });
    const controller = await loadedController(transport);
    controller.setSource([new File(["# source"], "source.md")]);
    await controller.submit();

    expect(controller.snapshot().active?.state).toBe("queued");
    await vi.advanceTimersByTimeAsync(1_000);

    expect(json).toHaveBeenLastCalledWith(
      `/api/v1/conversions/${job().id}`,
      expect.anything(),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(controller.snapshot().active).toEqual(failed);
  } finally {
    vi.useRealTimers();
  }
});

test("a new submission schedules polling after reopening an earlier success", async () => {
  const earlier = job({ state: "succeeded", progress: 100, step: "complete" });
  const next = job({
    id: "00000000-0000-4000-8000-000000000302",
    state: "queued",
  });
  const scheduled: Array<{ callback: () => void; delay: number }> = [];
  const json = vi
    .fn()
    .mockResolvedValueOnce(options())
    .mockResolvedValueOnce({ items: [earlier], limit: 10, offset: 0, total: 1 })
    .mockResolvedValueOnce(earlier)
    .mockResolvedValueOnce(
      job({
        error_code: "PDF_LIMIT_EXCEEDED",
        error_message: "The PDF exceeds configured limits.",
        id: next.id,
        state: "failed",
        step: "validating",
      }),
    );
  const transport = api({
    json,
    multipartWithMetadata: vi.fn().mockResolvedValue(accepted(next)),
  });
  const controller = await loadedController(transport, [
    transport,
    vi.fn(),
    () => "next-key",
    (callback, delay) => {
      scheduled.push({ callback, delay });
      return scheduled.length as unknown as ReturnType<typeof setTimeout>;
    },
    vi.fn(),
  ]);
  await controller.openJob(earlier.id);
  controller.setSource([new File(["# next"], "next.md")]);
  controller.setOutput("pdf");
  await controller.submit();

  expect(controller.snapshot().active?.id).toBe(next.id);
  expect(scheduled.at(-1)?.delay).toBe(1_000);
  scheduled.at(-1)!.callback();
  await vi.waitFor(() =>
    expect(controller.snapshot().active).toMatchObject({
      error_code: "PDF_LIMIT_EXCEEDED",
      id: next.id,
      state: "failed",
    }),
  );
});

test("polling calls a browser-like timer cancellation without a receiver", async () => {
  let scheduled!: () => void;
  const timer = 17 as unknown as ReturnType<typeof setTimeout>;
  const cancelSchedule = vi.fn(function browserCancel(
    this: unknown,
    received: ReturnType<typeof setTimeout>,
  ) {
    expect(this).toBeUndefined();
    expect(received).toBe(timer);
  });
  const transport = api({
    json: vi
      .fn()
      .mockResolvedValueOnce(options())
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
      .mockResolvedValueOnce(job({ state: "succeeded" })),
    multipartWithMetadata: vi
      .fn()
      .mockResolvedValue(accepted(job({ state: "queued" }))),
  });
  const controller = await loadedController(transport, [
    transport,
    vi.fn(),
    () => "key",
    (callback) => {
      scheduled = callback;
      return timer;
    },
    cancelSchedule,
  ]);
  controller.setSource([new File(["# source"], "source.md")]);
  await controller.submit();

  scheduled();
  await vi.waitFor(() =>
    expect(controller.snapshot().active?.state).toBe("succeeded"),
  );
  expect(cancelSchedule).toHaveBeenCalledOnce();
});

test.each([
  ["non-202 status", { status: 200 }],
  ["missing Location", { location: undefined }],
  ["empty Location", { location: " " }],
  ["foreign job Location", { location: "/api/v1/conversions/other" }],
  ["missing Retry-After", { retryAfterSeconds: undefined }],
] as const)(
  "accepted responses reject %s without activating a job",
  async (_label, override) => {
    const multipart = vi.fn().mockResolvedValue({ ...accepted(), ...override });
    const transport = api({
      json: vi
        .fn()
        .mockResolvedValueOnce(options())
        .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
      multipartWithMetadata: multipart,
    });
    const controller = await loadedController(transport, [
      transport,
      vi.fn(),
      () => "stable-key",
    ]);
    controller.setSource([new File(["# source"], "source.md")]);
    await controller.submit();
    await controller.submit();
    expect(controller.snapshot().active).toBeUndefined();
    expect(controller.snapshot().error).toMatch(/same request key/);
    expect(multipart.mock.calls.map((call) => call[3].idempotencyKey)).toEqual([
      "stable-key",
      "stable-key",
    ]);
  },
);

test("changing request input aborts and clears a pending submission", async () => {
  let aborted = false;
  const multipart = vi.fn(
    (_path, _form, _schema, request: { signal: AbortSignal }) =>
      new Promise((_resolve, reject) => {
        request.signal.addEventListener("abort", () => {
          aborted = true;
          reject(new DOMException("aborted", "AbortError"));
        });
      }),
  );
  const transport = api({
    json: vi
      .fn()
      .mockResolvedValueOnce(options())
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
    multipartWithMetadata:
      multipart as unknown as ApiTransport["multipartWithMetadata"],
  });
  const controller = await loadedController(transport);
  controller.setSource([new File(["# source"], "source.md")]);
  const pending = controller.submit();
  expect(controller.snapshot().submitting).toBe(true);
  controller.setOutput("pdf");
  await pending;
  expect(aborted).toBe(true);
  expect(controller.snapshot().submitting).toBe(false);
});

test("selected submissions poll after acceptance, suppress duplicates, and ignore superseded responses", async () => {
  let resolveSubmission!: (value: {
    data: ConversionResponse;
    location: string;
    retryAfterSeconds: number;
    status: number;
  }) => void;
  const multipart = vi.fn(
    () =>
      new Promise<ReturnType<typeof accepted>>((resolve) => {
        resolveSubmission = resolve;
      }),
  );
  const json = vi
    .fn()
    .mockResolvedValueOnce(options())
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
    .mockResolvedValueOnce(job({ state: "succeeded", progress: 100 }));
  const transport = api({ json, multipartWithMetadata: multipart });
  const controller = await loadedController(transport, [
    transport,
    vi.fn(),
    () => "key",
    (callback) => {
      queueMicrotask(callback);
      return 1 as unknown as ReturnType<typeof setTimeout>;
    },
  ]);
  controller.setSource([new File(["# source"], "source.md")]);
  controller.chooseTemplate(template());
  const first = controller.submit();
  await controller.submit();
  expect(multipart).toHaveBeenCalledOnce();
  const form = (
    multipart.mock.calls as unknown as Array<[string, FormData]>
  )[0]![1];
  expect(form.get("template_id")).toBe(template().id);
  expect(form.get("template_version_id")).toBe(template().current_version_id);
  resolveSubmission(accepted(job({ state: "running", step: "validating" })));
  await first;
  await vi.waitFor(() =>
    expect(controller.snapshot().active?.state).toBe("succeeded"),
  );
  expect(json).toHaveBeenCalledWith(
    `/api/v1/conversions/${job().id}`,
    expect.anything(),
    expect.anything(),
  );

  let resolveStale!: (value: ConversionResponse) => void;
  json.mockImplementationOnce(
    () =>
      new Promise<ConversionResponse>((resolve) => {
        resolveStale = resolve;
      }),
  );
  const stale = controller.openJob("00000000-0000-4000-8000-000000000301");
  controller.dispose();
  resolveStale(job({ id: "00000000-0000-4000-8000-000000000301" }));
  await stale;
  expect(controller.snapshot().active?.id).toBe(job().id);
});

test("polling backs off, recovers transient failures, cancels through terminal state, and fences jobs", async () => {
  const scheduled: Array<{ callback: () => void; delay: number }> = [];
  const json = vi
    .fn()
    .mockResolvedValueOnce(options())
    .mockResolvedValueOnce({ items: [job()], limit: 10, offset: 0, total: 1 })
    .mockResolvedValueOnce(
      job({ state: "running", step: "validating", progress: 20 }),
    )
    .mockRejectedValueOnce(new ApiError(503, "STORAGE", "Storage unavailable."))
    .mockResolvedValueOnce(
      job({ state: "running", cancel_requested: true, progress: 40 }),
    )
    .mockResolvedValueOnce(job({ state: "cancelled", progress: 40 }));
  const cancel = vi
    .fn()
    .mockResolvedValue(
      job({ state: "running", cancel_requested: true, progress: 40 }),
    );
  const transport = api({ json, cancel });
  const controller = await loadedController(transport, [
    transport,
    vi.fn(),
    () => "key",
    (callback, delay) => {
      scheduled.push({ callback, delay });
      return scheduled.length as unknown as ReturnType<typeof setTimeout>;
    },
    vi.fn(),
  ]);
  await controller.openJob(job().id);
  expect(scheduled.at(-1)?.delay).toBe(1_600);
  scheduled.pop()!.callback();
  await vi.waitFor(() =>
    expect(controller.snapshot().error).toBe(
      "Storage unavailable. Polling will continue.",
    ),
  );
  expect(scheduled.at(-1)?.delay).toBe(2_560);
  scheduled.pop()!.callback();
  await vi.waitFor(() =>
    expect(controller.snapshot().active?.cancel_requested).toBe(true),
  );
  await controller.openJob(job().id);
  await controller.cancel();
  expect(cancel).not.toHaveBeenCalled();
  expect(controller.snapshot().active?.state).toBe("cancelled");
});

test("cancellation failures resume polling and successful downloads require safe server filenames", async () => {
  const schedule = vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>);
  const cancellation = vi.fn().mockRejectedValue(new TypeError("offline"));
  const response = new Response("bytes", {
    headers: {
      "Cache-Control": "private, no-store",
      "Content-Disposition": 'attachment; filename="source.docx"',
      "Content-Type": "application/octet-stream",
      "X-Content-Type-Options": "nosniff",
    },
  });
  const transport = api({
    json: vi
      .fn()
      .mockResolvedValueOnce(options())
      .mockResolvedValueOnce({
        items: [job({ state: "running" })],
        limit: 10,
        offset: 0,
        total: 1,
      })
      .mockResolvedValueOnce(job({ state: "running" })),
    cancel: cancellation,
    download: vi.fn().mockResolvedValue(response),
  });
  const controller = await loadedController(transport, [
    transport,
    vi.fn(),
    () => "key",
    schedule,
  ]);
  await controller.openJob(job().id);
  await controller.cancel();
  expect(controller.snapshot().error).toMatch(/Cancellation/);
  expect(schedule).toHaveBeenCalled();

  const succeededTransport = api({
    json: vi
      .fn()
      .mockResolvedValueOnce(options())
      .mockResolvedValueOnce({
        items: [job({ state: "succeeded" })],
        limit: 10,
        offset: 0,
        total: 1,
      })
      .mockResolvedValueOnce(job({ state: "succeeded" })),
    download: vi.fn().mockResolvedValue(response),
  });
  const succeeded = await loadedController(succeededTransport);
  await succeeded.openJob(job().id);
  const result = await succeeded.download();
  expect(result?.filename).toBe("source.docx");
  expect(await result?.blob.text()).toBe("bytes");

  (
    succeededTransport.download as ReturnType<typeof vi.fn>
  ).mockResolvedValueOnce(
    new Response("private", { headers: { "Content-Type": "text/plain" } }),
  );
  expect(await succeeded.download()).toBeUndefined();
  expect(succeeded.snapshot().error).toBe(
    "The service returned an unexpected response.",
  );
});

test("download honors encoded filenames, rejects unsafe names, and expires on 401", async () => {
  const expire = vi.fn();
  const download = vi
    .fn()
    .mockResolvedValueOnce(
      new Response("bytes", {
        headers: {
          "Cache-Control": "private, no-store",
          "Content-Disposition":
            "attachment; filename*=UTF-8''report%20one.pdf",
          "Content-Type": "application/octet-stream",
          "X-Content-Type-Options": "nosniff",
        },
      }),
    )
    .mockResolvedValueOnce(
      new Response("bytes", {
        headers: {
          "Cache-Control": "private, no-store",
          "Content-Disposition": 'attachment; filename="../private.pdf"',
          "Content-Type": "application/octet-stream",
          "X-Content-Type-Options": "nosniff",
        },
      }),
    )
    .mockRejectedValueOnce(
      new ApiError(401, "AUTHENTICATION_REQUIRED", "ignored"),
    );
  const json = vi
    .fn()
    .mockResolvedValueOnce(options())
    .mockResolvedValueOnce({
      items: [job({ state: "succeeded" })],
      limit: 10,
      offset: 0,
      total: 1,
    })
    .mockResolvedValue(job({ state: "succeeded" }));
  const transport = api({ json, download });
  const controller = await loadedController(transport, [transport, expire]);
  expect(await controller.download()).toBeUndefined();
  await controller.openJob(job().id);
  expect((await controller.download())?.filename).toBe("report one.pdf");
  expect(await controller.download()).toBeUndefined();
  expect(controller.snapshot().error).toMatch(/unexpected response/);
  expect(await controller.download()).toBeUndefined();
  expect(expire).toHaveBeenCalledOnce();
});

test("polling stops on missing jobs and delegates cancellation expiry", async () => {
  const expire = vi.fn();
  const schedule = vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>);
  const json = vi
    .fn()
    .mockResolvedValueOnce(options())
    .mockResolvedValueOnce({
      items: [job({ state: "running" })],
      limit: 10,
      offset: 0,
      total: 1,
    })
    .mockRejectedValueOnce(new ApiError(404, "JOB_NOT_FOUND", "Not found."));
  const cancel = vi
    .fn()
    .mockRejectedValue(new ApiError(401, "AUTHENTICATION_REQUIRED", "ignored"));
  const transport = api({ json, cancel });
  const controller = await loadedController(transport, [
    transport,
    expire,
    () => "key",
    schedule,
  ]);
  await controller.openJob(job().id);
  expect(controller.snapshot().error).toBe(
    "Not found. Polling has stopped. Reopen the conversion to try again.",
  );
  expect(schedule).not.toHaveBeenCalled();
  await controller.cancel();
  expect(expire).toHaveBeenCalledOnce();
});

test("activating another job clears cancellation while fencing the stale result", async () => {
  const first = job({ state: "running" });
  const second = job({
    id: "00000000-0000-4000-8000-000000000302",
    state: "running",
  });
  let resolveCancellation!: (value: ConversionResponse) => void;
  const cancellation = vi.fn(
    () =>
      new Promise<ConversionResponse>((resolve) => {
        resolveCancellation = resolve;
      }),
  );
  const transport = api({
    cancel: cancellation,
    json: vi
      .fn()
      .mockResolvedValueOnce(options())
      .mockResolvedValueOnce({
        items: [first, second],
        limit: 10,
        offset: 0,
        total: 2,
      })
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(second),
  });
  const controller = await loadedController(transport, [
    transport,
    vi.fn(),
    () => "key",
    vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>),
  ]);
  await controller.openJob(first.id);
  const staleCancellation = controller.cancel();
  expect(controller.snapshot().cancelling).toBe(true);

  await controller.openJob(second.id);
  expect(controller.snapshot().active?.id).toBe(second.id);
  expect(controller.snapshot().cancelling).toBe(false);
  resolveCancellation(job({ state: "cancelled" }));
  await staleCancellation;
  expect(controller.snapshot().active?.id).toBe(second.id);
  expect(controller.snapshot().cancelling).toBe(false);
});

test("authoritative 401 during an authenticated operation expires once without replay", async () => {
  const expire = vi.fn();
  const multipart = vi
    .fn()
    .mockRejectedValue(new ApiError(401, "AUTHENTICATION_REQUIRED", "ignored"));
  const transport = api({
    json: vi
      .fn()
      .mockResolvedValueOnce(options())
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
    multipartWithMetadata: multipart,
  });
  const controller = await loadedController(transport, [transport, expire]);
  controller.setSource([new File(["# source"], "source.md")]);
  await controller.submit();
  await controller.submit();
  expect(expire).toHaveBeenCalledOnce();
  expect(multipart).toHaveBeenCalledOnce();
});
