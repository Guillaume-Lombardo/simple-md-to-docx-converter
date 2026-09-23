import type {
  ReversionCapabilitiesResponse,
  ReversionResponse,
} from "../src/api/generated/types.gen";
import { ApiError, type ApiTransport } from "../src/api/transport";
import { ReversionController } from "../src/reversion/controller";

const firstId = "00000000-0000-4000-8000-000000000201";
const secondId = "00000000-0000-4000-8000-000000000202";
const draftId = "00000000-0000-4000-8000-000000000301";

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
      modes: ["anydoc", "slides", "marp"],
      structured_extensions: [".pptx"],
    },
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
        detected_formats: ["pptx"],
        extensions: [".pptx"],
        family: "powerpoint",
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

function job(overrides: Partial<ReversionResponse> = {}): ReversionResponse {
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
    id: firstId,
    owner_id: "00000000-0000-4000-8000-000000000001",
    options: {
      extraction: "anydoc",
      include_images: true,
      include_notes: true,
    },
    result_mode: "markdown",
    result_size: 8,
    source_extension: ".docx",
    source_family: "word",
    source_stem: "report",
    state: "succeeded",
    step: "publishing",
    updated_at: "2026-09-06T08:00:00Z",
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

function api(
  jobs: ReversionResponse[] = [],
  overrides: Partial<ApiTransport> = {},
) {
  return {
    json: vi.fn((path: string) => {
      if (path === "/api/v1/reversions/capabilities")
        return Promise.resolve(capabilities());
      if (path === "/api/v1/reversions?offset=0&limit=10")
        return Promise.resolve({
          items: jobs,
          limit: 10,
          offset: 0,
          total: jobs.length,
        });
      const found = jobs.find(
        (candidate) => path === `/api/v1/reversions/${candidate.id}`,
      );
      if (found) return Promise.resolve(found);
      throw new Error(`Unexpected request: ${path}`);
    }),
    multipartWithMetadata: vi.fn(),
    cancel: vi.fn(),
    download: vi.fn(),
    ...overrides,
  } as unknown as ApiTransport;
}

test("a superseded capabilities load cannot replace current options or expire the session", async () => {
  const firstLoad = deferred<ReversionCapabilitiesResponse>();
  let loadCount = 0;
  const json = vi.fn((path: string) => {
    if (path === "/api/v1/reversions/capabilities")
      return ++loadCount === 1
        ? firstLoad.promise
        : Promise.resolve(capabilities());
    return Promise.resolve({ items: [], limit: 10, offset: 0, total: 0 });
  });
  const expire = vi.fn();
  const controller = new ReversionController(api([], { json }), expire);
  const obsolete = controller.load();
  await controller.load();
  const selected = new File(["document"], "current.docx");
  controller.setSource([selected]);
  firstLoad.resolve(capabilities());
  await obsolete;
  expect(controller.snapshot()).toMatchObject({
    phase: "ready",
    source: selected,
    options: { extraction: "anydoc" },
  });
  expect(expire).not.toHaveBeenCalled();
  controller.dispose();
});

test("Composer upload rejects a supported macro file locally and preserves a retryable Office source after 403", async () => {
  const multipartWithMetadata = vi
    .fn()
    .mockRejectedValueOnce(new ApiError(403, "FORBIDDEN", "Access denied."))
    .mockResolvedValueOnce({
      data: { id: draftId },
      location: `/api/v1/composer/drafts/${draftId}`,
      status: 201,
    });
  const expire = vi.fn();
  const transport = api([], { multipartWithMetadata });
  const controller = new ReversionController(transport, expire);
  await controller.load();
  expect(await controller.createComposerDraft()).toBeUndefined();
  expect(controller.snapshot().error).toMatch(/Choose a supported document/);
  controller.setSource([new File(["macro"], "report.docm")]);
  expect(await controller.createComposerDraft()).toBeUndefined();
  expect(controller.snapshot().error).toMatch(/DOCX, PPTX, or PDF/);
  expect(multipartWithMetadata).not.toHaveBeenCalled();

  const office = new File(["office"], "report.docx");
  controller.setSource([office]);
  expect(await controller.createComposerDraft()).toBeUndefined();
  expect(controller.snapshot()).toMatchObject({
    source: office,
    composerPending: false,
    error: "Access denied.",
  });
  expect(expire).not.toHaveBeenCalled();
  expect(await controller.createComposerDraft()).toBe(draftId);
  expect(controller.snapshot().error).toBeUndefined();
  expect(multipartWithMetadata).toHaveBeenCalledTimes(2);
  controller.dispose();
});

test("invalid extraction changes leave the selected document's valid settings intact", async () => {
  const transport = api();
  const controller = new ReversionController(transport);
  await controller.load();
  controller.setSource([new File(["word"], "report.docx")]);
  const beforeInvalid = controller.inputVersion();
  controller.setExtraction("marp");
  controller.setIncludeImages(false);
  controller.setIncludeNotes(false);
  expect(controller.snapshot().options).toEqual({
    extraction: "anydoc",
    include_images: true,
    include_notes: true,
  });
  expect(controller.inputVersion()).toBe(beforeInvalid);

  controller.setSource([new File(["slides"], "slides.pptx")]);
  controller.setExtraction("marp");
  controller.setIncludeNotes(false);
  expect(controller.snapshot().options).toEqual({
    extraction: "marp",
    include_images: true,
    include_notes: false,
  });
  controller.dispose();
});

test("a pending Composer handoff cannot double-submit, and 401 expires only the active request", async () => {
  const pending = deferred<{
    data: { id: string };
    location: string;
    status: number;
  }>();
  const jsonWithMetadata = vi.fn().mockReturnValue(pending.promise);
  const expire = vi.fn();
  const active = job();
  const transport = api([active], { jsonWithMetadata });
  const controller = new ReversionController(transport, expire);
  await controller.load();
  await controller.openJob(active.id);
  const first = controller.handoffActiveResult();
  expect(controller.snapshot().composerPending).toBe(true);
  expect(await controller.handoffActiveResult()).toBeUndefined();
  expect(jsonWithMetadata).toHaveBeenCalledTimes(1);
  pending.reject(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "Sign in again."),
  );
  expect(await first).toBeUndefined();
  expect(expire).toHaveBeenCalledOnce();
  controller.dispose();
});

test("an obsolete handoff 401 cannot expire a new selection, and malformed receipts remain retryable", async () => {
  const first = job();
  const second = job({ id: secondId });
  const obsolete = deferred<{
    data: { id: string };
    location: string;
    status: number;
  }>();
  const jsonWithMetadata = vi
    .fn()
    .mockReturnValueOnce(obsolete.promise)
    .mockResolvedValueOnce({
      data: { id: draftId },
      location: `/api/v1/composer/drafts/${draftId}`,
      status: 202,
    })
    .mockResolvedValueOnce({
      data: { id: draftId },
      location: `/api/v1/composer/drafts/${draftId}`,
      status: 201,
    });
  const expire = vi.fn();
  const transport = api([first, second], { jsonWithMetadata });
  const controller = new ReversionController(transport, expire);
  await controller.load();
  await controller.openJob(first.id);
  const oldHandoff = controller.handoffActiveResult();
  await controller.openJob(second.id);
  obsolete.reject(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "Old job expired."),
  );
  expect(await oldHandoff).toBeUndefined();
  expect(expire).not.toHaveBeenCalled();
  expect(controller.snapshot().active?.id).toBe(second.id);

  expect(await controller.handoffActiveResult()).toBeUndefined();
  expect(controller.snapshot()).toMatchObject({
    active: { id: second.id },
    composerPending: false,
    error: "The service returned an unexpected response.",
  });
  expect(await controller.handoffActiveResult()).toBe(draftId);
  expect(controller.snapshot().error).toBeUndefined();
  expect(jsonWithMetadata).toHaveBeenCalledTimes(3);
  controller.dispose();
});

test("a stale cancellation denial cannot alter a newly selected job or expire its session", async () => {
  const first = job({ state: "running" });
  const second = job({ id: secondId });
  const pending = deferred<ReversionResponse>();
  const cancel = vi.fn().mockReturnValue(pending.promise);
  const schedule = vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>);
  const expire = vi.fn();
  const transport = api([first, second], { cancel });
  const controller = new ReversionController(
    transport,
    expire,
    () => "key",
    schedule,
  );
  await controller.load();
  await controller.openJob(first.id);
  const cancellation = controller.cancel();
  expect(controller.snapshot().cancelling).toBe(true);
  await controller.openJob(second.id);
  const scheduledBefore = schedule.mock.calls.length;
  pending.reject(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "Old job expired."),
  );
  await cancellation;
  expect(controller.snapshot()).toMatchObject({
    active: { id: second.id, state: "succeeded" },
    cancelling: false,
    error: undefined,
  });
  expect(expire).not.toHaveBeenCalled();
  expect(schedule).toHaveBeenCalledTimes(scheduledBefore);
  controller.dispose();
});

test("a current polling 401 expires the session without another retry timer", async () => {
  const running = job({ state: "running" });
  const json = vi.fn((path: string) => {
    if (path === "/api/v1/reversions/capabilities")
      return Promise.resolve(capabilities());
    if (path === "/api/v1/reversions?offset=0&limit=10")
      return Promise.resolve({
        items: [running],
        limit: 10,
        offset: 0,
        total: 1,
      });
    return Promise.reject(
      new ApiError(401, "AUTHENTICATION_REQUIRED", "Sign in again."),
    );
  });
  const schedule = vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>);
  const expire = vi.fn();
  const controller = new ReversionController(
    api([running], { json }),
    expire,
    () => "key",
    schedule,
  );
  await controller.load();
  await controller.openJob(running.id);
  expect(expire).toHaveBeenCalledOnce();
  expect(schedule).not.toHaveBeenCalled();
  controller.dispose();
});

test("a delayed old download never returns bytes for the new selection, while current 403 and 401 differ", async () => {
  const first = job();
  const second = job({ id: secondId });
  let release!: () => void;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      release = () => {
        controller.enqueue(new TextEncoder().encode("# old private result"));
        controller.close();
      };
    },
  });
  const download = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(body, {
        headers: {
          "Cache-Control": "private, no-store",
          "Content-Disposition": 'attachment; filename="old.md"',
          "Content-Type": "text/markdown",
          "X-Content-Type-Options": "nosniff",
        },
      }),
    )
    .mockRejectedValueOnce(new ApiError(403, "FORBIDDEN", "Access denied."))
    .mockRejectedValueOnce(
      new ApiError(401, "AUTHENTICATION_REQUIRED", "Sign in again."),
    );
  const expire = vi.fn();
  const transport = api([first, second], { download });
  const controller = new ReversionController(transport, expire);
  await controller.load();
  await controller.openJob(first.id);
  const stale = controller.download();
  await vi.waitFor(() => expect(download).toHaveBeenCalledTimes(1));
  await controller.openJob(second.id);
  release();
  expect(await stale).toBeUndefined();
  expect(controller.snapshot().error).toBeUndefined();
  expect(await controller.download()).toBeUndefined();
  expect(controller.snapshot().error).toBe("Access denied.");
  expect(expire).not.toHaveBeenCalled();
  expect(await controller.download()).toBeUndefined();
  expect(expire).toHaveBeenCalledOnce();
  controller.dispose();
});
