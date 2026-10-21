import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApiError } from "../src/api/transport";
import { LibraryWorkspace } from "../src/composer/library";
import type { LibraryApi, LibraryDocument } from "../src/composer/library-api";

const user = {
  active: true,
  effective_idle_minutes: 30,
  id: "user-1",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};
const owned: LibraryDocument = {
  id: "document-1",
  owner_id: user.id,
  title: "Field notes",
  version: 1,
  etag: '"1"',
  current_version_id: "version-1",
  shared_with: ["user-2"],
  created_at: "2026-10-21T00:00:00Z",
  updated_at: "2026-10-21T00:00:00Z",
};
const shared: LibraryDocument = {
  ...owned,
  id: "document-2",
  owner_id: "user-3",
  title: "Shared report",
  shared_with: [],
};
const version = {
  id: "version-1",
  document_id: owned.id,
  number: 1,
  sha256: "a".repeat(64),
  size: 42,
  media_type: "text/markdown",
  chunk_count: 1,
  created_at: "2026-10-21T00:00:00Z",
};
const hit = {
  document_id: owned.id,
  title: "Field notes",
  version_id: "version-1",
  version_number: 1,
  chunk: 0,
  score: 2,
  snippet: "The northern turbine failed.",
};

function library(overrides: Partial<Record<keyof LibraryApi, unknown>> = {}) {
  return {
    list: vi.fn().mockResolvedValue([owned, shared]),
    upload: vi.fn().mockResolvedValue(owned),
    replace: vi.fn().mockResolvedValue({ data: { ...owned, version: 2 } }),
    versions: vi.fn().mockResolvedValue([version]),
    download: vi.fn().mockResolvedValue(
      new Response("# Notes", {
        headers: {
          "content-type": "text/markdown",
          "content-disposition": 'attachment; filename="notes.md"',
          "x-content-type-options": "nosniff",
        },
      }),
    ),
    grant: vi.fn().mockResolvedValue(owned),
    revoke: vi.fn().mockResolvedValue({ ...owned, shared_with: [] }),
    remove: vi.fn().mockResolvedValue(undefined),
    search: vi.fn().mockResolvedValue([hit]),
    excerpts: vi.fn().mockResolvedValue([
      {
        ...hit,
        version_sha256: "a".repeat(64),
        text: "The northern turbine failed after the storm.",
      },
    ]),
    ...overrides,
  };
}

function renderLibrary(api = library(), expire = vi.fn(), save = vi.fn()) {
  render(
    <LibraryWorkspace
      api={api as unknown as LibraryApi}
      expire={expire}
      save={save}
      user={user}
    />,
  );
  return { api, expire, save };
}

test("owners upload, search, read excerpts and manage a document", async () => {
  const { api, save } = renderLibrary();
  expect(await screen.findByText("Shared report")).toBeInTheDocument();
  expect(screen.getByText(/shared with you/)).toBeInTheDocument();

  const file = new File(["# Notes"], "notes.md", { type: "text/markdown" });
  fireEvent.change(screen.getByLabelText("Source (.md, .txt, .docx, .pptx)"), {
    target: { files: [file] },
  });
  fireEvent.change(screen.getByLabelText("Title (optional)"), {
    target: { value: "Field notes" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Upload document" }));
  expect(
    await screen.findByText("Library document uploaded."),
  ).toBeInTheDocument();
  expect(api.upload).toHaveBeenCalledWith(file, "Field notes");
  expect(
    screen.getByRole("button", { name: "Download version 1" }),
  ).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Search terms"), {
    target: { value: "turbine" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Search library" }));
  expect(
    await screen.findByText("The northern turbine failed."),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show excerpt" }));
  expect(
    await screen.findByText("The northern turbine failed after the storm."),
  ).toBeInTheDocument();
  expect(api.excerpts).toHaveBeenCalledWith([
    { version_id: "version-1", chunk: 0 },
  ]);

  fireEvent.click(screen.getByRole("button", { name: "Download version 1" }));
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  expect(save.mock.calls[0]![0].filename).toBe("notes.md");

  fireEvent.change(screen.getByLabelText("Replacement source"), {
    target: { files: [file] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Publish new version" }));
  expect(
    await screen.findByText("New library version published."),
  ).toBeInTheDocument();
  expect(api.replace).toHaveBeenCalledWith(owned, file);

  fireEvent.change(screen.getByLabelText("Account ID"), {
    target: { value: " user-4 " },
  });
  fireEvent.click(screen.getByRole("button", { name: "Share document" }));
  expect(
    await screen.findByText("Library document shared."),
  ).toBeInTheDocument();
  expect(api.grant).toHaveBeenCalledWith(owned, "user-4");
  fireEvent.click(screen.getByRole("button", { name: "Revoke user-2" }));
  expect(
    await screen.findByText("Library access revoked."),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Delete document" }));
  expect(
    await screen.findByText("Library document deleted."),
  ).toBeInTheDocument();
  expect(api.remove).toHaveBeenCalledWith(owned);
  expect(
    screen.queryByRole("region", { name: "Library document details" }),
  ).not.toBeInTheDocument();
});

test("readers cannot manage a shared document and filters are explicit", async () => {
  const { api } = renderLibrary();
  fireEvent.click(await screen.findByRole("button", { name: "Shared report" }));
  expect(
    await screen.findByRole("button", { name: "Download version 1" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Delete document" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Account ID")).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Filter titles"), {
    target: { value: "report" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Filter" }));
  await waitFor(() => expect(api.list).toHaveBeenLastCalledWith("report"));
});

test("library failures are explained and an ended session expires", async () => {
  const expire = vi.fn();
  const api = library({
    list: vi
      .fn()
      .mockResolvedValueOnce([owned])
      .mockRejectedValue(new ApiError(401, "AUTH", "Ended.")),
    search: vi.fn().mockResolvedValue([]),
    versions: vi
      .fn()
      .mockRejectedValue(new ApiError(404, "COMPOSER_LIBRARY_NOT_FOUND", "x")),
    remove: vi
      .fn()
      .mockRejectedValue(new ApiError(412, "COMPOSER_LIBRARY_CHANGED", "x")),
  });
  renderLibrary(api, expire);

  fireEvent.change(await screen.findByLabelText("Search terms"), {
    target: { value: "nothing" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Search library" }));
  expect(await screen.findByText("No matching excerpts.")).toBeInTheDocument();

  fireEvent.click(await screen.findByRole("button", { name: "Field notes" }));
  expect(
    await screen.findByText(
      "This library document is no longer available to you.",
    ),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Filter" }));
  expect(
    await screen.findByText("Your session ended. Please sign in again."),
  ).toBeInTheDocument();
  expect(expire).toHaveBeenCalledOnce();
});

test("an unavailable library reports a safe message", async () => {
  renderLibrary(library({ list: vi.fn().mockRejectedValue(new Error("x")) }));
  expect(
    await screen.findByText("The reference library is unavailable. Try again."),
  ).toBeInTheDocument();
});

test("a stale owner change asks for a review", async () => {
  renderLibrary(
    library({
      remove: vi
        .fn()
        .mockRejectedValue(new ApiError(412, "COMPOSER_LIBRARY_CHANGED", "x")),
      upload: vi
        .fn()
        .mockRejectedValue(
          new ApiError(422, "COMPOSER_LIBRARY_NO_TEXT", "No text."),
        ),
    }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Field notes" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Delete document" }),
  );
  expect(
    await screen.findByText(
      "This library document changed. Review the latest version before retrying.",
    ),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Source (.md, .txt, .docx, .pptx)"), {
    target: { files: [new File([""], "blank.md")] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Upload document" }));
  expect(await screen.findByText("No text.")).toBeInTheDocument();
});
