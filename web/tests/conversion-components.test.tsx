import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach } from "vitest";
import { AuthProvider } from "../src/auth/context";
import { AuthController } from "../src/auth/controller";
import type { ApiTransport } from "../src/api/transport";
import { ConversionController } from "../src/conversion/controller";
import { ConversionWorkspace, saveDownload } from "../src/conversion/workspace";

afterEach(() => vi.restoreAllMocks());

const user = {
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};

const conversionJob = {
  attempt: 0,
  cancel_requested: false,
  component_versions: [],
  correlation_id: "correlation",
  created_at: "2026-09-02T08:00:00Z",
  error_code: null,
  error_message: null,
  expires_at: null,
  id: "00000000-0000-4000-8000-000000000201",
  output: "docx" as const,
  owner_id: user.id,
  progress: 100,
  state: "succeeded",
  step: "complete",
  template_id: null,
  template_mode: "pandoc-default" as const,
  template_version_id: null,
  updated_at: "2026-09-02T08:00:00Z",
};

const accepted = {
  data: conversionJob,
  location: `/api/v1/conversions/${conversionJob.id}`,
  retryAfterSeconds: 1,
  status: 202,
};

function renderWorkspace(conversionApi: Partial<ApiTransport>) {
  const auth = new AuthController({
    json: vi.fn().mockResolvedValue(user),
  } as unknown as ApiTransport);
  const conversion = new ConversionController(
    conversionApi as ApiTransport,
    () => auth.expire(),
    () => "key",
  );
  return {
    auth,
    conversion,
    ...render(
      <AuthProvider controller={auth}>
        <ConversionWorkspace controller={conversion} />
      </AuthProvider>,
    ),
  };
}

test("workspace loads authoritative defaults and exposes accessible conversion controls", async () => {
  const json = vi
    .fn()
    .mockResolvedValueOnce({
      conversion_upload_max_bytes: 1_000_000,
      resolved_template: null,
      selection_source: "pandoc_default",
      template_version_id: null,
    })
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
    .mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 });
  renderWorkspace({ json });
  expect(
    await screen.findByRole("heading", { name: "Convert Markdown" }),
  ).toBeVisible();
  expect(screen.getByLabelText(/Source file/)).toHaveAttribute(
    "accept",
    expect.stringContaining(".md"),
  );
  expect(screen.getByLabelText(/Source file/)).toHaveAttribute(
    "aria-required",
    "true",
  );
  expect(screen.getByLabelText(/Source file/)).not.toHaveAttribute("required");
  expect(screen.getByRole("radio", { name: "DOCX" })).toBeChecked();
  expect(
    screen.getByText("Pandoc default", { selector: "strong" }),
  ).toBeVisible();
  expect(screen.getByText("No recent conversions.")).toBeVisible();
  expect(screen.getByText(/maximum 1000000 bytes/)).toBeVisible();
});

test("template search displays text safely and supports selection and reset", async () => {
  const selected = {
    current_version_id: "00000000-0000-4000-8000-000000000102",
    description: "<img src=x onerror=alert(1)>",
    id: "00000000-0000-4000-8000-000000000101",
    name: "Preferred <script>",
    owner_id: user.id,
    owner_username: "Alice",
    revision: 1,
    status: "active",
  };
  const json = vi
    .fn()
    .mockResolvedValueOnce({
      conversion_upload_max_bytes: 1_000_000,
      resolved_template: selected,
      selection_source: "preferred",
      template_version_id: selected.current_version_id,
    })
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
    .mockResolvedValue({ items: [selected], limit: 20, offset: 0, total: 1 });
  const { container } = renderWorkspace({ json });
  expect(await screen.findByText("Preferred template")).toBeVisible();
  fireEvent.change(
    screen.getByRole("searchbox", { name: "Search active templates" }),
    {
      target: { value: "Preferred" },
    },
  );
  const result = await screen.findByRole("button", {
    name: /Preferred <script>/,
  });
  fireEvent.click(result);
  expect(screen.getByText("Selected template")).toBeVisible();
  expect(container.querySelector("script")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Use Pandoc default" }));
  expect(
    screen.getByText("Pandoc default", { selector: "strong" }),
  ).toBeVisible();
});

test("file validation, drop cardinality, output changes, and duplicate submit are deterministic", async () => {
  let resolveSubmission!: (value: {
    data: typeof conversionJob;
    location: string;
    retryAfterSeconds: number;
    status: number;
  }) => void;
  const multipart = vi.fn(
    () =>
      new Promise<typeof accepted>((resolve) => {
        resolveSubmission = resolve;
      }),
  );
  renderWorkspace({
    json: vi
      .fn()
      .mockResolvedValueOnce({
        conversion_upload_max_bytes: 10,
        resolved_template: null,
        selection_source: "pandoc_default",
        template_version_id: null,
      })
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
      .mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 }),
    multipartWithMetadata: multipart,
  });
  const submit = await screen.findByRole("button", {
    name: "Start conversion",
  });
  const form = submit.closest("form")!;
  fireEvent.submit(form);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Choose a Markdown or ZIP file",
  );
  const input = screen.getByLabelText(/Source file/);
  fireEvent.change(input, { target: { files: [new File(["x"], "bad.txt")] } });
  fireEvent.submit(form);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "ending in .md or .zip",
  );
  fireEvent.change(input, {
    target: { files: [new File(["# ok"], "source.md")] },
  });
  fireEvent.click(screen.getByRole("radio", { name: "PDF" }));
  fireEvent.submit(form);
  fireEvent.click(
    screen.getByRole("button", { name: "Submitting conversion…" }),
  );
  expect(multipart).toHaveBeenCalledOnce();
  resolveSubmission(accepted);
  expect(
    await screen.findByText("Your conversion is ready to download."),
  ).toBeVisible();
});

test("recent jobs reopen, cancellation is perceivable, and expired jobs have no download", async () => {
  const running = {
    ...conversionJob,
    progress: 20,
    state: "running",
    step: "validating",
  };
  const json = vi
    .fn()
    .mockResolvedValueOnce({
      conversion_upload_max_bytes: 1_000_000,
      resolved_template: null,
      selection_source: "pandoc_default",
      template_version_id: null,
    })
    .mockResolvedValueOnce({ items: [running], limit: 10, offset: 0, total: 1 })
    .mockResolvedValueOnce(running)
    .mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 });
  const cancel = vi
    .fn()
    .mockResolvedValue({ ...running, cancel_requested: true });
  renderWorkspace({ json, cancel });
  fireEvent.click(
    await screen.findByRole("button", {
      name: /Conversion 00000000 · running/,
    }),
  );
  expect(
    await screen.findByRole("progressbar", { name: "Conversion progress" }),
  ).toHaveValue(20);
  fireEvent.click(screen.getByRole("button", { name: "Cancel conversion" }));
  expect(await screen.findByText(/Cancellation requested/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Download result" })).toBeNull();
});

test("validated downloads use the server filename and dropped files reach submission", async () => {
  const createObjectURL = vi
    .spyOn(URL, "createObjectURL")
    .mockReturnValue("blob:validated-result");
  const revokeObjectURL = vi
    .spyOn(URL, "revokeObjectURL")
    .mockImplementation(() => undefined);
  const anchorClick = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
  const multipart = vi.fn().mockResolvedValue(accepted);
  const download = vi.fn().mockResolvedValue(
    new Response("document", {
      headers: {
        "Cache-Control": "private, no-store",
        "Content-Disposition": 'attachment; filename="dropped.docx"',
        "Content-Type": "application/octet-stream",
        "X-Content-Type-Options": "nosniff",
      },
    }),
  );
  renderWorkspace({
    json: vi
      .fn()
      .mockResolvedValueOnce({
        conversion_upload_max_bytes: 1_000_000,
        resolved_template: null,
        selection_source: "pandoc_default",
        template_version_id: null,
      })
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
      .mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 }),
    multipartWithMetadata: multipart,
    download,
  });
  const source = new File(["# dropped"], "dropped.md");
  const hint = await screen.findByText(/Choose or drop exactly one/);
  const dropZone = hint.closest("label")!;
  fireEvent.dragEnter(dropZone);
  expect(screen.getByText("Drop the file now.")).toBeVisible();
  fireEvent.dragLeave(dropZone);
  expect(screen.getByText(/Choose or drop exactly one/)).toBeVisible();
  fireEvent.dragEnter(dropZone);
  fireEvent.dragOver(dropZone);
  fireEvent.drop(dropZone, { dataTransfer: { files: [source] } });
  expect(screen.getByText("Selected dropped.md (9 bytes).")).toBeVisible();
  expect(
    (screen.getByLabelText(/Source file/) as HTMLInputElement).files,
  ).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Start conversion" }));
  await screen.findByText("Your conversion is ready to download.");
  expect((multipart.mock.calls[0]![1] as FormData).get("source")).toBe(source);
  fireEvent.click(screen.getByRole("button", { name: "Download result" }));
  await vi.waitFor(() => expect(anchorClick).toHaveBeenCalledOnce());
  expect(createObjectURL).toHaveBeenCalledOnce();
  await vi.waitFor(() =>
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:validated-result"),
  );

  const unreadable = new Response("unreadable", {
    headers: {
      "Cache-Control": "private, no-store",
      "Content-Disposition": 'attachment; filename="dropped.docx"',
      "Content-Type": "application/octet-stream",
      "X-Content-Type-Options": "nosniff",
    },
  });
  vi.spyOn(unreadable, "blob").mockRejectedValue(new TypeError("body failed"));
  download.mockResolvedValueOnce(unreadable);
  fireEvent.click(screen.getByRole("button", { name: "Download result" }));
  expect(
    await screen.findByText("The result could not be downloaded."),
  ).toBeVisible();
  expect(anchorClick).toHaveBeenCalledOnce();
});

test("download saving defers object URL revocation", () => {
  const createObjectURL = vi
    .spyOn(URL, "createObjectURL")
    .mockReturnValue("blob:deferred-result");
  const revokeObjectURL = vi
    .spyOn(URL, "revokeObjectURL")
    .mockImplementation(() => undefined);
  const anchorClick = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
  const deferred: Array<() => void> = [];

  saveDownload(
    { blob: new Blob(["document"]), filename: "result.docx" },
    (callback) => deferred.push(callback),
  );

  expect(createObjectURL).toHaveBeenCalledOnce();
  expect(anchorClick).toHaveBeenCalledOnce();
  expect(revokeObjectURL).not.toHaveBeenCalled();
  expect(deferred).toHaveLength(1);
  deferred[0]!();
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:deferred-result");
});

test("unavailable options expose a bounded retry without private failure details", async () => {
  const json = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("private backend hostname"))
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
    .mockResolvedValueOnce({
      conversion_upload_max_bytes: 100,
      resolved_template: null,
      selection_source: "pandoc_default",
      template_version_id: null,
    })
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
    .mockResolvedValue({ items: [], limit: 20, offset: 0, total: 0 });
  renderWorkspace({ json });
  expect(
    await screen.findByRole("heading", { name: "Conversion is unavailable" }),
  ).toBeVisible();
  expect(document.body).not.toHaveTextContent("private backend hostname");
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(
    await screen.findByRole("heading", { name: "New conversion" }),
  ).toBeVisible();
});
