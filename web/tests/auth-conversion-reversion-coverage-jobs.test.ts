import type {
  ConversionResponse,
  ReversionCapabilitiesResponse,
  ReversionResponse,
} from "../src/api/generated/types.gen";
import type { ApiTransport } from "../src/api/transport";
import { ConversionController } from "../src/conversion/controller";
import { ReversionController } from "../src/reversion/controller";

const firstId = "00000000-0000-4000-8000-000000000201";
const secondId = "00000000-0000-4000-8000-000000000202";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

function reverseJob(id: string): ReversionResponse {
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
    result_mode: null,
    result_size: null,
    source_extension: ".docx",
    source_family: "word",
    source_stem: "report",
    state: "succeeded",
    step: "complete",
    updated_at: "2026-09-06T08:00:00Z",
  };
}

function forwardJob(id: string): ConversionResponse {
  return {
    attempt: 0,
    cancel_requested: false,
    component_versions: [],
    correlation_id: "correlation",
    created_at: "2026-09-06T08:00:00Z",
    error_code: null,
    error_message: null,
    expires_at: null,
    id,
    output: "docx",
    owner_id: "00000000-0000-4000-8000-000000000001",
    progress: 100,
    state: "succeeded",
    step: "completed",
    template_id: null,
    template_mode: "pandoc-default",
    template_version_id: null,
    updated_at: "2026-09-06T08:00:00Z",
  };
}

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
      modes: ["anydoc", "marp"],
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

test("Revert ignores an obsolete job response after a different job is selected", async () => {
  const oldResponse = deferred<ReversionResponse>();
  let oldSignal: AbortSignal | undefined;
  const json = vi.fn(
    (path: string, _schema: unknown, request: { signal?: AbortSignal }) => {
      if (path === "/api/v1/reversions/capabilities")
        return Promise.resolve(capabilities());
      if (path === "/api/v1/reversions?offset=0&limit=10")
        return Promise.resolve({
          items: [reverseJob(firstId), reverseJob(secondId)],
          limit: 10,
          offset: 0,
          total: 2,
        });
      if (path.endsWith(firstId)) {
        oldSignal = request.signal;
        return oldResponse.promise;
      }
      return Promise.resolve(reverseJob(secondId));
    },
  );
  const controller = new ReversionController({
    json,
  } as unknown as ApiTransport);
  await controller.load();
  const oldSelection = controller.openJob(firstId);
  await controller.openJob(secondId);
  oldResponse.resolve(reverseJob(firstId));
  await oldSelection;

  expect(oldSignal?.aborted).toBe(true);
  expect(controller.snapshot().active?.id).toBe(secondId);
  controller.dispose();
});

test("Convert restores a saved job only while no human has selected another job", async () => {
  const oldResponse = deferred<ConversionResponse>();
  const json = vi.fn((path: string) => {
    if (path === "/api/v1/conversion-options")
      return Promise.resolve({
        conversion_upload_max_bytes: 1_000,
        resolved_template: null,
        selection_source: "pandoc_default",
        template_version_id: null,
      });
    if (path.startsWith("/api/v1/conversions?"))
      return Promise.resolve({
        items: [forwardJob(firstId), forwardJob(secondId)],
        limit: 10,
        offset: 0,
        total: 2,
      });
    if (path.endsWith(firstId)) return oldResponse.promise;
    return Promise.resolve(forwardJob(secondId));
  });
  const controller = new ConversionController({
    json,
  } as unknown as ApiTransport);
  await controller.load();
  const version = controller.inputVersion();
  expect(
    controller.restoreInputs(
      {
        output: "docx",
        dialect: "auto",
        slideLevel: 2,
        query: "",
        activeJobId: firstId,
      },
      version,
    ),
  ).toBe(true);
  await controller.openJob(secondId);
  oldResponse.resolve(forwardJob(firstId));
  await vi.waitFor(() =>
    expect(controller.snapshot().active?.id).toBe(secondId),
  );
  expect(controller.snapshot().active?.id).toBe(secondId);
  controller.dispose();
});

test("Revert discards saved extraction settings removed by current capabilities", async () => {
  const json = vi
    .fn()
    .mockResolvedValueOnce(capabilities())
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 });
  const controller = new ReversionController({
    json,
  } as unknown as ApiTransport);
  await controller.load();
  const source = new File(["office"], "slides.pptx");
  expect(
    controller.restoreInputs(
      {
        source,
        options: {
          extraction: "slides",
          include_images: true,
          include_notes: false,
        },
      },
      controller.inputVersion(),
    ),
  ).toBe(true);
  expect(controller.snapshot()).toMatchObject({
    source,
    options: { extraction: "anydoc" },
    notice:
      "Saved extraction settings are no longer available. Review the current options.",
  });
  controller.dispose();
});
