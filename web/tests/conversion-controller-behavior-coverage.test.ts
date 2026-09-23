import type {
  ConversionOptionsResponse,
  ConversionResponse,
} from "../src/api/generated/types.gen";
import { ApiError, type ApiTransport } from "../src/api/transport";
import { ConversionController } from "../src/conversion/controller";

const firstId = "00000000-0000-4000-8000-000000000201";
const secondId = "00000000-0000-4000-8000-000000000202";
const draftId = "00000000-0000-4000-8000-000000000301";

function job(overrides: Partial<ConversionResponse> = {}): ConversionResponse {
  return {
    attempt: 0,
    cancel_requested: false,
    component_versions: [],
    correlation_id: "correlation",
    created_at: "2026-09-02T08:00:00Z",
    error_code: null,
    error_message: null,
    expires_at: null,
    id: firstId,
    output: "docx",
    owner_id: "00000000-0000-4000-8000-000000000001",
    progress: 100,
    state: "succeeded",
    step: "publishing",
    template_id: null,
    template_mode: "pandoc-default",
    template_version_id: null,
    updated_at: "2026-09-02T08:00:00Z",
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

function transport(
  jobs: ConversionResponse[] = [],
  overrides: Partial<ApiTransport> = {},
): ApiTransport {
  const options: ConversionOptionsResponse = {
    conversion_upload_max_bytes: 1_000_000,
    resolved_template: null,
    selection_source: "pandoc_default",
    template_version_id: null,
  };
  return {
    json: vi.fn((path: string) => {
      if (path.startsWith("/api/v1/conversion-options"))
        return Promise.resolve(options);
      if (path.startsWith("/api/v1/conversions?"))
        return Promise.resolve({
          items: jobs,
          limit: 10,
          offset: 0,
          total: jobs.length,
        });
      const found = jobs.find(
        (item) => path === `/api/v1/conversions/${item.id}`,
      );
      if (found) return Promise.resolve(found);
      throw new Error(`Unexpected request: ${path}`);
    }),
    multipart: vi.fn(),
    multipartWithMetadata: vi.fn(),
    cancel: vi.fn(),
    download: vi.fn(),
    ...overrides,
  } as unknown as ApiTransport;
}

test("Composer handoff rejects bundled results and retains the exact conversion through a malformed receipt and retry", async () => {
  const bundle = job({ output: "pptx-bundle" });
  const single = job({ id: secondId, output: "pdf" });
  const firstHandoff = deferred<{
    data: { id: string };
    location: string;
    status: number;
  }>();
  const jsonWithMetadata = vi
    .fn()
    .mockReturnValueOnce(firstHandoff.promise)
    .mockResolvedValueOnce({
      data: { id: draftId },
      location: `/api/v1/composer/drafts/${draftId}`,
      status: 201,
    });
  const api = transport([bundle, single], { jsonWithMetadata });
  const controller = new ConversionController(api);
  await controller.load();
  await controller.openJob(bundle.id);
  expect(await controller.handoffActiveResult()).toBeUndefined();
  expect(controller.snapshot().error).toMatch(/single-file output/);
  expect(jsonWithMetadata).not.toHaveBeenCalled();

  await controller.openJob(single.id);
  const pending = controller.handoffActiveResult();
  expect(controller.snapshot().composerPending).toBe(true);
  expect(await controller.handoffActiveResult()).toBeUndefined();
  expect(jsonWithMetadata).toHaveBeenCalledTimes(1);
  expect(jsonWithMetadata).toHaveBeenCalledWith(
    `/api/v1/composer/drafts/from-conversion/${single.id}`,
    expect.anything(),
    expect.objectContaining({ csrf: true, method: "POST" }),
  );

  firstHandoff.resolve({
    data: { id: draftId },
    location: `/api/v1/composer/drafts/${firstId}`,
    status: 201,
  });
  expect(await pending).toBeUndefined();
  expect(controller.snapshot()).toMatchObject({
    active: { id: single.id, output: "pdf" },
    composerPending: false,
    error: "The service returned an unexpected response.",
  });
  expect(await controller.handoffActiveResult()).toBe(draftId);
  expect(controller.snapshot()).toMatchObject({
    active: { id: single.id },
    composerPending: false,
    error: undefined,
  });
  controller.dispose();
});

test("a rejected draft upload can be retried, while an authoritative 401 expires the session", async () => {
  const expire = vi.fn();
  const multipartWithMetadata = vi
    .fn()
    .mockRejectedValueOnce(
      new ApiError(503, "UNAVAILABLE", "Composer is unavailable."),
    )
    .mockRejectedValueOnce(
      new ApiError(401, "AUTHENTICATION_REQUIRED", "Sign in again."),
    );
  const api = transport([], { multipartWithMetadata });
  const controller = new ConversionController(api, expire);
  await controller.load();
  expect(await controller.createComposerDraft()).toBeUndefined();
  expect(controller.snapshot().error).toMatch(/Choose a Markdown or ZIP file/);
  expect(multipartWithMetadata).not.toHaveBeenCalled();

  const source = new File(["# Draft"], "draft.md");
  controller.setSource([source]);
  expect(await controller.createComposerDraft()).toBeUndefined();
  expect(controller.snapshot()).toMatchObject({
    source,
    composerPending: false,
    error: "Composer is unavailable.",
  });
  expect(await controller.createComposerDraft()).toBeUndefined();
  expect(expire).toHaveBeenCalledOnce();
  expect(multipartWithMetadata).toHaveBeenCalledTimes(2);
  expect(
    (multipartWithMetadata.mock.calls[1]![1] as FormData).get("source"),
  ).toBe(source);
});

test("a failed cancellation from an abandoned job cannot overwrite the newly selected result", async () => {
  const first = job({ state: "running", progress: 30 });
  const second = job({ id: secondId, state: "succeeded" });
  const cancellation = deferred<ConversionResponse>();
  const cancel = vi.fn().mockReturnValue(cancellation.promise);
  const schedule = vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>);
  const api = transport([first, second], { cancel });
  const controller = new ConversionController(
    api,
    vi.fn(),
    () => "key",
    schedule,
  );
  await controller.load();
  await controller.openJob(first.id);
  const pending = controller.cancel();
  expect(controller.snapshot().cancelling).toBe(true);
  await controller.openJob(second.id);
  const scheduledBeforeStaleFailure = schedule.mock.calls.length;
  cancellation.reject(new ApiError(403, "FORBIDDEN", "Cannot cancel."));
  await pending;
  expect(controller.snapshot()).toMatchObject({
    active: { id: second.id, state: "succeeded" },
    cancelling: false,
    error: undefined,
  });
  expect(schedule).toHaveBeenCalledTimes(scheduledBeforeStaleFailure);
  controller.dispose();
});

test("an obsolete 401 from a superseded handoff cannot expire the current job's session", async () => {
  const first = job();
  const second = job({ id: secondId });
  const handoff = deferred<{
    data: { id: string };
    location: string;
    status: number;
  }>();
  const expire = vi.fn();
  const jsonWithMetadata = vi.fn().mockReturnValue(handoff.promise);
  const api = transport([first, second], { jsonWithMetadata });
  const controller = new ConversionController(api, expire);
  await controller.load();
  await controller.openJob(first.id);
  const pending = controller.handoffActiveResult();
  await controller.openJob(second.id);
  handoff.reject(new ApiError(401, "AUTHENTICATION_REQUIRED", "Expired."));
  expect(await pending).toBeUndefined();
  expect(expire).not.toHaveBeenCalled();
  expect(controller.snapshot()).toMatchObject({
    active: { id: second.id },
    composerPending: false,
    error: undefined,
  });
  controller.dispose();
});
