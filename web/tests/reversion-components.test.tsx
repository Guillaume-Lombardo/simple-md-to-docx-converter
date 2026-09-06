import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach } from "vitest";
import type { ApiTransport } from "../src/api/transport";
import { AuthController } from "../src/auth/controller";
import { AuthProvider } from "../src/auth/context";
import { ReversionController } from "../src/reversion/controller";
import {
  ReversionWorkspace,
  saveReversionDownload,
} from "../src/reversion/workspace";

afterEach(() => vi.restoreAllMocks());

const user = {
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};

const capabilities = {
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
      extensions: [".docx", ".pdf"],
      family: "word" as const,
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
  result_package_modes: ["markdown" as const],
  schema_version: 1,
};

const reversionJob = {
  attempt: 0,
  cancel_requested: false,
  component_versions: [] as Array<[string, string]>,
  correlation_id: "correlation",
  created_at: "2026-09-06T08:00:00Z",
  detected_format: "docx",
  error_code: null,
  error_message: null,
  expires_at: null,
  id: "00000000-0000-4000-8000-000000000201",
  owner_id: user.id,
  result_mode: "markdown" as const,
  result_size: 10,
  source_extension: ".docx",
  source_family: "word" as const,
  source_stem: "report",
  state: "succeeded" as const,
  step: "complete" as const,
  updated_at: "2026-09-06T08:00:00Z",
};

function renderWorkspace(reversionApi: Partial<ApiTransport>) {
  const auth = new AuthController({
    json: vi.fn().mockResolvedValue(user),
  } as unknown as ApiTransport);
  const reversion = new ReversionController(
    reversionApi as ApiTransport,
    () => auth.expire(),
    () => "key",
  );
  return {
    auth,
    reversion,
    ...render(
      <AuthProvider controller={auth}>
        <ReversionWorkspace controller={reversion} />
      </AuthProvider>,
    ),
  };
}

test("workspace uses authoritative capabilities for accessible controls and copy", async () => {
  renderWorkspace({
    json: vi
      .fn()
      .mockResolvedValueOnce(capabilities)
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
  });
  expect(
    await screen.findByRole("heading", { name: "Revert to Markdown" }),
  ).toBeVisible();
  expect(screen.getAllByText("Experimental")).not.toHaveLength(0);
  expect(
    screen.getByRole("link", { name: "Revert, Experimental" }),
  ).toHaveAttribute("aria-current", "page");
  expect(screen.getByLabelText(/Source document/)).toHaveAttribute(
    "accept",
    ".docx,.pdf",
  );
  expect(
    screen.getByText(/server currently accepts .docx, .pdf/),
  ).toBeVisible();
  expect(screen.getByText(/Extensions are a selection hint/)).toBeVisible();
  expect(screen.getByText(/CPU-only, low-compute/)).toBeVisible();
  expect(screen.getByText(/OCR is not available/)).toBeVisible();
  expect(screen.getByText(/Images are not preserved/)).toBeVisible();
  expect(screen.getByText("No recent document conversions.")).toBeVisible();
  expect(
    screen.getByRole("heading", { name: "New conversion to Markdown" })
      .parentElement?.parentElement,
  ).toHaveClass("lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.7fr)]");
});

test("drop, submission, status, and download form one browser workflow", async () => {
  const createObjectURL = vi
    .spyOn(URL, "createObjectURL")
    .mockReturnValue("blob:markdown-result");
  const revokeObjectURL = vi
    .spyOn(URL, "revokeObjectURL")
    .mockImplementation(() => undefined);
  const anchorClick = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
  const multipartWithMetadata = vi.fn().mockResolvedValue({
    data: reversionJob,
    location: `/api/v1/reversions/${reversionJob.id}`,
    retryAfterSeconds: 1,
    status: 202,
  });
  const source = new File(["document"], "report.docx");
  renderWorkspace({
    download: vi.fn().mockResolvedValue(
      new Response("# report", {
        headers: {
          "Cache-Control": "private, no-store",
          "Content-Disposition": 'attachment; filename="report.md"',
          "Content-Type": "text/markdown; charset=utf-8",
          "X-Content-Type-Options": "nosniff",
        },
      }),
    ),
    json: vi
      .fn()
      .mockResolvedValueOnce(capabilities)
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
    multipartWithMetadata,
  });
  const hint = await screen.findByText(/Choose or drop exactly one/);
  const dropZone = hint.closest("label")!;
  fireEvent.dragEnter(dropZone);
  expect(screen.getByText("Drop the document now.")).toBeVisible();
  fireEvent.dragLeave(dropZone);
  fireEvent.dragOver(dropZone);
  fireEvent.drop(dropZone, { dataTransfer: { files: [source] } });
  expect(screen.getByText(/Selected report.docx/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Start conversion" }));
  expect(
    await screen.findByText("Your Markdown result is ready."),
  ).toBeVisible();
  expect(
    (multipartWithMetadata.mock.calls[0]![1] as FormData).get("source"),
  ).toBe(source);
  fireEvent.click(screen.getByRole("button", { name: "Download result" }));
  await vi.waitFor(() => expect(anchorClick).toHaveBeenCalledOnce());
  expect(createObjectURL).toHaveBeenCalledOnce();
  await vi.waitFor(() =>
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:markdown-result"),
  );
});

test("recent running jobs can be reopened and cancelled", async () => {
  const running = {
    ...reversionJob,
    result_mode: null,
    result_size: null,
    state: "running" as const,
    step: "converting" as const,
  };
  const json = vi
    .fn()
    .mockResolvedValueOnce(capabilities)
    .mockResolvedValueOnce({ items: [running], limit: 10, offset: 0, total: 1 })
    .mockResolvedValueOnce(running);
  const cancel = vi.fn().mockResolvedValue({
    ...running,
    cancel_requested: true,
  });
  renderWorkspace({ cancel, json });
  fireEvent.click(
    await screen.findByRole("button", { name: "report.docx · running" }),
  );
  expect(await screen.findByText(/Converting the document/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Cancel conversion" }));
  expect(await screen.findByText(/Cancellation requested/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Download result" })).toBeNull();
});

test("unavailable capabilities fail closed and retry without leaking details", async () => {
  const json = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("private host"))
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
    .mockResolvedValueOnce(capabilities)
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 });
  renderWorkspace({ json });
  expect(
    await screen.findByRole("heading", { name: "Revert is unavailable" }),
  ).toBeVisible();
  expect(screen.getByText(/Submission is disabled/)).toBeVisible();
  expect(document.body).not.toHaveTextContent("private host");
  expect(screen.queryByRole("button", { name: "Start conversion" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(
    await screen.findByRole("heading", { name: "New conversion to Markdown" }),
  ).toBeVisible();
});

test("download saving defers object URL revocation", () => {
  const createObjectURL = vi
    .spyOn(URL, "createObjectURL")
    .mockReturnValue("blob:deferred");
  const revokeObjectURL = vi
    .spyOn(URL, "revokeObjectURL")
    .mockImplementation(() => undefined);
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
  const deferred: Array<() => void> = [];
  saveReversionDownload(
    { blob: new Blob(["result"]), filename: "result.md" },
    (callback) => deferred.push(callback),
  );
  expect(createObjectURL).toHaveBeenCalledOnce();
  expect(click).toHaveBeenCalledOnce();
  expect(revokeObjectURL).not.toHaveBeenCalled();
  deferred[0]!();
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:deferred");
});
