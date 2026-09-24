import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach } from "vitest";
import type { AdministrationApi } from "../src/admin/api";
import { TemplatesWorkspace } from "../src/admin/templates";
import { ApiError } from "../src/api/transport";

afterEach(() => vi.restoreAllMocks());

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
const template = {
  current_version_id: "10000000-0000-4000-8000-000000000001",
  description: "Approved document style",
  id: "20000000-0000-4000-8000-000000000001",
  kind: "docx" as const,
  name: "Classic report",
  owner_id: owner.id,
  owner_username: owner.username,
  revision: 1,
  status: "active" as const,
};
const version = {
  created_at: "2026-09-23T12:00:00Z",
  created_by: owner.id,
  declared_fonts: ["Carlito"],
  id: template.current_version_id,
  number: 1,
  resolved_fonts: [["Carlito", "Carlito"]] as [string, string][],
  restored_from_version_id: null,
  sha256: "a".repeat(64),
  size: 120,
  template_id: template.id,
  validation_trace: ["static_ooxml"],
};
const context = {
  preferred_template_id: null,
  system_fallback_template_id: null,
  template_max_archive_bytes: 1024,
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

function service(overrides: Record<string, unknown> = {}) {
  return {
    allTemplates: vi.fn().mockResolvedValue([template]),
    archive: vi.fn(),
    clearPreferred: vi.fn(),
    create: vi.fn(),
    delete: vi.fn(),
    replace: vi.fn(),
    restore: vi.fn(),
    setFallback: vi.fn(),
    setPreferred: vi.fn(),
    template: vi
      .fn()
      .mockResolvedValue({ data: template, etag: '"revision-1"' }),
    templateContent: vi.fn(),
    templateContext: vi.fn().mockResolvedValue(context),
    updateMetadata: vi.fn(),
    versions: vi.fn().mockResolvedValue([version]),
    ...overrides,
  };
}

function show(api: ReturnType<typeof service>, isAdmin = false) {
  const expire = vi.fn();
  render(
    <TemplatesWorkspace
      api={api as unknown as AdministrationApi}
      expire={expire}
      user={isAdmin ? admin : owner}
    />,
  );
  return { expire };
}

async function manageReport() {
  const initialItem = (
    await screen.findByRole("heading", { name: "Classic report" })
  ).closest("li")!;
  fireEvent.click(within(initialItem).getByRole("button", { name: "Manage" }));
  await screen.findByRole("heading", { name: "Manage Classic report" });
  return screen.getByRole("heading", { name: "Classic report" }).closest("li")!;
}

test("closing management does not cancel an independent template creation or reset its form early", async () => {
  const create = deferred<unknown>();
  let createSignal: AbortSignal | undefined;
  const newTemplate = {
    ...template,
    id: "20000000-0000-4000-8000-000000000002",
    name: "Board memo",
  };
  const api = service({
    allTemplates: vi
      .fn()
      .mockResolvedValueOnce([template])
      .mockResolvedValueOnce([template, newTemplate]),
    create: vi.fn((_payload, signal: AbortSignal) => {
      createSignal = signal;
      return create.promise;
    }),
  });
  const { expire } = show(api);
  await manageReport();
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "Board memo" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Description" }), {
    target: { value: "Approved board style" },
  });
  const file = new File(["document"], "memo.docx");
  fireEvent.change(screen.getByLabelText("DOCX file"), {
    target: { files: [file] },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "Create template" }).closest("form")!,
  );
  await waitFor(() => expect(api.create).toHaveBeenCalledOnce());
  expect(api.create.mock.calls[0]?.[0]).toMatchObject({
    content: file,
    kind: "docx",
    name: "Board memo",
  });
  fireEvent.click(screen.getByRole("button", { name: "Close management" }));
  expect(createSignal?.aborted).toBe(false);
  expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
    "Board memo",
  );
  expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  expect(
    screen.queryByRole("heading", { name: "Manage Classic report" }),
  ).toBeNull();

  await act(async () => create.resolve(newTemplate));
  expect(
    await screen.findByRole("heading", { name: "Board memo" }),
  ).toBeVisible();
  expect(screen.getByText("Template created.")).toBeVisible();
  expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("");
  expect(screen.getByRole("button", { name: "Create template" })).toBeEnabled();
  expect(api.allTemplates).toHaveBeenCalledTimes(2);
  expect(expire).not.toHaveBeenCalled();
});

test.each([
  [403, "You are not allowed to perform this action.", false],
  [401, "Your session ended. Please sign in again.", true],
] as const)(
  "closing management retains an independent fallback request and honors its %i result",
  async (status, message, shouldExpire) => {
    const fallback = deferred<void>();
    let fallbackSignal: AbortSignal | undefined;
    const api = service({
      setFallback: vi.fn((_id: string, signal: AbortSignal) => {
        fallbackSignal = signal;
        return fallback.promise;
      }),
    });
    const { expire } = show(api, true);
    const item = await manageReport();
    fireEvent.click(
      within(item).getByRole("button", { name: "Set system fallback" }),
    );
    await waitFor(() => expect(api.setFallback).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "Close management" }));
    expect(fallbackSignal?.aborted).toBe(false);
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(
      screen.queryByRole("heading", { name: "Manage Classic report" }),
    ).toBeNull();

    await act(async () =>
      fallback.reject(
        new ApiError(status, "DENIED", "private authorization detail"),
      ),
    );
    expect(await screen.findByText(message)).toBeVisible();
    expect(screen.queryByText("private authorization detail")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Create template" }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("heading", { name: "Manage Classic report" }),
    ).toBeNull();
    expect(api.allTemplates).toHaveBeenCalledOnce();
    expect(expire).toHaveBeenCalledTimes(shouldExpire ? 1 : 0);
  },
);

test("closing management leaves a current-library download pending until its verified response arrives", async () => {
  const download = deferred<Response>();
  let downloadSignal: AbortSignal | undefined;
  const api = service({
    templateContent: vi.fn(
      (_id: string, _versionId: string | undefined, signal: AbortSignal) => {
        downloadSignal = signal;
        return download.promise;
      },
    ),
  });
  const createObjectURL = vi
    .spyOn(URL, "createObjectURL")
    .mockReturnValue("blob:verified-template");
  const revokeObjectURL = vi
    .spyOn(URL, "revokeObjectURL")
    .mockImplementation(() => undefined);
  const anchorClick = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
  show(api);
  const item = await manageReport();
  fireEvent.click(
    within(item).getByRole("button", { name: "Download current DOCX" }),
  );
  await waitFor(() => expect(api.templateContent).toHaveBeenCalledOnce());
  expect(api.templateContent).toHaveBeenCalledWith(
    template.id,
    undefined,
    expect.any(AbortSignal),
  );
  fireEvent.click(screen.getByRole("button", { name: "Close management" }));
  expect(downloadSignal?.aborted).toBe(false);
  expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  expect(anchorClick).not.toHaveBeenCalled();

  await act(async () =>
    download.resolve(
      new Response("document", {
        headers: {
          "Cache-Control": "private, no-store",
          "Content-Disposition": 'attachment; filename="report.docx"',
          "Content-Type":
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          "X-Content-Type-Options": "nosniff",
        },
      }),
    ),
  );
  await waitFor(() => expect(anchorClick).toHaveBeenCalledOnce());
  expect(createObjectURL).toHaveBeenCalledOnce();
  await waitFor(() =>
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:verified-template"),
  );
  expect(screen.getByRole("button", { name: "Create template" })).toBeEnabled();
  expect(
    screen.queryByRole("heading", { name: "Manage Classic report" }),
  ).toBeNull();
});

test("a historical download canceled by Close cannot save a late response or expire a newer library view", async () => {
  const historical = deferred<Response>();
  let historicalSignal: AbortSignal | undefined;
  const api = service({
    templateContent: vi.fn(
      (_id: string, _versionId: string | undefined, signal: AbortSignal) => {
        historicalSignal = signal;
        return historical.promise;
      },
    ),
  });
  const anchorClick = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
  const { expire } = show(api);
  await manageReport();
  fireEvent.click(screen.getByRole("button", { name: "Download version 1" }));
  await waitFor(() => expect(api.templateContent).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("button", { name: "Close management" }));
  expect(historicalSignal?.aborted).toBe(true);
  expect(screen.getByRole("button", { name: "Create template" })).toBeEnabled();

  await act(async () =>
    historical.reject(
      new ApiError(401, "SESSION_EXPIRED", "old private download"),
    ),
  );
  expect(anchorClick).not.toHaveBeenCalled();
  expect(expire).not.toHaveBeenCalled();
  expect(screen.queryByText("old private download")).toBeNull();
  expect(
    screen.queryByRole("heading", { name: "Manage Classic report" }),
  ).toBeNull();
});
