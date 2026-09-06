import type {
  ReversionCapabilitiesResponse,
  ReversionResponse,
} from "../src/api/generated/types.gen";
import { ApiError, type ApiTransport } from "../src/api/transport";
import {
  isCancellable,
  nextPollDelay,
  ReversionController,
  statusPresentation,
  validateCapabilities,
  validateSource,
} from "../src/reversion/controller";

const capabilities = (
  overrides: Partial<ReversionCapabilitiesResponse> = {},
): ReversionCapabilitiesResponse => ({
  admission: {
    csv_policy: "bounded text",
    extension_is_hint: true,
    mismatch_policy: "reject",
    scanner_order: "scan first",
    undetected_policy: "reject",
  },
  execution: { hosted_fallback: false, local: true, ocr: false },
  format_families: [
    {
      content_detection: "signature",
      detected_formats: ["docx"],
      extensions: [".docx", ".docm"],
      family: "word",
      selected_parser_format: null,
    },
    {
      content_detection: "signature",
      detected_formats: ["pdf"],
      extensions: [".pdf"],
      family: "pdf",
      selected_parser_format: null,
    },
  ],
  maximum_upload_bytes: 1_000,
  pdf: {
    contract: "text extraction only",
    document_model_available: false,
    embedded_assets_available: false,
    image_preservation: false,
    mixed_or_image_only_pages: "reject as needs_ocr",
    warning: "Images are not preserved",
  },
  result_package_modes: ["markdown", "markdown_with_assets"],
  schema_version: 1,
  ...overrides,
});

const job = (
  overrides: Partial<ReversionResponse> = {},
): ReversionResponse => ({
  attempt: 0,
  cancel_requested: false,
  component_versions: [],
  correlation_id: "correlation",
  created_at: "2026-09-06T08:00:00Z",
  detected_format: "docx",
  error_code: null,
  error_message: null,
  expires_at: null,
  id: "00000000-0000-4000-8000-000000000201",
  owner_id: "00000000-0000-4000-8000-000000000001",
  result_mode: null,
  result_size: null,
  source_extension: ".docx",
  source_family: "word",
  source_stem: "report",
  state: "queued",
  step: "queued",
  updated_at: "2026-09-06T08:00:00Z",
  ...overrides,
});

function api(overrides: Partial<ApiTransport> = {}): ApiTransport {
  return {
    cancel: vi.fn(),
    download: vi.fn(),
    json: vi.fn(),
    multipart: vi.fn(),
    multipartWithMetadata: vi.fn(),
    ...overrides,
  } as unknown as ApiTransport;
}

async function loadedController(transport: ApiTransport) {
  const controller = new ReversionController(transport);
  await controller.load();
  return controller;
}

test("capability and source validation derive every client hint from the server", () => {
  expect(validateCapabilities(capabilities())).toEqual([
    ".docx",
    ".docm",
    ".pdf",
  ]);
  expect(() =>
    validateCapabilities(capabilities({ schema_version: 2 })),
  ).toThrow(/Unsupported/);
  expect(() =>
    validateCapabilities(
      capabilities({
        execution: { hosted_fallback: true, local: true, ocr: false },
      }),
    ),
  ).toThrow(/Unsupported/);
  expect(() =>
    validateCapabilities(
      capabilities({
        execution: { hosted_fallback: false, local: false, ocr: false },
      }),
    ),
  ).toThrow(/Unsupported/);
  expect(() =>
    validateCapabilities(
      capabilities({
        execution: { hosted_fallback: false, local: true, ocr: true },
      }),
    ),
  ).toThrow(/Unsupported/);
  expect(() =>
    validateCapabilities(capabilities({ format_families: [] })),
  ).toThrow(/Unsupported/);
  expect(() =>
    validateCapabilities(
      capabilities({
        format_families: [
          {
            content_detection: "bad",
            detected_formats: [],
            extensions: ["DOCX"],
            family: "word",
            selected_parser_format: null,
          },
        ],
      }),
    ),
  ).toThrow(/Unsupported/);
  expect(validateSource(undefined, [".docx"], 10)).toMatch(/Choose/);
  expect(validateSource(new File(["x"], "note.txt"), [".docx"], 10)).toMatch(
    /listed/,
  );
  expect(validateSource(new File([], "empty.docx"), [".docx"], 10)).toMatch(
    /empty/,
  );
  expect(
    validateSource(new File(["too long"], "large.docx"), [".docx"], 2),
  ).toMatch(/upload limit/);
  expect(
    validateSource(new File(["ok"], "REPORT.DOCX"), [".docx"], 10),
  ).toBeUndefined();
});

test("status, cancellation, and polling presentation cover the lifecycle", () => {
  expect(nextPollDelay(1_000)).toBe(1_600);
  expect(nextPollDelay(9_000)).toBe(10_000);
  expect(statusPresentation(job())).toMatch(/queued/);
  expect(
    statusPresentation(job({ state: "running", step: "isolating" })),
  ).toMatch(/isolated/);
  expect(
    statusPresentation(job({ state: "running", step: "converting" })),
  ).toMatch(/Converting/);
  expect(
    statusPresentation(job({ state: "running", step: "validating" })),
  ).toMatch(/Validating/);
  expect(
    statusPresentation(job({ state: "running", step: "publishing" })),
  ).toMatch(/Publishing/);
  expect(
    statusPresentation(job({ state: "running", step: "complete" })),
  ).toMatch(/Processing/);
  expect(
    statusPresentation(job({ state: "running", cancel_requested: true })),
  ).toMatch(/Cancellation requested/);
  expect(statusPresentation(job({ state: "cancelled" }))).toMatch(/cancelled/);
  expect(statusPresentation(job({ state: "expired" }))).toMatch(/expired/);
  expect(statusPresentation(job({ state: "succeeded" }))).toMatch(/ready/);
  expect(
    statusPresentation(job({ state: "failed", error_code: "needs_ocr" })),
  ).toMatch(/OCR/);
  expect(
    statusPresentation(job({ state: "failed", error_message: "Safe failure" })),
  ).toBe("Safe failure");
  expect(statusPresentation(job({ state: "failed" }))).toMatch(/failed/);
  expect(isCancellable(job())).toBe(true);
  expect(isCancellable(job({ cancel_requested: true }))).toBe(false);
  expect(isCancellable(job({ state: "succeeded" }))).toBe(false);
});

test("load accepts schema v1 and fails closed for unavailable or unsupported capabilities", async () => {
  const json = vi
    .fn()
    .mockResolvedValueOnce(capabilities())
    .mockResolvedValueOnce({ items: [job()], limit: 10, offset: 0, total: 1 })
    .mockResolvedValueOnce(capabilities({ schema_version: 2 }))
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
    .mockRejectedValueOnce(new TypeError("private backend"))
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 });
  const controller = new ReversionController(api({ json }));
  await controller.load();
  expect(controller.snapshot()).toMatchObject({
    phase: "ready",
    extensions: [".docx", ".docm", ".pdf"],
    recent: [{ source_stem: "report" }],
  });
  await controller.load();
  expect(controller.snapshot().phase).toBe("unavailable");
  await controller.load();
  expect(controller.snapshot().phase).toBe("unavailable");
  controller.dispose();
});

test("submission validates locally and preserves an idempotency key after ambiguity", async () => {
  const multipartWithMetadata = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("network detail"))
    .mockResolvedValueOnce({
      data: job({ state: "succeeded", result_mode: "markdown" }),
      location: `/api/v1/reversions/${job().id}`,
      retryAfterSeconds: 1,
      status: 202,
    });
  const transport = api({
    json: vi
      .fn()
      .mockResolvedValueOnce(capabilities())
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
    multipartWithMetadata,
  });
  const controller = await loadedController(transport);
  await controller.submit();
  expect(controller.snapshot().error).toMatch(/Choose/);
  controller.setSource([new File(["document"], "report.docx")]);
  await controller.submit();
  expect(controller.snapshot().error).toMatch(/outcome is unknown/);
  await controller.submit();
  const firstOptions = multipartWithMetadata.mock.calls[0]![3];
  const secondOptions = multipartWithMetadata.mock.calls[1]![3];
  expect(firstOptions.idempotencyKey).toBe(secondOptions.idempotencyKey);
  expect(
    (multipartWithMetadata.mock.calls[0]![1] as FormData).get("source"),
  ).toBeInstanceOf(File);
  expect(controller.snapshot().active?.state).toBe("succeeded");
  controller.dispose();
});

test("definitive rejection rotates the idempotency key and malformed acceptance stays ambiguous", async () => {
  const multipartWithMetadata = vi
    .fn()
    .mockRejectedValueOnce(new ApiError(422, "INVALID", "Invalid document."))
    .mockResolvedValueOnce({ data: job(), location: "/wrong", status: 202 });
  const controller = await loadedController(
    api({
      json: vi
        .fn()
        .mockResolvedValueOnce(capabilities())
        .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
      multipartWithMetadata,
    }),
  );
  controller.setSource([new File(["document"], "report.docx")]);
  await controller.submit();
  expect(controller.snapshot().error).toBe("Invalid document.");
  await controller.submit();
  expect(controller.snapshot().error).toMatch(/outcome is unknown/);
  expect(multipartWithMetadata.mock.calls[0]![3].idempotencyKey).not.toBe(
    multipartWithMetadata.mock.calls[1]![3].idempotencyKey,
  );
  controller.dispose();
});

test("accepted running work schedules once and duplicate submission is ignored", async () => {
  let resolveSubmission!: (value: {
    data: ReversionResponse;
    location: string;
    retryAfterSeconds: number;
    status: number;
  }) => void;
  const multipartWithMetadata = vi.fn(
    () =>
      new Promise<Parameters<typeof resolveSubmission>[0]>((resolve) => {
        resolveSubmission = resolve;
      }),
  );
  const scheduled: Array<{ callback: () => void; delay: number }> = [];
  const controller = new ReversionController(
    api({
      json: vi
        .fn()
        .mockResolvedValueOnce(capabilities())
        .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
      multipartWithMetadata,
    }),
    undefined,
    undefined,
    (callback, delay) => {
      scheduled.push({ callback, delay });
      return 1 as unknown as ReturnType<typeof setTimeout>;
    },
  );
  await controller.load();
  controller.setSource([new File(["document"], "report.docx")]);
  const submission = controller.submit();
  await controller.submit();
  expect(multipartWithMetadata).toHaveBeenCalledOnce();
  resolveSubmission({
    data: job(),
    location: `/api/v1/reversions/${job().id}`,
    retryAfterSeconds: 3,
    status: 202,
  });
  await submission;
  expect(scheduled).toHaveLength(1);
  expect(scheduled[0]!.delay).toBe(3_000);
  controller.dispose();
});

test("polling backs off, updates jobs, retries temporary errors, and fences cancellation", async () => {
  const scheduled: Array<{ callback: () => void; delay: number }> = [];
  const json = vi
    .fn()
    .mockResolvedValueOnce(capabilities())
    .mockResolvedValueOnce({ items: [job()], limit: 10, offset: 0, total: 1 })
    .mockResolvedValueOnce(job({ state: "running", step: "converting" }))
    .mockRejectedValueOnce(new TypeError("temporary"));
  const cancel = vi.fn().mockResolvedValue(job({ cancel_requested: true }));
  const controller = new ReversionController(
    api({ json, cancel }),
    undefined,
    undefined,
    (callback, delay) => {
      scheduled.push({ callback, delay });
      return 1 as unknown as ReturnType<typeof setTimeout>;
    },
    vi.fn(),
  );
  await controller.load();
  await controller.openJob(job().id);
  expect(controller.snapshot().active?.step).toBe("converting");
  expect(scheduled.at(-1)?.delay).toBe(1_600);
  scheduled.at(-1)!.callback();
  await vi.waitFor(() =>
    expect(controller.snapshot().error).toMatch(/continue/),
  );
  expect(scheduled.at(-1)?.delay).toBe(2_560);
  await controller.cancel();
  expect(controller.snapshot().active?.cancel_requested).toBe(true);
  controller.dispose();
});

test("terminal and missing jobs stop polling while cancellation failures resume it", async () => {
  const scheduled: Array<{ callback: () => void; delay: number }> = [];
  const cancel = vi.fn().mockRejectedValue(new TypeError("private"));
  const json = vi
    .fn()
    .mockResolvedValueOnce(capabilities())
    .mockResolvedValueOnce({ items: [job()], limit: 10, offset: 0, total: 1 })
    .mockRejectedValueOnce(new ApiError(404, "NOT_FOUND", "Not found."));
  const controller = new ReversionController(
    api({ cancel, json }),
    undefined,
    undefined,
    (callback, delay) => {
      scheduled.push({ callback, delay });
      return 1 as unknown as ReturnType<typeof setTimeout>;
    },
  );
  await controller.load();
  await controller.openJob(job().id);
  expect(controller.snapshot().error).toMatch(/Polling has stopped/);
  expect(scheduled).toHaveLength(0);
  await controller.cancel();
  expect(controller.snapshot().error).toMatch(/Cancellation could not/);
  expect(scheduled).toHaveLength(1);
  controller.dispose();
});

test("persistent polling failures stop after a bounded number of retries", async () => {
  const scheduled: Array<{ callback: () => void; delay: number }> = [];
  const json = vi
    .fn()
    .mockResolvedValueOnce(capabilities())
    .mockResolvedValueOnce({ items: [job()], limit: 10, offset: 0, total: 1 })
    .mockRejectedValue(new TypeError("temporary"));
  const controller = new ReversionController(
    api({ json }),
    undefined,
    undefined,
    (callback, delay) => {
      scheduled.push({ callback, delay });
      return scheduled.length as unknown as ReturnType<typeof setTimeout>;
    },
    vi.fn(),
  );
  await controller.load();
  await controller.openJob(job().id);
  expect(scheduled).toHaveLength(1);
  for (let failure = 2; failure <= 6; failure += 1) {
    scheduled.at(-1)!.callback();
    await vi.waitFor(() => expect(json).toHaveBeenCalledTimes(2 + failure));
  }
  expect(scheduled).toHaveLength(5);
  expect(controller.snapshot().error).toMatch(/Polling has stopped/);
  controller.dispose();
});

test("401 expires the session and download validates private Markdown and ZIP responses", async () => {
  const expire = vi.fn();
  const download = vi
    .fn()
    .mockResolvedValueOnce(
      new Response("# result", {
        headers: {
          "Cache-Control": "private, no-store",
          "Content-Disposition": 'attachment; filename="report.md"',
          "Content-Type": "text/markdown; charset=utf-8",
          "X-Content-Type-Options": "nosniff",
        },
      }),
    )
    .mockResolvedValueOnce(
      new Response("zip", {
        headers: {
          "Cache-Control": "private, no-store",
          "Content-Disposition":
            "attachment; filename*=UTF-8''report%20assets.zip",
          "Content-Type": "application/zip",
          "X-Content-Type-Options": "nosniff",
        },
      }),
    )
    .mockResolvedValueOnce(new Response("unsafe"));
  const json = vi
    .fn()
    .mockResolvedValueOnce(capabilities())
    .mockResolvedValueOnce({
      items: [job({ state: "succeeded", result_mode: "markdown" })],
      limit: 10,
      offset: 0,
      total: 1,
    })
    .mockResolvedValue(job({ state: "succeeded", result_mode: "markdown" }));
  const controller = new ReversionController(api({ json, download }), expire);
  await controller.load();
  await controller.openJob(job().id);
  expect((await controller.download())?.filename).toBe("report.md");
  expect((await controller.download())?.filename).toBe("report assets.zip");
  expect(await controller.download()).toBeUndefined();
  expect(controller.snapshot().error).toMatch(/unexpected response/);
  controller.dispose();

  const expired = new ReversionController(
    api({
      json: vi
        .fn()
        .mockRejectedValueOnce(
          new ApiError(401, "AUTHENTICATION_REQUIRED", "ignored"),
        )
        .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
    }),
    expire,
  );
  await expired.load();
  expect(expire).toHaveBeenCalledOnce();
  expired.dispose();
});

test("download rejects malformed filenames and does nothing without a successful job", async () => {
  const invalidResponses = [
    new Response("x", {
      headers: {
        "Cache-Control": "private, no-store",
        "Content-Disposition": "attachment; filename*=UTF-8''%E0%A4%A",
        "Content-Type": "application/zip",
        "X-Content-Type-Options": "nosniff",
      },
    }),
    new Response("x", {
      headers: {
        "Cache-Control": "private, no-store",
        "Content-Disposition": 'attachment; filename="../report.md"',
        "Content-Type": "text/markdown",
        "X-Content-Type-Options": "nosniff",
      },
    }),
    new Response("x", {
      headers: {
        "Cache-Control": "private, no-store",
        "Content-Disposition": 'attachment; filename="report.exe"',
        "Content-Type": "text/markdown",
        "X-Content-Type-Options": "nosniff",
      },
    }),
  ];
  const controller = new ReversionController(
    api({
      download: vi.fn().mockImplementation(() => invalidResponses.shift()),
      json: vi
        .fn()
        .mockResolvedValueOnce(capabilities())
        .mockResolvedValueOnce({
          items: [job({ state: "succeeded" })],
          limit: 10,
          offset: 0,
          total: 1,
        })
        .mockResolvedValue(job({ state: "succeeded" })),
    }),
  );
  await controller.load();
  expect(await controller.download()).toBeUndefined();
  await controller.openJob(job().id);
  for (let index = 0; index < 3; index += 1)
    expect(await controller.download()).toBeUndefined();
  expect(controller.snapshot().error).toMatch(/unexpected response/);
  controller.dispose();
});

test("source cardinality and detached subscribers are deterministic", async () => {
  const controller = await loadedController(
    api({
      json: vi
        .fn()
        .mockResolvedValueOnce(capabilities())
        .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
    }),
  );
  const listener = vi.fn();
  const detach = controller.subscribe(listener);
  controller.setSource([
    new File(["one"], "one.docx"),
    new File(["two"], "two.pdf"),
  ]);
  expect(controller.snapshot().error).toMatch(/exactly one/);
  controller.setSource(null);
  detach();
  controller.setSource([new File(["one"], "one.docx")]);
  expect(listener).toHaveBeenCalledTimes(2);
  controller.dispose();
});
