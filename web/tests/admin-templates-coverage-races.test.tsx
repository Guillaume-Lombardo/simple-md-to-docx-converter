import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import type { AdministrationApi } from "../src/admin/api";
import { TemplatesWorkspace } from "../src/admin/templates";
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
  id: "00000000-0000-4000-8000-000000000099",
  role: "admin" as const,
  username: "Admin",
};
const ownedWord = {
  current_version_id: "10000000-0000-4000-8000-000000000001",
  description: "Annual report body",
  id: "20000000-0000-4000-8000-000000000001",
  name: "Owned classic",
  owner_id: alice.id,
  owner_username: "Alice",
  revision: 1,
  status: "active" as const,
};
const sharedSlides = {
  ...ownedWord,
  id: "20000000-0000-4000-8000-000000000002",
  current_version_id: "10000000-0000-4000-8000-000000000002",
  name: "Shared slides",
  description: "Quarterly charts",
  kind: "pptx",
  owner_id: admin.id,
  owner_username: "Admin",
};
const archivedSlides = {
  ...sharedSlides,
  id: "20000000-0000-4000-8000-000000000003",
  name: "Old slides",
  description: "Historic charts",
  owner_id: alice.id,
  owner_username: "Alice",
  status: "archived" as const,
};
const currentVersion = {
  created_at: "2026-09-23T12:00:00Z",
  created_by: alice.id,
  declared_fonts: ["Carlito"],
  id: sharedSlides.current_version_id,
  number: 2,
  resolved_fonts: [["Carlito", "Carlito"]] as [string, string][],
  restored_from_version_id: null,
  sha256: "a".repeat(64),
  size: 120,
  template_id: sharedSlides.id,
  validation_trace: ["static_ooxml"],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function templateApi(overrides: Record<string, unknown> = {}) {
  return {
    allTemplates: vi
      .fn()
      .mockResolvedValue([ownedWord, sharedSlides, archivedSlides]),
    archive: vi.fn().mockResolvedValue(undefined),
    clearPreferred: vi.fn().mockResolvedValue(undefined),
    create: vi.fn().mockResolvedValue(sharedSlides),
    delete: vi.fn().mockResolvedValue(undefined),
    replace: vi
      .fn()
      .mockResolvedValue({ data: currentVersion, etag: '"next"' }),
    restore: vi
      .fn()
      .mockResolvedValue({ data: currentVersion, etag: '"next"' }),
    setFallback: vi.fn().mockResolvedValue(undefined),
    setPreferred: vi.fn().mockResolvedValue(undefined),
    template: vi.fn().mockImplementation(async (id: string) => ({
      data: id === sharedSlides.id ? sharedSlides : archivedSlides,
      etag: '"template-etag"',
    })),
    templateContent: vi.fn(),
    templateContext: vi.fn().mockResolvedValue({
      preferred_template_id: null,
      system_fallback_template_id: null,
      template_max_archive_bytes: 64,
    }),
    updateMetadata: vi
      .fn()
      .mockResolvedValue({ data: sharedSlides, etag: '"next"' }),
    versions: vi.fn().mockResolvedValue([currentVersion]),
    ...overrides,
  };
}

function show(
  api: ReturnType<typeof templateApi>,
  user: typeof alice | typeof admin = alice,
  expire = vi.fn(),
) {
  const view = render(
    <TemplatesWorkspace
      api={api as unknown as AdministrationApi}
      expire={expire}
      user={user}
    />,
  );
  return { expire, view };
}

test("format, owner, status, and trimmed description filters narrow the visible library", async () => {
  const api = templateApi();
  show(api);
  expect(
    await screen.findByRole("heading", { name: "Owned classic" }),
  ).toBeVisible();
  const kind = screen.getByRole("combobox", { name: "Filter template format" });
  fireEvent.change(kind, { target: { value: "docx" } });
  expect(screen.getByRole("heading", { name: "Owned classic" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Shared slides" })).toBeNull();
  fireEvent.change(kind, { target: { value: "pptx" } });
  expect(screen.queryByRole("heading", { name: "Owned classic" })).toBeNull();
  expect(screen.getByRole("heading", { name: "Shared slides" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Old slides" })).toBeVisible();
  fireEvent.click(screen.getByRole("checkbox", { name: "My templates" }));
  expect(screen.queryByRole("heading", { name: "Shared slides" })).toBeNull();
  fireEvent.change(screen.getByRole("combobox", { name: "Status" }), {
    target: { value: "active" },
  });
  expect(screen.getByText("No templates match these filters.")).toBeVisible();
  fireEvent.change(screen.getByRole("combobox", { name: "Status" }), {
    target: { value: "archived" },
  });
  expect(screen.getByRole("heading", { name: "Old slides" })).toBeVisible();
  fireEvent.change(screen.getByRole("textbox", { name: /Search name/ }), {
    target: { value: "  QUARTERLY  " },
  });
  expect(screen.getByText("No templates match these filters.")).toBeVisible();
  fireEvent.click(screen.getByRole("checkbox", { name: "My templates" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Status" }), {
    target: { value: "active" },
  });
  expect(screen.getByRole("heading", { name: "Shared slides" })).toBeVisible();
  expect(api.allTemplates).toHaveBeenCalledOnce();
});

test("PowerPoint creation rejects empty, wrong-kind, and oversized files before accepting a selected deck", async () => {
  const api = templateApi();
  show(api);
  await screen.findByRole("heading", { name: "Shared slides" });
  fireEvent.change(screen.getByRole("combobox", { name: "Template format" }), {
    target: { value: "pptx" },
  });
  const form = screen
    .getByRole("button", { name: "Create template" })
    .closest("form")!;
  fireEvent.submit(form);
  expect(screen.getByRole("alert")).toHaveTextContent("non-empty PPTX");
  const fileInput = screen.getByLabelText("PPTX file");
  fireEvent.change(fileInput, {
    target: { files: [new File(["wrong"], "report.docx")] },
  });
  fireEvent.submit(form);
  expect(screen.getByRole("alert")).toHaveTextContent(".pptx extension");
  fireEvent.change(fileInput, {
    target: { files: [new File([new Uint8Array(65)], "large.pptx")] },
  });
  fireEvent.submit(form);
  expect(screen.getByRole("alert")).toHaveTextContent("64 byte limit");
  const accepted = new File(["deck"], "review.PPTX");
  fireEvent.change(fileInput, { target: { files: [accepted] } });
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "Reviewed deck" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Description" }), {
    target: { value: "Board summary" },
  });
  fireEvent.submit(form);
  await waitFor(() => expect(api.create).toHaveBeenCalledOnce());
  expect(api.create.mock.calls[0]?.[0]).toMatchObject({
    content: accepted,
    kind: "pptx",
    name: "Reviewed deck",
    description: "Board summary",
  });
  expect(await screen.findByText("Template created.")).toBeVisible();
});

test("a context 401 expires the session, while a 403 preserves safe filtering controls", async () => {
  const expired = templateApi({
    templateContext: vi
      .fn()
      .mockRejectedValue(
        new ApiError(401, "AUTHENTICATION_REQUIRED", "Private context"),
      ),
  });
  const first = show(expired);
  await waitFor(() => expect(first.expire).toHaveBeenCalledOnce());
  expect(screen.queryByText("Private context")).toBeNull();
  first.view.unmount();

  const forbidden = templateApi({
    templateContext: vi
      .fn()
      .mockRejectedValue(
        new ApiError(403, "FORBIDDEN", "Private permission detail"),
      ),
  });
  const second = show(forbidden);
  expect(
    await screen.findByText("You are not allowed to perform this action."),
  ).toBeVisible();
  expect(second.expire).not.toHaveBeenCalled();
  expect(
    screen.getByRole("combobox", { name: "Filter template format" }),
  ).toBeVisible();
  expect(screen.queryByText("Private permission detail")).toBeNull();
});

test("a missing revision during 412 recovery closes stale management and allows explicit reload", async () => {
  const latest = { ...sharedSlides, name: "Authoritative slides" };
  const api = templateApi({
    template: vi
      .fn()
      .mockResolvedValueOnce({ data: sharedSlides, etag: '"template-etag"' })
      .mockResolvedValueOnce({ data: latest })
      .mockResolvedValueOnce({ data: latest, etag: '"new-etag"' }),
    updateMetadata: vi
      .fn()
      .mockRejectedValueOnce(
        new ApiError(412, "PRECONDITION_FAILED", "Private stale body"),
      ),
  });
  show(api, admin);
  const item = (
    await screen.findByRole("heading", { name: "Shared slides" })
  ).closest("li")!;
  fireEvent.click(within(item).getByRole("button", { name: "Manage" }));
  await screen.findByRole("heading", { name: "Manage Shared slides" });
  fireEvent.change(screen.getByRole("textbox", { name: "Template name" }), {
    target: { value: "My update" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save details" }));
  expect(
    await screen.findByText(/did not provide the template revision/),
  ).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: "Manage Shared slides" }),
  ).toBeNull();
  expect(screen.queryByText("Private stale body")).toBeNull();
  const currentItem = screen
    .getByRole("heading", { name: "Shared slides" })
    .closest("li")!;
  fireEvent.click(within(currentItem).getByRole("button", { name: "Manage" }));
  await waitFor(() => expect(api.template).toHaveBeenCalledTimes(3));
  expect(
    await screen.findByRole("heading", { name: "Manage Authoritative slides" }),
  ).toBeVisible();
  expect(api.updateMetadata).toHaveBeenCalledOnce();
});

test("a refreshed template owned by another account exposes no management controls", async () => {
  const api = templateApi({
    allTemplates: vi
      .fn()
      .mockResolvedValue([{ ...sharedSlides, owner_id: alice.id }]),
    template: vi.fn().mockResolvedValue({
      data: sharedSlides,
      etag: '"authoritative"',
    }),
  });
  show(api, alice);
  const item = (
    await screen.findByRole("heading", { name: "Shared slides" })
  ).closest("li")!;
  fireEvent.click(within(item).getByRole("button", { name: "Manage" }));
  await waitFor(() => expect(api.template).toHaveBeenCalledOnce());
  expect(
    screen.queryByRole("heading", { name: "Manage Shared slides" }),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "Save details" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Replace content" })).toBeNull();
  expect(api.updateMetadata).not.toHaveBeenCalled();
});

test("PowerPoint replacement uses the managed ETag and rejects invalid files before upload", async () => {
  const api = templateApi();
  show(api, admin);
  const item = (
    await screen.findByRole("heading", { name: "Shared slides" })
  ).closest("li")!;
  fireEvent.click(within(item).getByRole("button", { name: "Manage" }));
  const replace = await screen.findByRole("button", {
    name: "Replace content",
  });
  const form = replace.closest("form")!;
  fireEvent.submit(form);
  expect(screen.getByRole("alert")).toHaveTextContent("non-empty PPTX");
  const input = screen.getByLabelText("Replacement PPTX");
  fireEvent.change(input, {
    target: { files: [new File(["wrong"], "replacement.docx")] },
  });
  fireEvent.submit(form);
  expect(screen.getByRole("alert")).toHaveTextContent(".pptx extension");
  fireEvent.change(input, {
    target: { files: [new File([new Uint8Array(65)], "large.pptx")] },
  });
  fireEvent.submit(form);
  expect(screen.getByRole("alert")).toHaveTextContent("64 byte limit");
  const accepted = new File(["deck"], "replacement.PPTX");
  fireEvent.change(input, { target: { files: [accepted] } });
  fireEvent.change(
    screen.getByRole("textbox", {
      name: "Replacement expected fonts (comma separated)",
    }),
    { target: { value: "Aptos, Carlito" } },
  );
  fireEvent.submit(form);
  await waitFor(() => expect(api.replace).toHaveBeenCalledOnce());
  expect(api.replace).toHaveBeenCalledWith(
    sharedSlides.id,
    '"template-etag"',
    accepted,
    "Aptos, Carlito",
    expect.any(AbortSignal),
  );
  expect(await screen.findByText("Template content replaced.")).toBeVisible();
});

test.each(["success", "401"] as const)(
  "closing management releases a pending stale refresh before another template is managed (%s)",
  async (outcome) => {
    const refresh = deferred<{ data: typeof sharedSlides; etag: string }>();
    let staleSignal: AbortSignal | undefined;
    const api = templateApi({
      template: vi
        .fn()
        .mockResolvedValueOnce({ data: sharedSlides, etag: '"template-etag"' })
        .mockImplementationOnce((_id: string, signal: AbortSignal) => {
          staleSignal = signal;
          return refresh.promise;
        })
        .mockResolvedValueOnce({ data: archivedSlides, etag: '"other-etag"' }),
      updateMetadata: vi
        .fn()
        .mockRejectedValueOnce(
          new ApiError(412, "PRECONDITION_FAILED", "Old conflict"),
        ),
    });
    const { expire } = show(api, admin);
    const item = (
      await screen.findByRole("heading", { name: "Shared slides" })
    ).closest("li")!;
    fireEvent.click(within(item).getByRole("button", { name: "Manage" }));
    await screen.findByRole("heading", { name: "Manage Shared slides" });
    fireEvent.change(screen.getByRole("textbox", { name: "Template name" }), {
      target: { value: "Old edit" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save details" }));
    await waitFor(() => expect(api.template).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "Close management" }));
    expect(staleSignal?.aborted).toBe(true);
    expect(
      screen.getByRole("button", { name: "Create template" }),
    ).toBeEnabled();
    const otherItem = screen
      .getByRole("heading", { name: "Old slides" })
      .closest("li")!;
    const manageOther = within(otherItem).getByRole("button", {
      name: "Manage",
    });
    expect(manageOther).toBeEnabled();
    fireEvent.click(manageOther);
    expect(
      await screen.findByRole("heading", { name: "Manage Old slides" }),
    ).toBeVisible();
    expect(api.template).toHaveBeenCalledTimes(3);
    expect(
      screen.getByRole("button", { name: "Delete template permanently" }),
    ).toBeEnabled();
    await act(async () => {
      if (outcome === "401")
        refresh.reject(
          new ApiError(401, "AUTHENTICATION_REQUIRED", "Old refresh"),
        );
      else
        refresh.resolve({
          data: { ...sharedSlides, name: "Authoritative slides" },
          etag: '"new-etag"',
        });
    });
    expect(
      screen.getByRole("heading", { name: "Manage Old slides" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Manage Authoritative slides" }),
    ).toBeNull();
    expect(screen.queryByText("Old refresh")).toBeNull();
    expect(expire).not.toHaveBeenCalled();
  },
);

test("closing management preserves an unrelated pending library preference through authoritative reload", async () => {
  const pending = deferred<void>();
  let preferenceSignal: AbortSignal | undefined;
  const context = {
    preferred_template_id: null as string | null,
    system_fallback_template_id: null,
    template_max_archive_bytes: 64,
  };
  const api = templateApi({
    setPreferred: vi.fn((_id: string, signal: AbortSignal) => {
      preferenceSignal = signal;
      return pending.promise;
    }),
    templateContext: vi
      .fn()
      .mockResolvedValueOnce(context)
      .mockResolvedValueOnce({
        ...context,
        preferred_template_id: ownedWord.id,
      }),
  });
  show(api, admin);
  const managedItem = (
    await screen.findByRole("heading", { name: "Shared slides" })
  ).closest("li")!;
  fireEvent.click(within(managedItem).getByRole("button", { name: "Manage" }));
  await screen.findByRole("heading", { name: "Manage Shared slides" });
  const preferredItem = screen
    .getByRole("heading", { name: "Owned classic" })
    .closest("li")!;
  fireEvent.click(
    within(preferredItem).getByRole("button", { name: "Make preferred" }),
  );
  await waitFor(() => expect(api.setPreferred).toHaveBeenCalledOnce());
  expect(preferenceSignal?.aborted).toBe(false);
  expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Close management" }));
  expect(preferenceSignal?.aborted).toBe(false);
  expect(
    screen.queryByRole("heading", { name: "Manage Shared slides" }),
  ).toBeNull();
  expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  expect(
    within(preferredItem).getByRole("button", { name: "Make preferred" }),
  ).toBeDisabled();
  expect(api.allTemplates).toHaveBeenCalledOnce();

  await act(async () => pending.resolve(undefined));
  expect(await screen.findByText("Preferred template updated.")).toBeVisible();
  const updatedItem = screen
    .getByRole("heading", { name: "Owned classic" })
    .closest("li")!;
  expect(
    within(updatedItem).getByRole("button", { name: "Preferred" }),
  ).toBeEnabled();
  expect(screen.getByRole("button", { name: "Create template" })).toBeEnabled();
  expect(api.setPreferred).toHaveBeenCalledOnce();
  expect(api.allTemplates).toHaveBeenCalledTimes(2);
  expect(api.templateContext).toHaveBeenCalledTimes(2);
  expect(screen.getAllByText("Preferred template updated.")).toHaveLength(1);
});
