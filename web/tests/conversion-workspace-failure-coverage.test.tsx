import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach } from "vitest";
import { AuthProvider } from "../src/auth/context";
import { AuthController } from "../src/auth/controller";
import type { ApiTransport } from "../src/api/transport";
import { ConversionController } from "../src/conversion/controller";
import { ConversionWorkspace } from "../src/conversion/workspace";
import {
  readConversionInputs,
  writeConversionInputs,
  type SavedConversionInputs,
} from "../src/conversion/persistence";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

function observeDocumentNavigation(): string[] {
  const paths: string[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    paths.push(this.getAttribute("href") ?? "");
  });
  return paths;
}
vi.mock("../src/conversion/persistence", () => ({
  ownerStorageEpoch: vi.fn().mockReturnValue(0),
  resumeOwnerStorage: vi.fn(),
  readConversionInputs: vi.fn().mockResolvedValue(undefined),
  writeConversionInputs: vi.fn().mockResolvedValue(undefined),
  clearConversionInputs: vi.fn().mockResolvedValue(undefined),
}));

afterEach(() => {
  vi.restoreAllMocks();
  push.mockReset();
  vi.mocked(readConversionInputs).mockReset().mockResolvedValue(undefined);
  vi.mocked(writeConversionInputs).mockReset().mockResolvedValue(undefined);
});

const user = {
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};

const options = {
  conversion_upload_max_bytes: 1_000_000,
  resolved_template: null,
  selection_source: "pandoc_default",
  template_version_id: null,
};

const emptyPage = { items: [], limit: 10, offset: 0, total: 0 };
const job = {
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
  source_filename: "",
  owner_id: user.id,
  progress: 100,
  state: "succeeded" as const,
  step: "complete",
  template_id: null,
  template_mode: "pandoc-default" as const,
  template_version_id: null,
  updated_at: "2026-09-02T08:00:00Z",
};

function renderWorkspace(api: Partial<ApiTransport>) {
  const auth = new AuthController({
    json: vi.fn().mockResolvedValue(user),
  } as unknown as ApiTransport);
  const conversion = new ConversionController(
    api as ApiTransport,
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

function standardJson() {
  return vi.fn(async (path: string) => {
    if (path === "/api/v1/conversion-options") return options;
    return emptyPage;
  });
}

test("retrying saved inputs preserves a query typed during the failed read", async () => {
  let resolveRetry!: (value: SavedConversionInputs) => void;
  vi.mocked(readConversionInputs)
    .mockRejectedValueOnce(new Error("browser storage unavailable"))
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveRetry = resolve;
        }),
    );
  renderWorkspace({ json: standardJson() });

  const retry = await screen.findByRole("button", {
    name: "Try loading saved inputs",
  });
  const search = screen.getByRole("searchbox", {
    name: "Search active templates",
  });
  fireEvent.change(search, { target: { value: "current query" } });
  fireEvent.click(retry);
  expect(
    screen.getByRole("button", { name: "Loading saved inputs…" }),
  ).toBeDisabled();
  expect(writeConversionInputs).not.toHaveBeenCalled();

  resolveRetry({
    dialect: "auto",
    output: "pdf",
    query: "stale saved query",
    slideLevel: 2,
  });
  await waitFor(() =>
    expect(screen.getByRole("radio", { name: "PDF" })).toBeChecked(),
  );
  expect(search).toHaveValue("current query");
  expect(
    screen.queryByText(/Saved browser inputs could not be loaded/),
  ).toBeNull();
  await waitFor(() =>
    expect(vi.mocked(writeConversionInputs).mock.lastCall?.[3]).toBe(
      "current query",
    ),
  );
});

test("a late saved read cannot restore private inputs after session expiry", async () => {
  let resolveRead!: (value: SavedConversionInputs) => void;
  vi.mocked(readConversionInputs).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveRead = resolve;
      }),
  );
  const { auth } = renderWorkspace({ json: standardJson() });
  await waitFor(() => expect(readConversionInputs).toHaveBeenCalledOnce());
  auth.expire();
  await waitFor(() =>
    expect(
      screen.queryByRole("heading", { name: "Convert Markdown" }),
    ).toBeNull(),
  );
  await act(async () => {
    resolveRead({
      dialect: "auto",
      output: "pdf",
      query: "private template",
      slideLevel: 2,
      source: new File(["private"], "private.md"),
    });
  });
  expect(writeConversionInputs).not.toHaveBeenCalled();
  expect(document.body).not.toHaveTextContent("private.md");
});

test("template fallback and unavailable search results are explained before selection", async () => {
  const fallback = {
    current_version_id: "00000000-0000-4000-8000-000000000102",
    description: "",
    id: "00000000-0000-4000-8000-000000000101",
    name: "System styles",
    owner_id: user.id,
    owner_username: "Alice",
    revision: 1,
    status: "active",
  };
  const unavailable = {
    ...fallback,
    current_version_id: null,
    id: "00000000-0000-4000-8000-000000000103",
    name: "Awaiting revision",
  };
  const json = vi.fn(async (path: string) => {
    if (path === "/api/v1/conversion-options")
      return {
        ...options,
        resolved_template: fallback,
        selection_source: "system_fallback",
        template_version_id: fallback.current_version_id,
      };
    if (path.startsWith("/api/v1/conversions?")) return emptyPage;
    return { items: [unavailable, fallback], limit: 20, offset: 0, total: 2 };
  });
  renderWorkspace({ json });
  expect(await screen.findByText("System fallback template")).toBeVisible();
  expect(
    screen.getByText("System styles", { selector: "strong" }),
  ).toBeVisible();

  fireEvent.change(
    screen.getByRole("searchbox", { name: "Search active templates" }),
    {
      target: { value: "styles" },
    },
  );
  const unavailableButton = await screen.findByRole("button", {
    name: /Awaiting revision/,
  });
  expect(unavailableButton).toBeDisabled();
  expect(unavailableButton).toHaveTextContent("No description");
  fireEvent.click(screen.getByRole("button", { name: /System styles/ }));
  expect(screen.getByText("Selected template")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Use Pandoc default" }));
  expect(
    screen.getByText("Pandoc default", { selector: "strong" }),
  ).toBeVisible();
});

test("a failed final source save blocks Composer upload while retaining the source", async () => {
  const navigations = observeDocumentNavigation();
  const multipartWithMetadata = vi.fn();
  renderWorkspace({ json: standardJson(), multipartWithMetadata });
  const input = await screen.findByLabelText(/Source file/);
  const source = new File(["# Retained"], "retained.md");
  fireEvent.change(input, { target: { files: [source] } });
  await waitFor(() =>
    expect(vi.mocked(writeConversionInputs).mock.lastCall?.[2].source).toBe(
      source,
    ),
  );
  const open = screen.getByRole("button", {
    name: "Open selected source in Composer",
  });
  expect(open).toBeEnabled();
  vi.mocked(writeConversionInputs).mockRejectedValueOnce(
    new DOMException("quota", "QuotaExceededError"),
  );
  fireEvent.click(open);
  expect(
    await screen.findByText(/could not be saved in this browser/),
  ).toBeVisible();
  expect(open).toBeDisabled();
  expect(screen.getByText("Selected retained.md (10 bytes).")).toBeVisible();
  expect(multipartWithMetadata).not.toHaveBeenCalled();
  expect(navigations).toEqual([]);
  expect(push).not.toHaveBeenCalled();
});

test("a recent unnamed result can hand off once despite browser storage failure", async () => {
  const navigations = observeDocumentNavigation();
  vi.mocked(writeConversionInputs).mockRejectedValue(
    new DOMException("quota", "QuotaExceededError"),
  );
  const draftId = "00000000-0000-4000-8000-000000000301";
  let resolveHandoff!: (value: unknown) => void;
  const jsonWithMetadata = vi.fn(
    () =>
      new Promise((resolve) => {
        resolveHandoff = resolve;
      }),
  );
  const json = vi.fn(async (path: string) => {
    if (path === "/api/v1/conversion-options") return options;
    if (path.startsWith("/api/v1/conversions?"))
      return { items: [job], limit: 10, offset: 0, total: 1 };
    if (path === `/api/v1/conversions/${job.id}`) return job;
    return emptyPage;
  });
  renderWorkspace({
    json,
    jsonWithMetadata:
      jsonWithMetadata as unknown as ApiTransport["jsonWithMetadata"],
  });
  const recent = await screen.findByRole("button", {
    name: "Untitled document · succeeded",
  });
  fireEvent.click(recent);
  const open = await screen.findByRole("button", {
    name: "Open result in Composer",
  });
  fireEvent.click(open);
  await waitFor(() => expect(jsonWithMetadata).toHaveBeenCalledOnce());
  expect(
    screen.getByRole("button", { name: "Creating Composer draft…" }),
  ).toBeDisabled();
  expect(push).not.toHaveBeenCalled();

  resolveHandoff({
    data: {
      content: "# Result",
      created_at: "2026-09-23T08:00:00Z",
      current_revision_id: null,
      etag: '"1"',
      id: draftId,
      source_kind: "conversion",
      source_media_type:
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      title: "result",
      updated_at: "2026-09-23T08:00:00Z",
      version: 1,
    },
    location: `/api/v1/composer/drafts/${draftId}`,
    status: 201,
  });
  await waitFor(() =>
    expect(navigations).toContain(`/composer?draft=${draftId}`),
  );
  expect(push).not.toHaveBeenCalled();
  expect(jsonWithMetadata).toHaveBeenCalledWith(
    `/api/v1/composer/drafts/from-conversion/${job.id}`,
    expect.anything(),
    expect.objectContaining({ csrf: true, method: "POST" }),
  );
});
