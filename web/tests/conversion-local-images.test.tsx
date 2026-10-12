import { fireEvent, render, screen } from "@testing-library/react";
import JSZip from "jszip";
import { afterEach } from "vitest";
import { AuthProvider } from "../src/auth/context";
import { AuthController } from "../src/auth/controller";
import type { ApiTransport } from "../src/api/transport";
import { ConversionController } from "../src/conversion/controller";
import { ConversionWorkspace } from "../src/conversion/workspace";
import {
  ESCAPING_IMAGE_PATH,
  MISSING_IMAGE,
} from "../src/conversion/packaging";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../src/conversion/persistence", () => ({
  ownerStorageEpoch: vi.fn().mockReturnValue(0),
  resumeOwnerStorage: vi.fn(),
  readConversionInputs: vi.fn().mockResolvedValue(undefined),
  writeConversionInputs: vi.fn().mockResolvedValue(undefined),
  clearConversionInputs: vi.fn().mockResolvedValue(undefined),
}));

afterEach(() => {
  vi.restoreAllMocks();
});

const user = {
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};

const job = {
  attempt: 0,
  cancel_requested: false,
  component_versions: [],
  correlation_id: "correlation",
  created_at: "2026-10-10T08:00:00Z",
  error_code: null,
  error_message: null,
  expires_at: null,
  id: "00000000-0000-4000-8000-000000000301",
  output: "docx" as const,
  source_filename: "report.zip",
  owner_id: user.id,
  progress: 0,
  state: "queued",
  step: "queued",
  template_id: null,
  template_mode: "pandoc-default" as const,
  template_version_id: null,
  updated_at: "2026-10-10T08:00:00Z",
};

const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 9]);

function transport(overrides: Partial<ApiTransport> = {}): ApiTransport {
  return {
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
    multipart: vi.fn(),
    multipartWithMetadata: vi.fn().mockResolvedValue({
      data: job,
      location: `/api/v1/conversions/${job.id}`,
      retryAfterSeconds: 1,
      status: 202,
    }),
    cancel: vi.fn(),
    download: vi.fn(),
    ...overrides,
  } as unknown as ApiTransport;
}

function selection(markdown = "# R\n\n![a](img/a.png)\n") {
  return [
    { file: new File([markdown], "report.md"), path: "report/report.md" },
    { file: new File([png], "a.png"), path: "report/img/a.png" },
  ];
}

async function loaded(api = transport()) {
  const controller = new ConversionController(
    api,
    undefined,
    () => "key",
    (callback) => setTimeout(callback, 60_000),
  );
  await controller.load();
  return controller;
}

test("several selected files are packaged before an unchanged multipart submission", async () => {
  const api = transport();
  const controller = await loaded(api);
  const states: boolean[] = [];
  controller.subscribe((state) => states.push(state.packaging));

  await controller.selectFiles(selection());

  const state = controller.snapshot();
  expect(states).toContain(true);
  expect(state.packaging).toBe(false);
  expect(state.source?.name).toBe("report.zip");
  expect(state.notice).toBe(
    "Packaged report.md with 1 local image as report.zip.",
  );
  await controller.submit();
  const form = vi.mocked(api.multipartWithMetadata).mock.calls[0]![1];
  const uploaded = form.get("source") as File;
  expect(uploaded.name).toBe("report.zip");
  const archive = await JSZip.loadAsync(await uploaded.arrayBuffer());
  expect(Object.keys(archive.files)).toEqual(["document.md", "img/a.png"]);
  expect([...form.keys()]).toEqual(["source", "output"]);
  controller.dispose();
});

test("plural notices, stable packaging errors and unreadable files", async () => {
  const controller = await loaded();
  await controller.selectFiles([
    ...selection("![a](img/a.png) ![b](b.png)"),
    { file: new File([png], "b.png"), path: "report/b.png" },
  ]);
  expect(controller.snapshot().notice).toMatch(/with 2 local images/);

  await controller.selectFiles(selection("![a](../a.png)"));
  expect(controller.snapshot()).toMatchObject({
    error: ESCAPING_IMAGE_PATH,
    packaging: false,
    source: undefined,
  });

  const unreadable = new File(["x"], "doc.md");
  vi.spyOn(unreadable, "arrayBuffer").mockRejectedValue(new Error("gone"));
  await controller.selectFiles([
    { file: unreadable, path: "doc.md" },
    { file: new File([png], "a.png"), path: "a.png" },
  ]);
  expect(controller.snapshot().error).toBe(
    "The selected files could not be read. Choose them again.",
  );
  controller.dispose();
});

test("a newer selection supersedes packaging and blocks submission meanwhile", async () => {
  const api = transport();
  const controller = await loaded(api);
  let release!: (value: ArrayBuffer) => void;
  const slow = new File(["![a](a.png)"], "doc.md");
  vi.spyOn(slow, "arrayBuffer").mockReturnValue(
    new Promise((resolve) => {
      release = resolve;
    }),
  );
  const pending = controller.selectFiles([
    { file: slow, path: "doc.md" },
    { file: new File([png], "a.png"), path: "a.png" },
  ]);
  expect(controller.snapshot().packaging).toBe(true);
  await controller.submit();
  expect(api.multipartWithMetadata).not.toHaveBeenCalled();

  const replacement = new File(["# plain"], "plain.md");
  await controller.selectFiles([{ file: replacement, path: "plain.md" }]);
  release(new TextEncoder().encode("![a](a.png)").buffer as ArrayBuffer);
  await pending;
  expect(controller.snapshot().source).toBe(replacement);

  let fail!: (error: Error) => void;
  const failing = new File(["x"], "doc.md");
  vi.spyOn(failing, "arrayBuffer").mockReturnValue(
    new Promise((_resolve, reject) => {
      fail = reject;
    }),
  );
  const rejected = controller.selectFiles([
    { file: failing, path: "doc.md" },
    { file: new File([png], "a.png"), path: "a.png" },
  ]);
  controller.dispose();
  fail(new Error("late"));
  await rejected;
  expect(controller.snapshot().error).toBeUndefined();

  await controller.selectFiles([]);
  expect(controller.snapshot().source).toBeUndefined();
});

function renderWorkspace(api: ApiTransport, presentation = false) {
  const auth = new AuthController({
    json: vi.fn().mockResolvedValue(user),
  } as unknown as ApiTransport);
  const conversion = new ConversionController(
    api,
    () => auth.expire(),
    () => "key",
    undefined,
    undefined,
    presentation,
  );
  render(
    <AuthProvider controller={auth}>
      <ConversionWorkspace
        controller={conversion}
        presentation={presentation}
      />
    </AuthProvider>,
  );
  return conversion;
}

test.each([false, true])(
  "workspace %# accepts multi-file, folder and dropped-folder selections",
  async (presentation) => {
    const api = transport();
    renderWorkspace(api, presentation);
    const input = (await screen.findByLabelText(
      /Source file/,
    )) as HTMLInputElement;
    // Settle the debounced initial template search, which clears old errors.
    await vi.waitFor(() => expect(api.json).toHaveBeenCalledTimes(3));
    expect(input).toHaveAttribute("multiple");
    expect(input.getAttribute("accept")).toContain(".png");
    const folder = screen.getByLabelText(/Markdown folder/);
    expect(folder).toHaveAttribute("webkitdirectory");
    expect(
      screen.getByText(/a Markdown file together with its images/),
    ).toBeVisible();

    fireEvent.change(input, {
      target: {
        files: [new File(["![a](a.png)"], "doc.md"), new File([png], "a.png")],
      },
    });
    expect(
      await screen.findByText("Packaged doc.md with 1 local image as doc.zip."),
    ).toBeVisible();
    expect(screen.getByText(/Selected doc\.zip/)).toBeVisible();

    const nested = new File([png], "a.png");
    Object.defineProperty(nested, "webkitRelativePath", {
      value: "folder/img/a.png",
    });
    const markdown = new File(["![a](img/missing.png)"], "doc.md");
    Object.defineProperty(markdown, "webkitRelativePath", {
      value: "folder/doc.md",
    });
    fireEvent.change(folder, { target: { files: [markdown, nested] } });
    expect(await screen.findByText(MISSING_IMAGE)).toBeVisible();
    fireEvent.change(folder, { target: { files: null } });

    const dropZone = input.closest("label")!;
    const dropped = new File(["![a](img/a.png)"], "dropped.md");
    fireEvent.drop(dropZone, {
      dataTransfer: {
        files: [],
        items: [
          {
            webkitGetAsEntry: () => ({
              createReader: () => {
                let done = false;
                return {
                  readEntries: (resolve: (entries: unknown[]) => void) => {
                    resolve(
                      done
                        ? []
                        : [
                            {
                              file: (cb: (file: File) => void) => cb(dropped),
                              isDirectory: false,
                              isFile: true,
                              name: "dropped.md",
                            },
                            {
                              file: (cb: (file: File) => void) =>
                                cb(new File([png], "a.png")),
                              isDirectory: false,
                              isFile: true,
                              name: "img/a.png",
                            },
                          ],
                    );
                    done = true;
                  },
                };
              },
              isDirectory: true,
              isFile: false,
              name: "project",
            }),
          },
        ],
      },
    });
    expect(
      await screen.findByText(
        "Packaged dropped.md with 1 local image as dropped.zip.",
      ),
    ).toBeVisible();
  },
);

test("a failed folder traversal falls back to the dropped file list", async () => {
  renderWorkspace(transport());
  const input = await screen.findByLabelText(/Source file/);
  const file = new File(["# only"], "only.md");
  fireEvent.drop(input.closest("label")!, {
    dataTransfer: {
      files: [file],
      items: [
        {
          webkitGetAsEntry: () => ({
            createReader: () => ({
              readEntries: (
                _resolve: unknown,
                reject: (error: Error) => void,
              ) => reject(new Error("denied")),
            }),
            isDirectory: true,
            isFile: false,
            name: "denied",
          }),
        },
      ],
    },
  });
  expect(await screen.findByText(/Selected only\.md/)).toBeVisible();
});
