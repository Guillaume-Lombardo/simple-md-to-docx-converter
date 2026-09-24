import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "../src/api/transport";
import type { AdministrationApi } from "../src/admin/api";
import { TemplatesWorkspace } from "../src/admin/templates";

const owner = {
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};
const admin = {
  ...owner,
  id: "00000000-0000-4000-8000-000000000099",
  role: "admin" as const,
  username: "Admin",
};
const owned = {
  current_version_id: "10000000-0000-4000-8000-000000000001",
  description: "Owner document",
  id: "20000000-0000-4000-8000-000000000001",
  kind: "docx" as const,
  name: "Owner template",
  owner_id: owner.id,
  owner_username: owner.username,
  revision: 1,
  status: "active" as const,
};
const other = {
  ...owned,
  current_version_id: "10000000-0000-4000-8000-000000000002",
  description: "Admin document",
  id: "20000000-0000-4000-8000-000000000002",
  name: "Admin template",
  owner_id: admin.id,
  owner_username: admin.username,
};
const slides = {
  ...other,
  current_version_id: "10000000-0000-4000-8000-000000000003",
  id: "20000000-0000-4000-8000-000000000003",
  kind: "pptx" as const,
  name: "Admin slides",
};
const version = {
  created_at: "2026-09-02T10:00:00Z",
  created_by: admin.id,
  declared_fonts: ["Carlito"],
  id: other.current_version_id,
  number: 2,
  resolved_fonts: [["Carlito", "Carlito"]] as [string, string][],
  restored_from_version_id: null,
  sha256: "a".repeat(64),
  size: 120,
  template_id: other.id,
  validation_trace: ["static_ooxml"],
};

function api(overrides: Record<string, unknown> = {}) {
  return {
    allTemplates: vi.fn().mockResolvedValue([owned, other, slides]),
    archive: vi.fn(),
    clearPreferred: vi.fn(),
    create: vi.fn(),
    delete: vi.fn(),
    replace: vi.fn(),
    restore: vi.fn(),
    setFallback: vi.fn(),
    setPreferred: vi.fn().mockResolvedValue(undefined),
    template: vi.fn().mockResolvedValue({ data: other, etag: '"template-1"' }),
    templateContent: vi.fn(),
    templateContext: vi.fn().mockResolvedValue({
      preferred_template_id: other.id,
      system_fallback_template_id: owned.id,
      template_max_archive_bytes: 1024,
    }),
    updateMetadata: vi.fn(),
    versions: vi.fn().mockResolvedValue([version]),
    ...overrides,
  };
}

async function manageOther() {
  const item = (await screen.findByText("Admin template")).closest("li")!;
  fireEvent.click(within(item).getByRole("button", { name: "Manage" }));
  await screen.findByRole("heading", { name: "Manage Admin template" });
}

test("non-owners can see approved templates but cannot manage them; PowerPoint cannot become a Word default", async () => {
  const service = api();
  const view = render(
    <TemplatesWorkspace
      api={service as unknown as AdministrationApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  const ownItem = (await screen.findByText("Owner template")).closest("li")!;
  const otherItem = screen.getByText("Admin template").closest("li")!;
  const slidesItem = screen.getByText("Admin slides").closest("li")!;
  expect(within(ownItem).getByRole("button", { name: "Manage" })).toBeVisible();
  expect(
    within(otherItem).queryByRole("button", { name: "Manage" }),
  ).toBeNull();
  expect(
    within(slidesItem).queryByRole("button", { name: "Manage" }),
  ).toBeNull();
  expect(
    within(slidesItem).getByRole("button", { name: "Download current PPTX" }),
  ).toBeVisible();
  expect(
    within(slidesItem).queryByRole("button", { name: "Make preferred" }),
  ).toBeNull();
  expect(
    within(slidesItem).queryByRole("button", { name: "Set system fallback" }),
  ).toBeNull();
  expect(service.template).not.toHaveBeenCalled();
  view.unmount();

  render(
    <TemplatesWorkspace
      api={service as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  const adminItem = (await screen.findByText("Admin template")).closest("li")!;
  expect(
    within(adminItem).getByRole("button", { name: "Manage" }),
  ).toBeVisible();
  expect(
    within(screen.getByText("Admin slides").closest("li")!).getByRole(
      "button",
      {
        name: "Manage",
      },
    ),
  ).toBeVisible();
});

test("a 412 refresh without an ETag closes stale management instead of offering a stale retry", async () => {
  const service = api({
    template: vi
      .fn()
      .mockResolvedValueOnce({ data: other, etag: '"template-1"' })
      .mockResolvedValueOnce({ data: { ...other, name: "Changed on server" } }),
    updateMetadata: vi
      .fn()
      .mockRejectedValue(new ApiError(412, "PRECONDITION_FAILED", "stale")),
  });
  const expire = vi.fn();
  render(
    <TemplatesWorkspace
      api={service as unknown as AdministrationApi}
      expire={expire}
      user={admin}
    />,
  );
  await manageOther();
  fireEvent.click(screen.getByRole("button", { name: "Save details" }));
  expect(
    await screen.findByText(/did not provide the template revision/),
  ).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: "Manage Admin template" }),
  ).toBeNull();
  expect(service.template).toHaveBeenCalledTimes(2);
  expect(service.updateMetadata).toHaveBeenCalledWith(
    other.id,
    '"template-1"',
    other.name,
    other.description,
    expect.any(AbortSignal),
  );
  expect(expire).not.toHaveBeenCalled();
});

test("a successful preference write followed by failed reload does not announce success", async () => {
  const service = api({
    allTemplates: vi
      .fn()
      .mockResolvedValueOnce([owned, other, slides])
      .mockRejectedValueOnce(new Error("private reload detail")),
  });
  render(
    <TemplatesWorkspace
      api={service as unknown as AdministrationApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  const item = (await screen.findByText("Owner template")).closest("li")!;
  fireEvent.click(within(item).getByRole("button", { name: "Make preferred" }));
  expect(
    await screen.findByText("Templates could not be loaded. Try again."),
  ).toBeVisible();
  expect(screen.queryByText("Preferred template updated.")).toBeNull();
  expect(screen.queryByText("private reload detail")).toBeNull();
  expect(service.setPreferred).toHaveBeenCalledWith(
    owned.id,
    expect.any(AbortSignal),
  );
  expect(screen.getByRole("button", { name: "Make preferred" })).toBeEnabled();
});

test("download denial retains access, while a later 401 expires the session", async () => {
  const service = api({
    templateContent: vi
      .fn()
      .mockRejectedValueOnce(new ApiError(403, "FORBIDDEN", "private detail"))
      .mockRejectedValueOnce(
        new ApiError(401, "SESSION_EXPIRED", "private detail"),
      ),
  });
  const expire = vi.fn();
  render(
    <TemplatesWorkspace
      api={service as unknown as AdministrationApi}
      expire={expire}
      user={owner}
    />,
  );
  const item = (await screen.findByText("Owner template")).closest("li")!;
  fireEvent.click(
    within(item).getByRole("button", { name: "Download current DOCX" }),
  );
  expect(
    await screen.findByText("You are not allowed to perform this action."),
  ).toBeVisible();
  expect(expire).not.toHaveBeenCalled();
  expect(screen.getByText("Owner template")).toBeVisible();
  fireEvent.click(
    within(item).getByRole("button", { name: "Download current DOCX" }),
  );
  expect(
    await screen.findByText("Your session ended. Please sign in again."),
  ).toBeVisible();
  expect(expire).toHaveBeenCalledOnce();
  expect(screen.queryByText("private detail")).toBeNull();
});

test("a 412 refresh that loses authorization expires the session and closes management", async () => {
  const service = api({
    template: vi
      .fn()
      .mockResolvedValueOnce({ data: other, etag: '"template-1"' })
      .mockRejectedValueOnce(
        new ApiError(401, "SESSION_EXPIRED", "private refresh detail"),
      ),
    updateMetadata: vi
      .fn()
      .mockRejectedValue(new ApiError(412, "PRECONDITION_FAILED", "stale")),
  });
  const expire = vi.fn();
  render(
    <TemplatesWorkspace
      api={service as unknown as AdministrationApi}
      expire={expire}
      user={admin}
    />,
  );
  await manageOther();
  fireEvent.click(screen.getByRole("button", { name: "Save details" }));
  expect(
    await screen.findByText("Your session ended. Please sign in again."),
  ).toBeVisible();
  expect(expire).toHaveBeenCalledOnce();
  expect(
    screen.queryByRole("heading", { name: "Manage Admin template" }),
  ).toBeNull();
  expect(screen.queryByText("private refresh detail")).toBeNull();
  expect(service.template).toHaveBeenCalledTimes(2);
});

test("PowerPoint creation rejects a Word file and submits only the selected format", async () => {
  const service = api({ create: vi.fn().mockResolvedValue({ data: slides }) });
  const interaction = userEvent.setup({ applyAccept: false });
  render(
    <TemplatesWorkspace
      api={service as unknown as AdministrationApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  await screen.findByText("Owner template");
  fireEvent.change(screen.getByLabelText("Template format"), {
    target: { value: "pptx" },
  });
  const fileInput = screen.getByLabelText("PPTX file");
  const createForm = screen
    .getByRole("button", { name: "Create template" })
    .closest("form")!;
  await interaction.upload(fileInput, new File(["word"], "wrong.docx"));
  fireEvent.submit(createForm);
  expect(
    screen.getByText("Choose a file with the .pptx extension."),
  ).toBeVisible();
  expect(service.create).not.toHaveBeenCalled();

  await interaction.upload(fileInput, new File(["slides"], "deck.pptx"));
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "New slides" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Description" }), {
    target: { value: "Deck body" },
  });
  fireEvent.submit(createForm);
  await waitFor(() => expect(service.create).toHaveBeenCalledOnce());
  expect(service.create.mock.calls[0]![0]).toMatchObject({
    kind: "pptx",
    name: "New slides",
    description: "Deck body",
  });
  expect(service.create.mock.calls[0]![0].content.name).toBe("deck.pptx");
});
