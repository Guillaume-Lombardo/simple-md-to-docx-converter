import type {
  ReversionCapabilitiesResponse,
  ReversionResponse,
} from "../src/api/generated/types.gen";
import { ApiError, type ApiTransport } from "../src/api/transport";
import { ReversionController } from "../src/reversion/controller";

const firstId = "00000000-0000-4000-8000-000000000201";
const secondId = "00000000-0000-4000-8000-000000000202";

function capabilities(): ReversionCapabilitiesResponse {
  return {
    admission: {
      csv_policy: "bounded text",
      extension_is_hint: true,
      mismatch_policy: "reject",
      scanner_order: "scan first",
      undetected_policy: "reject",
    },
    execution: { hosted_fallback: false, local: true, ocr: false },
    extraction: {
      default_mode: "anydoc",
      include_images_default: true,
      include_notes_default: true,
      modes: ["anydoc", "slides"],
      structured_extensions: [".pptx"],
    },
    format_families: [
      {
        content_detection: "signature",
        detected_formats: ["docx", "pptx"],
        extensions: [".docx", ".pptx"],
        family: "word",
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
  };
}

function job(
  id = firstId,
  state: ReversionResponse["state"] = "queued",
): ReversionResponse {
  return {
    attempt: 0,
    cancel_requested: false,
    component_versions: [],
    correlation_id: "correlation",
    created_at: "2026-09-06T08:00:00Z",
    detected_format: "docx",
    error_code: null,
    error_message: null,
    expires_at: null,
    id,
    owner_id: "00000000-0000-4000-8000-000000000001",
    options: {
      extraction: "anydoc",
      include_images: true,
      include_notes: true,
    },
    result_mode: state === "succeeded" ? "markdown" : null,
    result_size: null,
    source_extension: ".docx",
    source_family: "word",
    source_stem: "report",
    state,
    step: state === "succeeded" ? "complete" : "queued",
    updated_at: "2026-09-06T08:00:00Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function controllerWith(transport: Partial<ApiTransport>, expire = vi.fn()) {
  const scheduled: Array<{ callback: () => void; delay: number }> = [];
  const controller = new ReversionController(
    transport as ApiTransport,
    expire,
    () => "request-key",
    (callback, delay) => {
      scheduled.push({ callback, delay });
      return scheduled.length as unknown as ReturnType<typeof setTimeout>;
    },
    vi.fn(),
  );
  return { controller, expire, scheduled };
}

function loadJson(
  recent: ReversionResponse[] = [],
  getJob: (id: string) => ReversionResponse = (id) =>
    recent.find((item) => item.id === id) ?? job(id),
) {
  return vi.fn(async (path: string) => {
    if (path === "/api/v1/reversions/capabilities") return capabilities();
    if (path.startsWith("/api/v1/reversions?"))
      return { items: recent, limit: 10, offset: 0, total: recent.length };
    return getJob(path.split("/").at(-1) ?? "");
  });
}

test("a newer successful load ignores a late unauthorized response from its predecessor", async () => {
  const firstCapabilities = deferred<ReversionCapabilitiesResponse>();
  let capabilityReads = 0;
  let firstSignal: AbortSignal | undefined;
  const json = vi.fn(
    (path: string, _schema: unknown, request: { signal?: AbortSignal }) => {
      if (path === "/api/v1/reversions/capabilities") {
        capabilityReads += 1;
        if (capabilityReads === 1) {
          firstSignal = request.signal;
          return firstCapabilities.promise;
        }
        return Promise.resolve(capabilities());
      }
      return Promise.resolve({
        items: [job(secondId)],
        limit: 10,
        offset: 0,
        total: 1,
      });
    },
  );
  const { controller, expire } = controllerWith({ json });
  const stale = controller.load();
  await controller.load();
  expect(firstSignal?.aborted).toBe(true);
  firstCapabilities.reject(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "Old session expired."),
  );
  await stale;
  expect(controller.snapshot()).toMatchObject({
    phase: "ready",
    recent: [{ id: secondId }],
  });
  expect(expire).not.toHaveBeenCalled();
  controller.dispose();
});

test("Composer source handoff retains a selected file across a 403 and succeeds on retry", async () => {
  const upload = vi
    .fn()
    .mockRejectedValueOnce(
      new ApiError(403, "FORBIDDEN", "Composer access denied."),
    )
    .mockResolvedValueOnce({
      data: { id: "00000000-0000-4000-8000-000000000301" },
      location: "/api/v1/composer/drafts/00000000-0000-4000-8000-000000000301",
      status: 201,
    });
  const { controller, expire } = controllerWith({
    json: loadJson(),
    multipartWithMetadata: upload,
  });
  await controller.load();
  const source = new File(["office bytes"], "review.docx");
  controller.setSource([source]);
  expect(await controller.createComposerDraft()).toBeUndefined();
  expect(controller.snapshot()).toMatchObject({
    source,
    composerPending: false,
    error: "Composer access denied.",
  });
  expect(expire).not.toHaveBeenCalled();
  expect(await controller.createComposerDraft()).toBe(
    "00000000-0000-4000-8000-000000000301",
  );
  expect(upload).toHaveBeenCalledTimes(2);
  expect((upload.mock.calls[1]?.[1] as FormData).get("source")).toBe(source);
  expect(controller.snapshot().composerPending).toBe(false);
  controller.dispose();
});

test("a Revert-supported macro document remains selected but is not uploaded to Composer", async () => {
  const supported = capabilities();
  supported.format_families[0]!.extensions.push(".docm");
  const json = vi.fn(async (path: string) =>
    path === "/api/v1/reversions/capabilities"
      ? supported
      : { items: [], limit: 10, offset: 0, total: 0 },
  );
  const upload = vi.fn();
  const { controller } = controllerWith({
    json,
    multipartWithMetadata: upload,
  });
  await controller.load();
  const source = new File(["office bytes"], "macros.docm");
  controller.setSource([source]);
  expect(await controller.createComposerDraft()).toBeUndefined();
  expect(controller.snapshot()).toMatchObject({
    source,
    error: expect.stringMatching(
      /Composer accepts selected DOCX, PPTX, or PDF/,
    ),
  });
  expect(upload).not.toHaveBeenCalled();
  controller.dispose();
});

test("a Composer upload 401 expires the session without leaking its private detail", async () => {
  const upload = vi
    .fn()
    .mockRejectedValue(
      new ApiError(401, "AUTHENTICATION_REQUIRED", "Private upload detail."),
    );
  const { controller, expire } = controllerWith({
    json: loadJson(),
    multipartWithMetadata: upload,
  });
  await controller.load();
  controller.setSource([new File(["office bytes"], "review.docx")]);
  expect(await controller.createComposerDraft()).toBeUndefined();
  expect(expire).toHaveBeenCalledOnce();
  expect(controller.snapshot().error).toBeUndefined();
  controller.dispose();
});

test("an invalid Composer handoff receipt leaves the exact successful result available for retry", async () => {
  const finished = job(firstId, "succeeded");
  const handoff = vi
    .fn()
    .mockResolvedValueOnce({
      data: { id: "00000000-0000-4000-8000-000000000301" },
      location: "/api/v1/composer/drafts/wrong",
      status: 201,
    })
    .mockResolvedValueOnce({
      data: { id: "00000000-0000-4000-8000-000000000301" },
      location: "/api/v1/composer/drafts/00000000-0000-4000-8000-000000000301",
      status: 201,
    });
  const { controller } = controllerWith({
    json: loadJson([finished]),
    jsonWithMetadata: handoff,
  });
  await controller.load();
  await controller.openJob(firstId);
  expect(await controller.handoffActiveResult()).toBeUndefined();
  expect(controller.snapshot()).toMatchObject({
    active: { id: firstId, state: "succeeded" },
    composerPending: false,
    error: "The service returned an unexpected response.",
  });
  expect(await controller.handoffActiveResult()).toBe(
    "00000000-0000-4000-8000-000000000301",
  );
  expect(handoff).toHaveBeenCalledTimes(2);
  controller.dispose();
});

test("a Composer handoff cannot return an old job's draft after another job is opened", async () => {
  const first = job(firstId, "succeeded");
  const second = job(secondId, "succeeded");
  const pending = deferred<{
    data: { id: string };
    location: string;
    status: number;
  }>();
  let handoffSignal: AbortSignal | undefined;
  const jsonWithMetadata = vi.fn(
    (_path: string, _schema: unknown, request: { signal?: AbortSignal }) => {
      handoffSignal = request.signal;
      return pending.promise;
    },
  );
  const { controller } = controllerWith({
    json: loadJson([first, second]),
    jsonWithMetadata: jsonWithMetadata as ApiTransport["jsonWithMetadata"],
  });
  await controller.load();
  await controller.openJob(firstId);
  const stale = controller.handoffActiveResult();
  expect(controller.snapshot().composerPending).toBe(true);
  expect(await controller.handoffActiveResult()).toBeUndefined();
  expect(jsonWithMetadata).toHaveBeenCalledOnce();
  await controller.openJob(secondId);
  expect(handoffSignal?.aborted).toBe(true);
  pending.resolve({
    data: { id: "00000000-0000-4000-8000-000000000301" },
    location: "/api/v1/composer/drafts/00000000-0000-4000-8000-000000000301",
    status: 201,
  });
  expect(await stale).toBeUndefined();
  expect(controller.snapshot()).toMatchObject({
    active: { id: secondId },
    composerPending: false,
  });
  controller.dispose();
});

test("new source selection fences a late accepted reversion submission", async () => {
  const pending = deferred<{
    data: ReversionResponse;
    location: string;
    retryAfterSeconds: number;
    status: number;
  }>();
  let submissionSignal: AbortSignal | undefined;
  const upload = vi.fn(
    (
      _path: string,
      _form: FormData,
      _schema: unknown,
      request: { signal?: AbortSignal },
    ) => {
      submissionSignal = request.signal;
      return pending.promise;
    },
  );
  const { controller, scheduled } = controllerWith({
    json: loadJson(),
    multipartWithMetadata: upload as ApiTransport["multipartWithMetadata"],
  });
  await controller.load();
  controller.setSource([new File(["first"], "first.docx")]);
  const stale = controller.submit();
  controller.setSource([new File(["second"], "second.docx")]);
  expect(submissionSignal?.aborted).toBe(true);
  pending.resolve({
    data: job(firstId),
    location: `/api/v1/reversions/${firstId}`,
    retryAfterSeconds: 1,
    status: 202,
  });
  await stale;
  expect(controller.snapshot()).toMatchObject({
    source: { name: "second.docx" },
    submitting: false,
    recent: [],
  });
  expect(controller.snapshot().active).toBeUndefined();
  expect(scheduled).toHaveLength(0);
  controller.dispose();
});

test("queue-capacity rejection rotates the request key while an ambiguous outage reuses it", async () => {
  const upload = vi
    .fn()
    .mockRejectedValueOnce(
      new ApiError(503, "REVERSION_QUEUE_CAPACITY_EXCEEDED", "Queue is full."),
    )
    .mockRejectedValueOnce(new ApiError(503, "UNAVAILABLE", "Status unknown."))
    .mockResolvedValueOnce({
      data: job(firstId, "succeeded"),
      location: `/api/v1/reversions/${firstId}`,
      retryAfterSeconds: 1,
      status: 202,
    });
  let keyNumber = 0;
  const controller = new ReversionController(
    {
      json: loadJson(),
      multipartWithMetadata: upload,
    } as unknown as ApiTransport,
    vi.fn(),
    () => `request-${++keyNumber}`,
    () => 1 as unknown as ReturnType<typeof setTimeout>,
    vi.fn(),
  );
  await controller.load();
  controller.setSource([new File(["office bytes"], "report.docx")]);
  await controller.submit();
  expect(controller.snapshot().error).toBe("Queue is full.");
  await controller.submit();
  expect(controller.snapshot().error).toMatch(/outcome is unknown/);
  await controller.submit();
  const keys = upload.mock.calls.map(
    (call) => (call[3] as { idempotencyKey: string }).idempotencyKey,
  );
  expect(keys[0]).not.toBe(keys[1]);
  expect(keys[1]).toBe(keys[2]);
  expect(controller.snapshot().active?.state).toBe("succeeded");
  controller.dispose();
});

test("an obsolete cancellation 401 cannot expire a newer selected job", async () => {
  const first = job(firstId);
  const second = job(secondId, "succeeded");
  const pending = deferred<ReversionResponse>();
  let cancelSignal: AbortSignal | undefined;
  const cancel = vi.fn(
    (_path: string, _schema: unknown, signal: AbortSignal) => {
      cancelSignal = signal;
      return pending.promise;
    },
  );
  const { controller, expire } = controllerWith({
    json: loadJson([first, second]),
    cancel,
  });
  await controller.load();
  await controller.openJob(firstId);
  const stale = controller.cancel();
  expect(controller.snapshot().cancelling).toBe(true);
  await controller.openJob(secondId);
  expect(cancelSignal?.aborted).toBe(true);
  pending.reject(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "Obsolete cancellation."),
  );
  await stale;
  expect(controller.snapshot()).toMatchObject({
    phase: "ready",
    active: { id: secondId },
    cancelling: false,
  });
  expect(expire).not.toHaveBeenCalled();
  controller.dispose();
});

test("a forbidden status refresh preserves the authorized job and can recover on the next poll", async () => {
  const current = job(firstId);
  const baseJson = loadJson([current]);
  let polls = 0;
  const json = vi.fn((path: string) => {
    if (path === `/api/v1/reversions/${firstId}`) {
      polls += 1;
      return polls === 1
        ? Promise.reject(new ApiError(403, "FORBIDDEN", "Status denied."))
        : Promise.resolve(job(firstId, "succeeded"));
    }
    return baseJson(path);
  });
  const { controller, expire, scheduled } = controllerWith({ json });
  await controller.load();
  await controller.openJob(firstId);
  expect(controller.snapshot()).toMatchObject({
    phase: "ready",
    active: { id: firstId },
    error: expect.stringMatching(/Polling will continue/),
  });
  expect(expire).not.toHaveBeenCalled();
  expect(scheduled).toHaveLength(1);
  scheduled[0]!.callback();
  await vi.waitFor(() =>
    expect(controller.snapshot()).toMatchObject({
      active: { id: firstId, state: "succeeded" },
      error: undefined,
    }),
  );
  controller.dispose();
});

test("a late result download cannot return the previous job's artifact after selection changes", async () => {
  const first = job(firstId, "succeeded");
  const second = job(secondId, "succeeded");
  const pending = deferred<Response>();
  const download = vi.fn(() => pending.promise);
  const { controller } = controllerWith({
    json: loadJson([first, second]),
    download,
  });
  await controller.load();
  await controller.openJob(firstId);
  const stale = controller.download();
  expect(download).toHaveBeenCalledWith(`/api/v1/reversions/${firstId}/result`);
  await controller.openJob(secondId);
  pending.resolve(
    new Response("# old result", {
      headers: {
        "Cache-Control": "private, no-store",
        "Content-Disposition": 'attachment; filename="old.md"',
        "Content-Type": "text/markdown",
        "X-Content-Type-Options": "nosniff",
      },
    }),
  );
  expect(await stale).toBeUndefined();
  expect(controller.snapshot().active?.id).toBe(secondId);
  controller.dispose();
});

test("a late result-download 401 cannot expire a newer selected job", async () => {
  const first = job(firstId, "succeeded");
  const second = job(secondId, "succeeded");
  const pending = deferred<Response>();
  const { controller, expire } = controllerWith({
    json: loadJson([first, second]),
    download: vi.fn(() => pending.promise),
  });
  await controller.load();
  await controller.openJob(firstId);
  const stale = controller.download();
  await controller.openJob(secondId);
  pending.reject(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "Old download expired."),
  );
  expect(await stale).toBeUndefined();
  expect(expire).not.toHaveBeenCalled();
  expect(controller.snapshot().active?.id).toBe(secondId);
  controller.dispose();
});

test("A to B to A reselection still fences the first A download by generation", async () => {
  const first = job(firstId, "succeeded");
  const second = job(secondId, "succeeded");
  const pending = deferred<Response>();
  const download = vi.fn(() => pending.promise);
  const { controller } = controllerWith({
    json: loadJson([first, second]),
    download,
  });
  await controller.load();
  await controller.openJob(firstId);
  const stale = controller.download();
  await controller.openJob(secondId);
  await controller.openJob(firstId);
  pending.resolve(
    new Response("# superseded result", {
      headers: {
        "Cache-Control": "private, no-store",
        "Content-Disposition": 'attachment; filename="first.md"',
        "Content-Type": "text/markdown",
        "X-Content-Type-Options": "nosniff",
      },
    }),
  );
  expect(await stale).toBeUndefined();
  expect(controller.snapshot().active?.id).toBe(firstId);
  controller.dispose();
});

test("a 401 from the currently selected result download expires the session", async () => {
  const finished = job(firstId, "succeeded");
  const download = vi
    .fn()
    .mockRejectedValue(
      new ApiError(401, "AUTHENTICATION_REQUIRED", "Private download detail."),
    );
  const { controller, expire } = controllerWith({
    json: loadJson([finished]),
    download,
  });
  await controller.load();
  await controller.openJob(firstId);
  expect(await controller.download()).toBeUndefined();
  expect(expire).toHaveBeenCalledOnce();
  expect(controller.snapshot().error).toBeUndefined();
  controller.dispose();
});
