import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AdministrationApi } from "../src/admin/api";
import { TemplatesWorkspace } from "../src/admin/templates";
import { UsersWorkspace } from "../src/admin/users";
import { ApiError } from "../src/api/transport";

const alice = {
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};
const admin = {
  ...alice,
  effective_idle_minutes: 15,
  id: "00000000-0000-4000-8000-000000000099",
  role: "admin" as const,
  username: "Admin",
};
const fallback = {
  current_version_id: "10000000-0000-4000-8000-000000000001",
  description: "Fallback body",
  id: "20000000-0000-4000-8000-000000000001",
  name: "Fallback template",
  owner_id: alice.id,
  owner_username: "Alice",
  revision: 1,
  status: "active" as const,
};
const preferred = {
  current_version_id: "10000000-0000-4000-8000-000000000002",
  description: "Preferred body",
  id: "20000000-0000-4000-8000-000000000002",
  name: "Preferred template",
  owner_id: admin.id,
  owner_username: "Admin",
  revision: 2,
  status: "active" as const,
};
const archived = {
  ...preferred,
  id: "20000000-0000-4000-8000-000000000003",
  name: "Archived style",
  status: "archived" as const,
};
const version = {
  created_at: "2026-09-02T10:00:00Z",
  created_by: admin.id,
  declared_fonts: ["Carlito"],
  id: preferred.current_version_id,
  number: 2,
  resolved_fonts: [["Carlito", "Carlito"]] as [string, string][],
  restored_from_version_id: null,
  sha256: "a".repeat(64),
  size: 120,
  template_id: preferred.id,
  validation_trace: ["static_ooxml"],
};

function templateDownloadResponse(filename: string): Response {
  return new Response("docx bytes", {
    headers: {
      "Cache-Control": "private, no-store",
      "Content-Disposition": `attachment; filename="${filename}"`,
      "Content-Type":
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

function templateApi(overrides: Record<string, unknown> = {}) {
  return {
    allTemplates: vi.fn().mockResolvedValue([fallback, preferred, archived]),
    archive: vi.fn().mockResolvedValue({ data: archived, etag: '"next"' }),
    clearPreferred: vi.fn().mockResolvedValue(undefined),
    create: vi.fn().mockResolvedValue(preferred),
    delete: vi.fn().mockResolvedValue(undefined),
    replace: vi.fn().mockResolvedValue({ data: version, etag: '"next"' }),
    restore: vi.fn().mockResolvedValue({ data: version, etag: '"next"' }),
    setFallback: vi.fn().mockResolvedValue(undefined),
    setPreferred: vi.fn().mockResolvedValue(undefined),
    template: vi.fn().mockImplementation((id: string) =>
      Promise.resolve({
        data: id === archived.id ? archived : preferred,
        etag: '"template-etag"',
      }),
    ),
    templateContent: vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(templateDownloadResponse("template-v2.docx")),
      ),
    templateContext: vi.fn().mockResolvedValue({
      preferred_template_id: preferred.id,
      system_fallback_template_id: fallback.id,
      template_max_archive_bytes: 1024,
    }),
    updateMetadata: vi
      .fn()
      .mockResolvedValue({ data: preferred, etag: '"next"' }),
    versions: vi
      .fn()
      .mockResolvedValue([
        version,
        { ...version, id: "10000000-0000-4000-8000-000000000003", number: 1 },
      ]),
    ...overrides,
  };
}

test("template library loads all entries and filters locally without markup interpretation", async () => {
  const api = templateApi();
  const { container } = render(
    <TemplatesWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={alice}
    />,
  );
  expect(await screen.findByText("Fallback body")).toBeVisible();
  expect(screen.getAllByText("Preferred body")).toHaveLength(2);
  fireEvent.change(screen.getByRole("textbox", { name: /search name/i }), {
    target: { value: "Admin" },
  });
  expect(screen.queryByText("Fallback body")).toBeNull();
  expect(screen.getAllByText("Preferred body")).toHaveLength(2);
  fireEvent.click(screen.getByRole("checkbox", { name: "My templates" }));
  expect(screen.getByText("No templates match these filters.")).toBeVisible();
  fireEvent.change(screen.getByRole("textbox", { name: /search name/i }), {
    target: { value: "<img src=x>" },
  });
  expect(container.querySelector("img")).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: /search name/i }), {
    target: { value: "" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "My templates" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Status" }), {
    target: { value: "archived" },
  });
  expect(screen.getByRole("heading", { name: "Archived style" })).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: "Fallback template" }),
  ).toBeNull();
  expect(api.allTemplates).toHaveBeenCalledWith({}, expect.any(AbortSignal));
});

test("template load and revision failures remain safe and actionable", async () => {
  const expire = vi.fn();
  const failed = templateApi({
    allTemplates: vi
      .fn()
      .mockRejectedValue(new ApiError(403, "FORBIDDEN", "unsafe")),
  });
  const { unmount } = render(
    <TemplatesWorkspace
      api={failed as unknown as AdministrationApi}
      expire={expire}
      user={alice}
    />,
  );
  expect(
    await screen.findByText("You are not allowed to perform this action."),
  ).toBeVisible();
  expect(expire).not.toHaveBeenCalled();
  unmount();

  const missingEtag = templateApi({
    template: vi.fn().mockResolvedValue({ data: preferred }),
  });
  const missingRender = render(
    <TemplatesWorkspace
      api={missingEtag as unknown as AdministrationApi}
      expire={expire}
      user={admin}
    />,
  );
  await screen.findByText("Fallback body");
  fireEvent.click(
    within(screen.getByText("Preferred template").closest("li")!).getByRole(
      "button",
      { name: "Manage" },
    ),
  );
  expect(
    await screen.findByText(/did not provide the template revision/),
  ).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: /Manage Preferred/ }),
  ).toBeNull();
  missingRender.unmount();

  const manageFailure = templateApi({
    template: vi.fn().mockRejectedValue(new TypeError("private failure")),
  });
  render(
    <TemplatesWorkspace
      api={manageFailure as unknown as AdministrationApi}
      expire={expire}
      user={admin}
    />,
  );
  await screen.findByText("Fallback body");
  fireEvent.click(
    within(screen.getByText("Preferred template").closest("li")!).getByRole(
      "button",
      { name: "Manage" },
    ),
  );
  expect(
    await screen.findByText("The template could not be loaded. Try again."),
  ).toBeVisible();
  expect(screen.queryByText("private failure")).toBeNull();
});

test("template creation validates files, prevents duplicates, and reloads after success", async () => {
  const interaction = userEvent.setup();
  let finish!: (value: unknown) => void;
  let finishReload!: (value: unknown) => void;
  const api = templateApi({
    allTemplates: vi
      .fn()
      .mockResolvedValueOnce([fallback, preferred, archived])
      .mockImplementationOnce(
        () => new Promise((resolve) => (finishReload = resolve)),
      ),
    create: vi
      .fn()
      .mockImplementation(() => new Promise((resolve) => (finish = resolve))),
  });
  render(
    <TemplatesWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={alice}
    />,
  );
  await screen.findByText("Fallback body");
  fireEvent.submit(
    screen.getByRole("button", { name: "Create template" }).closest("form")!,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("non-empty DOCX");
  const file = new File(["docx"], "owned.docx", {
    type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  });
  await interaction.upload(screen.getByLabelText("DOCX file"), file);
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "Owned" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Description" }), {
    target: { value: "Body" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: /Expected fonts/ }), {
    target: { value: " Carlito, Caladea " },
  });
  const submit = screen.getByRole("button", { name: "Create template" });
  fireEvent.submit(submit.closest("form")!);
  fireEvent.submit(submit.closest("form")!);
  expect(api.create).toHaveBeenCalledOnce();
  expect(api.create.mock.calls[0]![0]).toMatchObject({
    expectedFonts: " Carlito, Caladea ",
    name: "Owned",
  });
  act(() => finish(preferred));
  await waitFor(() => expect(api.allTemplates).toHaveBeenCalledTimes(2));
  expect(screen.queryByText("Template created.")).toBeNull();
  expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  fireEvent.submit(submit.closest("form")!);
  expect(api.create).toHaveBeenCalledOnce();
  expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Owned");
  expect(screen.getByRole("textbox", { name: "Name" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Description" })).toHaveValue(
    "Body",
  );
  expect(screen.getByRole("textbox", { name: "Description" })).toBeDisabled();
  expect(screen.getByLabelText("DOCX file")).toBeDisabled();
  act(() => finishReload([fallback, preferred, archived]));
  expect(await screen.findByText("Template created.")).toBeVisible();
  expect(api.allTemplates).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("");
  expect(screen.getByRole("textbox", { name: "Description" })).toHaveValue("");
  expect(screen.getByRole("button", { name: "Create template" })).toBeEnabled();
});

test("template creation enforces the authoritative upload bound", async () => {
  const interaction = userEvent.setup();
  const api = templateApi();
  render(
    <TemplatesWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={alice}
    />,
  );
  await screen.findByText("Fallback body");
  const oversized = new File([new Uint8Array(1025)], "large.docx", {
    type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  });
  await interaction.upload(screen.getByLabelText("DOCX file"), oversized);
  fireEvent.submit(
    screen.getByRole("button", { name: "Create template" }).closest("form")!,
  );
  expect(
    screen.getByText(/exceeds the configured 1024 byte limit/),
  ).toBeVisible();
  expect(api.create).not.toHaveBeenCalled();
});

test("template management uses server ETag, edits and explicitly clears replacement fonts", async () => {
  const interaction = userEvent.setup({ applyAccept: false });
  const api = templateApi();
  render(
    <TemplatesWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  await screen.findByRole("heading", { name: "Preferred template" });
  const preferredItem = screen.getByText("Preferred template").closest("li")!;
  fireEvent.click(
    within(preferredItem).getByRole("button", { name: "Manage" }),
  );
  expect(
    await screen.findByRole("heading", { name: "Manage Preferred template" }),
  ).toBeVisible();
  fireEvent.change(screen.getByRole("textbox", { name: "Template name" }), {
    target: { value: "Renamed" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save details" }));
  await waitFor(() =>
    expect(api.updateMetadata).toHaveBeenCalledWith(
      preferred.id,
      '"template-etag"',
      "Renamed",
      "Preferred body",
      expect.any(AbortSignal),
    ),
  );

  fireEvent.click(
    within(screen.getByText("Preferred template").closest("li")!).getByRole(
      "button",
      { name: "Manage" },
    ),
  );
  await screen.findByRole("heading", { name: "Manage Preferred template" });
  const invalid = new File(["not a document"], "unsafe.txt", {
    type: "text/plain",
  });
  await interaction.upload(screen.getByLabelText("Replacement DOCX"), invalid);
  fireEvent.submit(
    screen.getByRole("button", { name: "Replace content" }).closest("form")!,
  );
  expect(screen.getByText(/\.docx extension/)).toBeVisible();
  expect(api.replace).not.toHaveBeenCalled();
  fireEvent.change(
    screen.getByRole("textbox", { name: /Replacement expected fonts/ }),
    { target: { value: "   " } },
  );
  const replacement = new File(["new"], "new.docx", {
    type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  });
  await interaction.upload(
    screen.getByLabelText("Replacement DOCX"),
    replacement,
  );
  fireEvent.submit(
    screen.getByRole("button", { name: "Replace content" }).closest("form")!,
  );
  await waitFor(() =>
    expect(api.replace).toHaveBeenCalledWith(
      preferred.id,
      '"template-etag"',
      replacement,
      "   ",
      expect.any(AbortSignal),
    ),
  );
});

test("stale template metadata resets every field to the authoritative snapshot and waits for explicit retry", async () => {
  const authoritativeVersion = {
    ...version,
    declared_fonts: ["Aptos", "Carlito"],
    id: "10000000-0000-4000-8000-000000000009",
    number: 9,
  };
  const authoritativeTemplate = {
    ...preferred,
    current_version_id: authoritativeVersion.id,
    description: "Authoritative description",
    name: "Authoritative template",
    revision: 9,
  };
  const api = templateApi({
    template: vi
      .fn()
      .mockResolvedValueOnce({ data: preferred, etag: '"template-etag"' })
      .mockResolvedValueOnce({
        data: authoritativeTemplate,
        etag: '"authoritative-etag"',
      }),
    updateMetadata: vi
      .fn()
      .mockRejectedValueOnce(new ApiError(412, "STALE", "unsafe"))
      .mockResolvedValueOnce({
        data: authoritativeTemplate,
        etag: '"next"',
      }),
    versions: vi
      .fn()
      .mockResolvedValueOnce([version])
      .mockResolvedValueOnce([authoritativeVersion]),
  });
  render(
    <TemplatesWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  await screen.findByText("Fallback body");
  fireEvent.click(
    within(screen.getByText("Preferred template").closest("li")!).getByRole(
      "button",
      { name: "Manage" },
    ),
  );
  const name = await screen.findByRole("textbox", { name: "Template name" });
  fireEvent.change(name, { target: { value: "Stale local name" } });
  fireEvent.change(
    screen.getByRole("textbox", { name: "Template description" }),
    { target: { value: "Stale local description" } },
  );
  fireEvent.change(
    screen.getByRole("textbox", { name: /Replacement expected fonts/ }),
    { target: { value: "Stale Local Font" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Save details" }));

  expect(await screen.findByText(/changed on the server/)).toBeVisible();
  expect(
    screen.getByRole("heading", { name: "Manage Authoritative template" }),
  ).toBeVisible();
  expect(screen.getByRole("textbox", { name: "Template name" })).toHaveValue(
    "Authoritative template",
  );
  expect(
    screen.getByRole("textbox", { name: "Template description" }),
  ).toHaveValue("Authoritative description");
  expect(
    screen.getByRole("textbox", { name: /Replacement expected fonts/ }),
  ).toHaveValue("Aptos, Carlito");
  expect(screen.getByText(/Version 9 · 120 bytes/)).toBeVisible();
  expect(api.updateMetadata).toHaveBeenCalledOnce();

  await act(async () => Promise.resolve());
  expect(api.updateMetadata).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Save details" }));
  await waitFor(() => expect(api.updateMetadata).toHaveBeenCalledTimes(2));
  expect(api.updateMetadata.mock.calls[1]).toEqual([
    preferred.id,
    '"authoritative-etag"',
    "Authoritative template",
    "Authoritative description",
    expect.any(AbortSignal),
  ]);
});

test("template download controls preserve server filenames for current and historical content", async () => {
  const api = templateApi({
    templateContent: vi
      .fn()
      .mockImplementationOnce(() =>
        Promise.resolve(templateDownloadResponse("preferred-current.docx")),
      )
      .mockImplementationOnce(() =>
        Promise.resolve(templateDownloadResponse("preferred-v2.docx")),
      ),
  });
  const createObjectURL = vi
    .spyOn(URL, "createObjectURL")
    .mockReturnValueOnce("blob:current")
    .mockReturnValueOnce("blob:historical");
  const downloadedFilenames: string[] = [];
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(function (this: HTMLAnchorElement) {
      downloadedFilenames.push(this.download);
    });
  render(
    <TemplatesWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  await screen.findByText("Fallback body");
  const preferredItem = screen.getByText("Preferred template").closest("li")!;
  fireEvent.click(
    within(preferredItem).getByRole("button", {
      name: "Download current DOCX",
    }),
  );
  await waitFor(() => expect(api.templateContent).toHaveBeenCalledOnce());
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(downloadedFilenames[0]).toBe("preferred-current.docx");

  fireEvent.click(
    within(preferredItem).getByRole("button", { name: "Manage" }),
  );
  await screen.findByRole("heading", { name: "Manage Preferred template" });
  fireEvent.click(screen.getByRole("button", { name: "Download version 2" }));
  await waitFor(() => expect(api.templateContent).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(click).toHaveBeenCalledTimes(2));
  expect(api.templateContent.mock.calls).toEqual([
    [preferred.id, undefined, expect.any(AbortSignal)],
    [preferred.id, version.id, expect.any(AbortSignal)],
  ]);
  expect(downloadedFilenames[1]).toBe("preferred-v2.docx");
  expect(createObjectURL).toHaveBeenCalledTimes(2);
  vi.restoreAllMocks();
});

test("an authoritative 401 during a rendered template download expires the session", async () => {
  const expire = vi.fn();
  const api = templateApi({
    templateContent: vi
      .fn()
      .mockRejectedValue(
        new ApiError(401, "AUTHENTICATION_REQUIRED", "unsafe"),
      ),
  });
  const createObjectURL = vi.spyOn(URL, "createObjectURL");
  render(
    <TemplatesWorkspace
      api={api as unknown as AdministrationApi}
      expire={expire}
      user={admin}
    />,
  );
  await screen.findByText("Fallback body");
  fireEvent.click(
    within(screen.getByText("Preferred template").closest("li")!).getByRole(
      "button",
      { name: "Download current DOCX" },
    ),
  );
  expect(
    await screen.findByText("Your session ended. Please sign in again."),
  ).toBeVisible();
  expect(expire).toHaveBeenCalledOnce();
  expect(api.templateContent).toHaveBeenCalledOnce();
  expect(createObjectURL).not.toHaveBeenCalled();
  vi.restoreAllMocks();
});

test("template preference, fallback, restore, confirmation, stale writes, and session loss are bounded", async () => {
  const expire = vi.fn();
  const api = templateApi({
    updateMetadata: vi
      .fn()
      .mockRejectedValue(new ApiError(412, "STALE", "unsafe")),
  });
  render(
    <TemplatesWorkspace
      api={api as unknown as AdministrationApi}
      expire={expire}
      user={admin}
    />,
  );
  await screen.findByText("Fallback body");
  fireEvent.click(
    screen.getByRole("button", { name: "Clear preferred template" }),
  );
  expect(await screen.findByText("Preferred template cleared.")).toBeVisible();
  const preferredItem = screen.getByText("Preferred template").closest("li")!;
  fireEvent.click(
    within(preferredItem).getByRole("button", { name: "Set system fallback" }),
  );
  expect(await screen.findByText("System fallback updated.")).toBeVisible();
  fireEvent.click(
    within(screen.getByText("Preferred template").closest("li")!).getByRole(
      "button",
      { name: "Manage" },
    ),
  );
  await screen.findByRole("heading", { name: "Manage Preferred template" });
  fireEvent.change(screen.getByRole("textbox", { name: "Template name" }), {
    target: { value: "Stale edit" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save details" }));
  expect(await screen.findByText(/changed on the server/)).toBeVisible();
  expect(api.template).toHaveBeenCalledTimes(2);
  api.updateMetadata.mockResolvedValue({ data: preferred, etag: '"next"' });
  fireEvent.click(screen.getByRole("button", { name: "Restore version 1" }));
  expect(
    await screen.findByText("Version 1 restored as a new version."),
  ).toBeVisible();
  fireEvent.click(
    within(screen.getByText("Preferred template").closest("li")!).getByRole(
      "button",
      { name: "Manage" },
    ),
  );
  await screen.findByRole("heading", { name: "Manage Preferred template" });
  fireEvent.click(screen.getByRole("button", { name: "Archive template" }));
  expect(
    screen.getByRole("dialog", { name: "Archive template?" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Archive template" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  expect(await screen.findByText("Template archived.")).toBeVisible();
  expect(api.archive).toHaveBeenCalledWith(
    preferred.id,
    '"template-etag"',
    expect.any(AbortSignal),
  );
  fireEvent.click(
    within(screen.getByText("Archived style").closest("li")!).getByRole(
      "button",
      { name: "Manage" },
    ),
  );
  await screen.findByRole("heading", { name: "Manage Archived style" });
  fireEvent.click(
    screen.getByRole("button", { name: "Delete template permanently" }),
  );
  expect(
    screen.getByRole("dialog", { name: "Delete template permanently?" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  expect(await screen.findByText("Template deleted.")).toBeVisible();
  expect(api.delete).toHaveBeenCalled();
});

test("template mutation failures keep server details bounded", async () => {
  const api = templateApi({
    setPreferred: vi
      .fn()
      .mockRejectedValue(new ApiError(409, "CONFLICT", "unsafe")),
  });
  render(
    <TemplatesWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={alice}
    />,
  );
  await screen.findByText("Fallback body");
  fireEvent.click(
    within(screen.getByText("Fallback template").closest("li")!).getByRole(
      "button",
      { name: "Make preferred" },
    ),
  );
  expect(
    await screen.findByText("The requested value already exists."),
  ).toBeVisible();
  expect(screen.queryByText("unsafe")).toBeNull();
});

test("guarded template deletion refreshes after 412 and requires an explicit retry", async () => {
  const deleteTemplate = vi
    .fn()
    .mockRejectedValueOnce(
      new ApiError(412, "TEMPLATE_PRECONDITION_FAILED", "unsafe"),
    )
    .mockResolvedValueOnce(undefined);
  const api = templateApi({ delete: deleteTemplate });
  render(
    <TemplatesWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  await screen.findByText("Archived style");
  fireEvent.click(
    within(screen.getByText("Archived style").closest("li")!).getByRole(
      "button",
      { name: "Manage" },
    ),
  );
  await screen.findByRole("heading", { name: "Manage Archived style" });
  fireEvent.click(
    screen.getByRole("button", { name: "Delete template permanently" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

  expect(await screen.findByText(/changed on the server/)).toBeVisible();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(deleteTemplate).toHaveBeenCalledOnce();
  expect(api.template).toHaveBeenCalledTimes(2);

  fireEvent.click(
    screen.getByRole("button", { name: "Delete template permanently" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  expect(await screen.findByText("Template deleted.")).toBeVisible();
  expect(deleteTemplate).toHaveBeenCalledTimes(2);
});

test("template requests publish no late state after navigation", async () => {
  let resolveLibrary!: (value: unknown) => void;
  let resolveContext!: (value: unknown) => void;
  const loadingApi = templateApi({
    allTemplates: vi
      .fn()
      .mockImplementation(
        () => new Promise((resolve) => (resolveLibrary = resolve)),
      ),
    templateContext: vi
      .fn()
      .mockImplementation(
        () => new Promise((resolve) => (resolveContext = resolve)),
      ),
  });
  const loadingView = render(
    <TemplatesWorkspace
      api={loadingApi as unknown as AdministrationApi}
      expire={vi.fn()}
      user={alice}
    />,
  );
  await waitFor(() => expect(loadingApi.allTemplates).toHaveBeenCalledOnce());
  loadingView.unmount();
  await act(async () => {
    resolveLibrary([fallback]);
    resolveContext({
      preferred_template_id: null,
      system_fallback_template_id: fallback.id,
      template_max_archive_bytes: 1024,
    });
    await Promise.resolve();
  });

  let resolveMutation!: (value: unknown) => void;
  const mutationApi = templateApi({
    setPreferred: vi
      .fn()
      .mockImplementation(
        () => new Promise((resolve) => (resolveMutation = resolve)),
      ),
  });
  const mutationView = render(
    <TemplatesWorkspace
      api={mutationApi as unknown as AdministrationApi}
      expire={vi.fn()}
      user={alice}
    />,
  );
  await screen.findByText("Fallback body");
  fireEvent.click(
    within(screen.getByText("Fallback template").closest("li")!).getByRole(
      "button",
      { name: "Make preferred" },
    ),
  );
  mutationView.unmount();
  await act(async () => {
    resolveMutation(undefined);
    await Promise.resolve();
  });
  expect(mutationApi.allTemplates).toHaveBeenCalledOnce();
});

test("a parent rerender does not abort or restart template loading", async () => {
  let resolveLibrary!: (value: unknown) => void;
  let resolveContext!: (value: unknown) => void;
  let librarySignal: AbortSignal | undefined;
  let contextSignal: AbortSignal | undefined;
  const api = templateApi({
    allTemplates: vi
      .fn()
      .mockImplementation((_filters: unknown, signal: AbortSignal) => {
        librarySignal = signal;
        return new Promise((resolve) => (resolveLibrary = resolve));
      }),
    setPreferred: vi
      .fn()
      .mockRejectedValue(
        new ApiError(401, "AUTHENTICATION_REQUIRED", "unsafe"),
      ),
    templateContext: vi.fn().mockImplementation((signal: AbortSignal) => {
      contextSignal = signal;
      return new Promise((resolve) => (resolveContext = resolve));
    }),
  });
  const firstExpire = vi.fn();
  const latestExpire = vi.fn();
  const view = render(
    <TemplatesWorkspace
      api={api as unknown as AdministrationApi}
      expire={firstExpire}
      user={alice}
    />,
  );
  await waitFor(() => expect(api.allTemplates).toHaveBeenCalledOnce());
  view.rerender(
    <TemplatesWorkspace
      api={api as unknown as AdministrationApi}
      expire={latestExpire}
      user={alice}
    />,
  );
  expect(api.allTemplates).toHaveBeenCalledOnce();
  expect(api.templateContext).toHaveBeenCalledOnce();
  expect(librarySignal?.aborted).toBe(false);
  expect(contextSignal?.aborted).toBe(false);
  await act(async () => {
    resolveLibrary([fallback]);
    resolveContext({
      preferred_template_id: null,
      system_fallback_template_id: fallback.id,
      template_max_archive_bytes: 1024,
    });
  });
  const item = (await screen.findByText("Fallback body")).closest("li")!;
  fireEvent.click(within(item).getByRole("button", { name: "Make preferred" }));
  await waitFor(() => expect(latestExpire).toHaveBeenCalledOnce());
  expect(firstExpire).not.toHaveBeenCalled();
});

function userApi(overrides: Record<string, unknown> = {}) {
  return {
    createUser: vi.fn().mockResolvedValue(alice),
    resetPassword: vi.fn().mockResolvedValue(undefined),
    setActive: vi.fn().mockResolvedValue(alice),
    setPasswordChangeRequired: vi.fn().mockResolvedValue(alice),
    users: vi.fn().mockResolvedValue([admin, alice]),
    ...overrides,
  };
}

test("ordinary users receive no account data or controls", async () => {
  const api = userApi();
  render(
    <UsersWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={alice}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Administrator access is required.",
  );
  expect(api.users).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: /Create account/ })).toBeNull();
});

test("administrator account workflows filter, guard duplicates, and reload", async () => {
  let finish!: (value: unknown) => void;
  let finishReload!: (value: unknown) => void;
  const api = userApi({
    createUser: vi
      .fn()
      .mockImplementation(() => new Promise((resolve) => (finish = resolve))),
    users: vi
      .fn()
      .mockResolvedValueOnce([admin, alice])
      .mockImplementationOnce(
        () => new Promise((resolve) => (finishReload = resolve)),
      ),
  });
  render(
    <UsersWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  expect(await screen.findByRole("heading", { name: "Alice" })).toBeVisible();
  fireEvent.change(
    screen.getByRole("textbox", { name: "Search by username" }),
    { target: { value: "Ali" } },
  );
  expect(screen.queryByRole("heading", { name: "Admin" })).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Username" }), {
    target: { value: "Bob" },
  });
  fireEvent.change(screen.getByLabelText("Temporary password"), {
    target: { value: "temporary" },
  });
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: /Require password change at next sign-in/,
    }),
  );
  const create = screen.getByRole("button", { name: "Create account" });
  fireEvent.click(create);
  fireEvent.click(create);
  expect(api.createUser).toHaveBeenCalledOnce();
  act(() => finish(alice));
  await waitFor(() => expect(api.users).toHaveBeenCalledTimes(2));
  expect(screen.queryByText("Account created.")).toBeNull();
  expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  fireEvent.submit(create.closest("form")!);
  expect(api.createUser).toHaveBeenCalledOnce();
  expect(screen.getByRole("textbox", { name: "Username" })).toHaveValue("Bob");
  expect(screen.getByRole("textbox", { name: "Username" })).toBeDisabled();
  expect(screen.getByLabelText("Temporary password")).toHaveValue("temporary");
  expect(screen.getByLabelText("Temporary password")).toBeDisabled();
  expect(
    screen.getByRole("checkbox", {
      name: /Require password change at next sign-in/,
    }),
  ).toBeChecked();
  expect(
    screen.getByRole("checkbox", {
      name: /Require password change at next sign-in/,
    }),
  ).toBeDisabled();
  act(() => finishReload([admin, alice]));
  expect(await screen.findByText("Account created.")).toBeVisible();
  expect(screen.getByRole("textbox", { name: "Username" })).toHaveValue("");
  expect(screen.getByLabelText("Temporary password")).toHaveValue("");
  expect(
    screen.getByRole("checkbox", {
      name: /Require password change at next sign-in/,
    }),
  ).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Create account" })).toBeEnabled();
});

test("failed account creation preserves fields for correction", async () => {
  const api = userApi({
    createUser: vi
      .fn()
      .mockRejectedValue(new ApiError(409, "CONFLICT", "unsafe")),
  });
  render(
    <UsersWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  await screen.findByRole("heading", { name: "Alice" });
  const username = screen.getByRole("textbox", { name: "Username" });
  const password = screen.getByLabelText("Temporary password");
  fireEvent.change(username, { target: { value: "Existing" } });
  fireEvent.change(password, { target: { value: "still-present" } });
  fireEvent.click(screen.getByRole("button", { name: "Create account" }));
  expect(
    await screen.findByText("The requested value already exists."),
  ).toBeVisible();
  expect(username).toHaveValue("Existing");
  expect(password).toHaveValue("still-present");
});

test("administrator can change status, renewal, and reset password with revoked-session handling", async () => {
  const expire = vi.fn();
  const api = userApi();
  render(
    <UsersWorkspace
      api={api as unknown as AdministrationApi}
      expire={expire}
      user={admin}
    />,
  );
  await screen.findByRole("heading", { name: "Alice" });
  fireEvent.click(screen.getByRole("button", { name: "Deactivate Alice" }));
  expect(
    await screen.findByText(/deactivated and sessions revoked/),
  ).toBeVisible();
  fireEvent.click(
    screen.getByRole("button", { name: "Require password renewal for Alice" }),
  );
  expect(
    await screen.findByText(/renewal required and sessions revoked/),
  ).toBeVisible();
  fireEvent.click(
    screen.getByRole("button", { name: "Reset password for Alice" }),
  );
  const dialog = screen.getByRole("dialog", {
    name: "Reset password for Alice",
  });
  fireEvent.change(within(dialog).getByLabelText("New temporary password"), {
    target: { value: "replacement" },
  });
  fireEvent.click(within(dialog).getByRole("checkbox"));
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Reset password" }),
  );
  expect(
    await screen.findByText(/Password reset and sessions revoked/),
  ).toBeVisible();

  api.users.mockRejectedValueOnce(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "unsafe"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Deactivate Alice" }));
  await waitFor(() => expect(expire).toHaveBeenCalled());
});

test("administrator handles inactive accounts, renewal cancellation, empty searches, and safe failures", async () => {
  const inactive = {
    ...alice,
    active: false,
    password_change_required: true,
  };
  const api = userApi({ users: vi.fn().mockResolvedValue([admin, inactive]) });
  render(
    <UsersWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  await screen.findByRole("button", { name: "Reactivate Alice" });
  fireEvent.click(screen.getByRole("button", { name: "Reactivate Alice" }));
  expect(await screen.findByText("Account reactivated.")).toBeVisible();
  fireEvent.click(
    screen.getByRole("button", { name: "Cancel password renewal for Alice" }),
  );
  expect(
    await screen.findByText("Password renewal cancelled and sessions revoked."),
  ).toBeVisible();
  fireEvent.change(
    screen.getByRole("textbox", { name: "Search by username" }),
    {
      target: { value: "nobody" },
    },
  );
  expect(screen.getByText("No accounts match this search.")).toBeVisible();
});

test("account load errors are bounded and do not expose server details", async () => {
  const api = userApi({
    users: vi.fn().mockRejectedValue(new TypeError("private database detail")),
  });
  render(
    <UsersWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  expect(
    await screen.findByText("Accounts could not be loaded. Try again."),
  ).toBeVisible();
  expect(screen.queryByText(/private database detail/)).toBeNull();
});

test("account requests publish no late state after navigation", async () => {
  let resolveUsers!: (value: unknown) => void;
  const api = userApi({
    users: vi
      .fn()
      .mockImplementation(
        () => new Promise((resolve) => (resolveUsers = resolve)),
      ),
  });
  const view = render(
    <UsersWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  await waitFor(() => expect(api.users).toHaveBeenCalledOnce());
  view.unmount();
  await act(async () => {
    resolveUsers([alice]);
    await Promise.resolve();
  });
  expect(api.users).toHaveBeenCalledOnce();
});

test("a parent rerender does not abort or restart account loading", async () => {
  let resolveUsers!: (value: unknown) => void;
  let usersSignal: AbortSignal | undefined;
  const api = userApi({
    users: vi.fn().mockImplementation((signal: AbortSignal) => {
      usersSignal = signal;
      return new Promise((resolve) => (resolveUsers = resolve));
    }),
  });
  const view = render(
    <UsersWorkspace
      api={api as unknown as AdministrationApi}
      expire={() => undefined}
      user={admin}
    />,
  );
  await waitFor(() => expect(api.users).toHaveBeenCalledOnce());
  view.rerender(
    <UsersWorkspace
      api={api as unknown as AdministrationApi}
      expire={() => undefined}
      user={admin}
    />,
  );
  expect(api.users).toHaveBeenCalledOnce();
  expect(usersSignal?.aborted).toBe(false);
  await act(async () => resolveUsers([alice]));
  expect(await screen.findByRole("heading", { name: "Alice" })).toBeVisible();
  expect(api.users).toHaveBeenCalledOnce();
  expect(usersSignal?.aborted).toBe(false);
});
