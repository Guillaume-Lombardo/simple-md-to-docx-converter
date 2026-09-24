import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApiError } from "../src/api/transport";
import type { AuthorDirectoryApi } from "../src/composer/author-directory-api";
import { AuthorDirectory } from "../src/composer/author-directory";
import type { FillTemplateApi } from "../src/composer/fill-template-api";
import { FillTemplatesWorkspace } from "../src/composer/fill-templates";
import { ComposerFillPanel } from "../src/composer/fill-panel";

const owner = {
  active: true,
  effective_idle_minutes: 30,
  id: "user-1",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};
const shared = { ...owner, id: "user-2", username: "Bob" };
const author = {
  id: "author-1",
  owner_id: owner.id,
  name: "Alice",
  fields: { title: { value: "Editor", provenance: "supplied" as const } },
  version: 1,
  shared_with: [] as string[],
};
const schema = {
  fields: [
    { name: "title", type: "text" as const, required: true, constraints: {} },
  ],
  repeats: [
    {
      name: "findings",
      min_items: 0,
      max_items: 2,
      fields: [
        {
          name: "summary",
          type: "text" as const,
          required: true,
          constraints: {},
        },
      ],
    },
  ],
};
const template = {
  id: "template-1",
  owner_id: owner.id,
  name: "Report",
  version: 1,
  etag: '"template-1"',
  active_version_id: "version-2",
  shared_with: [] as string[],
  created_at: "2026-09-24T00:00:00Z",
  updated_at: "2026-09-24T00:00:00Z",
};
const active = {
  id: "version-2",
  template_id: template.id,
  number: 2,
  schema_version: 1,
  schema,
  schema_sha256: "a".repeat(64),
  docx_sha256: "b".repeat(64),
  size: 100,
  created_at: "2026-09-24T00:00:00Z",
};
const old = {
  ...active,
  id: "version-1",
  number: 1,
  docx_sha256: "c".repeat(64),
};
const draft = {
  id: "draft-1",
  title: "Draft",
  content: "# Draft",
  version: 1,
  current_revision_id: "source-1",
  source_kind: "upload",
  source_media_type: "text/markdown",
  created_at: "2026-09-24T00:00:00Z",
  updated_at: "2026-09-24T00:00:00Z",
  etag: '"draft-1"',
};
const plan = {
  id: "plan-1",
  draft_id: draft.id,
  source_revision_id: "source-1",
  template_id: template.id,
  template_version_id: active.id,
  author_refs: [],
  version: 1,
  etag: '"plan-1"',
  values: { title: "Approved" },
  provenance: { title: { kind: "human_approved" as const } },
  questions: [],
  state: "approved" as const,
  result_revision_id: null,
};

test("author creation preserves unknown facts and rejects duplicate field names before submission", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([]),
    create: vi.fn().mockResolvedValue(author),
    get: vi.fn().mockResolvedValue({ data: author, etag: '"author-1"' }),
  };
  render(
    <AuthorDirectory
      api={api as unknown as AuthorDirectoryApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  await waitFor(() => expect(api.list).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Author name"), {
    target: { value: "Alice" },
  });
  fireEvent.change(screen.getByLabelText("Field name"), {
    target: { value: "office" },
  });
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Unknown or unresolved" }),
  );
  fireEvent.change(screen.getByLabelText("Provenance"), {
    target: { value: "unresolved" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "Create author" }).closest("form")!,
  );
  await waitFor(() =>
    expect(api.create).toHaveBeenCalledWith({
      name: "Alice",
      fields: { office: { value: null, provenance: "unresolved" } },
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "New author" }));
  fireEvent.change(screen.getByLabelText("Author name"), {
    target: { value: "Duplicate" },
  });
  fireEvent.change(screen.getByLabelText("Field name"), {
    target: { value: "title" },
  });
  fireEvent.change(screen.getByLabelText("Value"), {
    target: { value: "One" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add field" }));
  fireEvent.change(screen.getAllByLabelText("Field name")[1]!, {
    target: { value: "title" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "Create author" }).closest("form")!,
  );
  expect(
    screen.getByText("Each filled author field needs a unique name."),
  ).toBeVisible();
  expect(api.create).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Remove field 2" }));
  expect(screen.getAllByLabelText("Field name")).toHaveLength(1);
});

test("shared author is readable without edit or share controls", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([author]),
    get: vi.fn().mockResolvedValue({ data: author, etag: '"author-1"' }),
  };
  render(
    <AuthorDirectory
      api={api as unknown as AuthorDirectoryApi}
      expire={vi.fn()}
      user={shared}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Alice (shared)" }),
  );
  expect(screen.getByLabelText("Author name")).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Save author" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Author sharing" })).toBeNull();
});

test("stale author mutation reloads the newest version and leaves the conflict visible", async () => {
  let version = 1;
  const api = {
    list: vi.fn().mockResolvedValue([author]),
    get: vi.fn().mockImplementation(() =>
      Promise.resolve({
        data: { ...author, version },
        etag: `"author-${version}"`,
      }),
    ),
    update: vi.fn().mockImplementation(() => {
      version = 2;
      return Promise.reject(new ApiError(412, "STALE", "raw"));
    }),
  };
  render(
    <AuthorDirectory
      api={api as unknown as AuthorDirectoryApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Alice (mine)" }));
  fireEvent.submit(
    (await screen.findByRole("button", { name: "Save author" })).closest(
      "form",
    )!,
  );
  expect(
    await screen.findByText(
      "This author entry changed. Review the latest version before retrying.",
    ),
  ).toBeVisible();
  expect(screen.getByText(/Version: 2/)).toBeVisible();
  expect(screen.queryByText("raw")).toBeNull();
});

test("author pagination loads the next authorized page", async () => {
  const first = Array.from({ length: 100 }, (_, at) => ({
    ...author,
    id: `author-${at}`,
    name: `Author ${at}`,
  }));
  const api = {
    list: vi
      .fn()
      .mockImplementation((offset: number) =>
        Promise.resolve(
          offset === 0
            ? first
            : [{ ...author, id: "last", name: "Last author" }],
        ),
      ),
  };
  const expire = vi.fn();
  render(
    <AuthorDirectory
      api={api as unknown as AuthorDirectoryApi}
      expire={expire}
      user={owner}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Load more authors" }),
  );
  expect(
    await screen.findByRole("button", { name: "Last author (mine)" }),
  ).toBeVisible();
  expect(api.list).toHaveBeenLastCalledWith(100, undefined);
});

test("author list authentication failure expires the session without leaking detail", async () => {
  const api = {
    list: vi
      .fn()
      .mockRejectedValue(new ApiError(401, "UNAUTHORIZED", "private detail")),
  };
  const expire = vi.fn();
  render(
    <AuthorDirectory
      api={api as unknown as AuthorDirectoryApi}
      expire={expire}
      user={owner}
    />,
  );
  expect(
    await screen.findByText("Your session ended. Please sign in again."),
  ).toBeVisible();
  expect(expire).toHaveBeenCalledOnce();
  expect(screen.queryByText("private detail")).toBeNull();
});

test("author creation keeps cited source references and handles a server rejection", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([]),
    create: vi
      .fn()
      .mockRejectedValue(
        new ApiError(422, "INVALID_FIELD", "Field is invalid."),
      ),
  };
  render(
    <AuthorDirectory
      api={api as unknown as AuthorDirectoryApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  await waitFor(() => expect(api.list).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Author name"), {
    target: { value: "Alice" },
  });
  fireEvent.change(screen.getByLabelText("Field name"), {
    target: { value: "office" },
  });
  fireEvent.change(screen.getByLabelText("Value"), {
    target: { value: "Paris" },
  });
  fireEvent.change(screen.getByLabelText("Provenance"), {
    target: { value: "cited" },
  });
  fireEvent.change(screen.getByLabelText("Source reference (optional)"), {
    target: { value: "profile:4" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create author" }));
  await waitFor(() =>
    expect(api.create).toHaveBeenCalledWith({
      name: "Alice",
      fields: {
        office: {
          value: "Paris",
          provenance: "cited",
          source_reference: "profile:4",
        },
      },
    }),
  );
  expect(await screen.findByText("Field is invalid.")).toBeVisible();
});

test("author detail without a revision token cannot be edited or shared", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([author]),
    get: vi.fn().mockResolvedValue({ data: author, etag: "" }),
    update: vi.fn(),
  };
  render(
    <AuthorDirectory
      api={api as unknown as AuthorDirectoryApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Alice (mine)" }));
  expect(
    await screen.findByText("The author directory is unavailable. Try again."),
  ).toBeVisible();
  expect(screen.queryByRole("button", { name: "Save author" })).toBeNull();
  expect(api.update).not.toHaveBeenCalled();
});

test("author sharing conflict reloads current grants and ETag", async () => {
  const newer = { ...author, version: 2, shared_with: ["user-3"] };
  const api = {
    list: vi.fn().mockResolvedValue([author]),
    get: vi
      .fn()
      .mockResolvedValueOnce({ data: author, etag: '"author-1"' })
      .mockResolvedValue({ data: newer, etag: '"author-2"' }),
    grant: vi
      .fn()
      .mockRejectedValue(new ApiError(412, "STALE", "private detail")),
  };
  render(
    <AuthorDirectory
      api={api as unknown as AuthorDirectoryApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Alice (mine)" }));
  await screen.findByText(/Version: 1/);
  fireEvent.change(screen.getByLabelText("Account ID"), {
    target: { value: "user-2" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Grant access" }));
  expect(
    await screen.findByText(
      "This author entry changed. Review the latest version before retrying.",
    ),
  ).toBeVisible();
  expect(screen.getByText(/Version: 2/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Revoke user-3" })).toBeVisible();
  expect(screen.queryByText("private detail")).toBeNull();
});

test("failed author pagination leaves the authorized first page visible", async () => {
  const first = Array.from({ length: 100 }, (_, at) => ({
    ...author,
    id: `author-${at}`,
    name: `Author ${at}`,
  }));
  const api = {
    list: vi
      .fn()
      .mockResolvedValueOnce(first)
      .mockRejectedValue(new ApiError(503, "DOWN", "Try later.")),
  };
  render(
    <AuthorDirectory
      api={api as unknown as AuthorDirectoryApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Load more authors" }),
  );
  expect(await screen.findByText("Try later.")).toBeVisible();
  expect(screen.getByRole("button", { name: "Author 0 (mine)" })).toBeVisible();
});

test("author form rejects an unnamed value but omits a completely blank row", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([]),
    create: vi.fn().mockResolvedValue(author),
    get: vi.fn().mockResolvedValue({ data: author, etag: '"author-1"' }),
  };
  render(
    <AuthorDirectory
      api={api as unknown as AuthorDirectoryApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  await waitFor(() => expect(api.list).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Author name"), {
    target: { value: "Alice" },
  });
  fireEvent.change(screen.getByLabelText("Value"), {
    target: { value: "Unlabeled" },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "Create author" }).closest("form")!,
  );
  expect(
    screen.getByText("Each filled author field needs a unique name."),
  ).toBeVisible();
  expect(api.create).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Value"), { target: { value: "" } });
  fireEvent.submit(
    screen.getByRole("button", { name: "Create author" }).closest("form")!,
  );
  await waitFor(() =>
    expect(api.create).toHaveBeenCalledWith({ name: "Alice", fields: {} }),
  );
});

test("typed template owner can inspect old versions, replace, and grant then revoke", async () => {
  const current = { ...template };
  const api = {
    list: vi.fn().mockResolvedValue([current]),
    get: vi
      .fn()
      .mockImplementation(() => Promise.resolve({ data: { ...current } })),
    versions: vi.fn().mockResolvedValue([active, old]),
    version: vi.fn().mockResolvedValue(active),
    replace: vi.fn().mockResolvedValue({ data: current }),
    grant: vi.fn().mockImplementation(() => {
      current.shared_with = ["user-2"];
      current.etag = '"template-2"';
      return Promise.resolve({ data: current });
    }),
    revoke: vi.fn().mockImplementation(() => {
      current.shared_with = [];
      current.etag = '"template-3"';
      return Promise.resolve({ data: current });
    }),
  };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Report (mine)" }));
  fireEvent.change(await screen.findByLabelText("Inspect template version"), {
    target: { value: old.id },
  });
  expect(screen.getByText(`DOCX SHA-256: ${old.docx_sha256}`)).toBeVisible();
  fireEvent.change(screen.getByLabelText("Account ID"), {
    target: { value: "user-2" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Grant access" }));
  await waitFor(() =>
    expect(api.grant).toHaveBeenCalledWith(
      template.id,
      "user-2",
      '"template-1"',
    ),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Revoke user-2" }));
  await waitFor(() =>
    expect(api.revoke).toHaveBeenCalledWith(
      template.id,
      "user-2",
      '"template-2"',
    ),
  );
  fireEvent.change(screen.getByLabelText("DOCX file"), {
    target: { files: [new File(["DOCX"], "new.docx")] },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "Create new version" }).closest("form")!,
  );
  await waitFor(() =>
    expect(api.replace).toHaveBeenCalledWith(
      template.id,
      '"template-3"',
      expect.objectContaining({ fields: schema.fields }),
      expect.any(File),
    ),
  );
});

test("shared typed template remains readable and rejects local mutation controls", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    get: vi.fn().mockResolvedValue({ data: template }),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
  };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={vi.fn()}
      user={shared}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Report (shared)" }),
  );
  expect(await screen.findByText(/Active immutable version/)).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Create new version" }),
  ).toBeNull();
  expect(
    screen.queryByRole("region", { name: "Typed template sharing" }),
  ).toBeNull();
});

test("invalid DOCX and malformed constraints keep template creation local", async () => {
  const api = { list: vi.fn().mockResolvedValue([]), create: vi.fn() };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  await waitFor(() => expect(api.list).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Template name"), {
    target: { value: "Report" },
  });
  fireEvent.submit(
    screen
      .getByRole("button", { name: "Create typed template" })
      .closest("form")!,
  );
  expect(screen.getByText("Choose a non-empty DOCX file.")).toBeVisible();
  fireEvent.change(screen.getByLabelText("DOCX file"), {
    target: { files: [new File([], "empty.docx")] },
  });
  fireEvent.submit(
    screen
      .getByRole("button", { name: "Create typed template" })
      .closest("form")!,
  );
  expect(api.create).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("DOCX file"), {
    target: { files: [new File(["x"], "report.txt")] },
  });
  fireEvent.submit(
    screen
      .getByRole("button", { name: "Create typed template" })
      .closest("form")!,
  );
  expect(screen.getByText("Choose a non-empty DOCX file.")).toBeVisible();
  expect(api.create).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Add field" }));
  const constraints = screen.getByLabelText(
    "Constraints (JSON object)",
  ) as HTMLTextAreaElement;
  fireEvent.change(constraints, { target: { value: "[1]" } });
  expect(constraints.checkValidity()).toBe(false);
  fireEvent.change(constraints, { target: { value: '{"max_length":80}' } });
  expect(constraints.checkValidity()).toBe(true);
  const explicitDefault = screen.getByLabelText(
    /Explicit default/,
  ) as HTMLInputElement;
  fireEvent.change(explicitDefault, { target: { value: "bad-json" } });
  expect(explicitDefault.checkValidity()).toBe(false);
  fireEvent.change(explicitDefault, { target: { value: '"Approved"' } });
  expect(explicitDefault.checkValidity()).toBe(true);
});

test("typed schema editor sends explicit defaults, optional fields, and edited repeat bounds", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([]),
    create: vi.fn().mockResolvedValue({ data: template }),
    get: vi.fn().mockResolvedValue({ data: template }),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
  };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  await waitFor(() => expect(api.list).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Template name"), {
    target: { value: "Report" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add field" }));
  fireEvent.change(screen.getByLabelText("Field name"), {
    target: { value: "approved" },
  });
  fireEvent.change(screen.getByLabelText("Field type"), {
    target: { value: "boolean" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "Required" }));
  fireEvent.change(screen.getByLabelText(/Explicit default/), {
    target: { value: "false" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Add repeatable section" }),
  );
  fireEvent.change(screen.getByLabelText("Repeat name"), {
    target: { value: "findings" },
  });
  fireEvent.change(screen.getByLabelText("Minimum rows"), {
    target: { value: "1" },
  });
  fireEvent.change(screen.getByLabelText("Maximum rows"), {
    target: { value: "3" },
  });
  fireEvent.change(screen.getAllByLabelText("Field name")[1]!, {
    target: { value: "count" },
  });
  fireEvent.change(screen.getAllByLabelText("Field type")[1]!, {
    target: { value: "integer" },
  });
  fireEvent.change(screen.getByLabelText("DOCX file"), {
    target: { files: [new File(["docx"], "report.docx")] },
  });
  fireEvent.submit(
    screen
      .getByRole("button", { name: "Create typed template" })
      .closest("form")!,
  );
  await waitFor(() => expect(api.create).toHaveBeenCalledOnce());
  expect(api.create.mock.calls[0]![1]).toEqual({
    fields: [
      {
        name: "approved",
        type: "boolean",
        required: false,
        constraints: {},
        default: false,
      },
    ],
    repeats: [
      {
        name: "findings",
        min_items: 1,
        max_items: 3,
        fields: [
          { name: "count", type: "integer", required: true, constraints: {} },
        ],
      },
    ],
  });
});

test("typed template paging reveals older authorized catalog and immutable versions", async () => {
  const firstTemplates = Array.from({ length: 100 }, (_, at) => ({
    ...template,
    id: `template-${at}`,
    name: `Template ${at}`,
  }));
  const firstVersions = Array.from({ length: 100 }, (_, at) => ({
    ...active,
    id: `version-${at + 2}`,
    number: at + 2,
  }));
  const older = { ...old, id: "older-version", number: 1 };
  const api = {
    list: vi
      .fn()
      .mockImplementation((offset: number) =>
        Promise.resolve(offset === 0 ? firstTemplates : [template]),
      ),
    get: vi.fn().mockResolvedValue({ data: template }),
    versions: vi
      .fn()
      .mockImplementation((_id: string, offset = 0) =>
        Promise.resolve(offset === 0 ? firstVersions : [older]),
      ),
    version: vi.fn().mockResolvedValue(active),
  };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Load more templates" }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Report (mine)" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Load older versions" }),
  );
  await waitFor(() =>
    expect(api.versions).toHaveBeenLastCalledWith(template.id, 100),
  );
  fireEvent.change(screen.getByLabelText("Inspect template version"), {
    target: { value: older.id },
  });
  expect(screen.getByText(`DOCX SHA-256: ${older.docx_sha256}`)).toBeVisible();
});

test("typed template replacement conflict refreshes the current version safely", async () => {
  const newer = { ...template, version: 2, etag: '"template-2"' };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    get: vi
      .fn()
      .mockResolvedValueOnce({ data: template })
      .mockResolvedValue({ data: newer }),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
    replace: vi
      .fn()
      .mockRejectedValue(new ApiError(412, "STALE", "private detail")),
  };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Report (mine)" }));
  await screen.findByText(/Active immutable version/);
  fireEvent.change(screen.getByLabelText("DOCX file"), {
    target: { files: [new File(["docx"], "next.docx")] },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "Create new version" }).closest("form")!,
  );
  expect(
    await screen.findByText(
      "This template changed. Review the current version before retrying.",
    ),
  ).toBeVisible();
  expect(screen.getByText(/Catalog version: 2/)).toBeVisible();
  expect(screen.queryByText("private detail")).toBeNull();
});

test("typed template download rejects a mislabeled response", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    get: vi.fn().mockResolvedValue({ data: template }),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
    download: vi.fn().mockResolvedValue(
      new Response("text", {
        headers: { "content-type": "text/plain" },
      }),
    ),
  };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Report (mine)" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Download selected DOCX" }),
  );
  expect(
    await screen.findByText(
      "The typed template request could not be completed. Try again.",
    ),
  ).toBeVisible();
  expect(api.download).toHaveBeenCalledWith(template.id, active.id);
});

test("typed template list authentication failure expires the session", async () => {
  const expire = vi.fn();
  const api = {
    list: vi
      .fn()
      .mockRejectedValue(new ApiError(401, "UNAUTHORIZED", "private detail")),
  };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={expire}
      user={owner}
    />,
  );
  expect(
    await screen.findByText("Your session ended. Please sign in again."),
  ).toBeVisible();
  expect(expire).toHaveBeenCalledOnce();
  expect(screen.queryByText("private detail")).toBeNull();
});

test("revoked typed template detail stays unavailable without exposing server detail", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    get: vi
      .fn()
      .mockRejectedValue(new ApiError(403, "FORBIDDEN", "private detail")),
    versions: vi.fn().mockResolvedValue([active]),
  };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Report (mine)" }));
  expect(
    await screen.findByText(
      "This typed template is no longer available to you.",
    ),
  ).toBeVisible();
  expect(screen.queryByText("private detail")).toBeNull();
});

test("typed template without active version can be selected and reset to a new template", async () => {
  const noVersion = { ...template, active_version_id: null };
  const api = {
    list: vi.fn().mockResolvedValue([noVersion]),
    get: vi.fn().mockResolvedValue({ data: noVersion }),
    versions: vi.fn().mockResolvedValue([]),
    version: vi.fn(),
  };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Report (mine)" }));
  expect(
    await screen.findByRole("button", { name: "Create new version" }),
  ).toBeVisible();
  expect(api.version).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "New typed template" }));
  expect(screen.getByLabelText("Template name")).toHaveValue("");
  expect(
    screen.getByRole("button", { name: "Create typed template" }),
  ).toBeVisible();
});

test("typed template sharing conflict reloads the latest catalog ETag", async () => {
  const newer = { ...template, version: 2, etag: '"template-2"' };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    get: vi
      .fn()
      .mockResolvedValueOnce({ data: template })
      .mockResolvedValue({ data: newer }),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
    grant: vi
      .fn()
      .mockRejectedValue(new ApiError(409, "CONFLICT", "private detail")),
  };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Report (mine)" }));
  await screen.findByText(/Active immutable version/);
  fireEvent.change(screen.getByLabelText("Account ID"), {
    target: { value: "user-2" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Grant access" }));
  expect(
    await screen.findByText(
      "This template changed. Review the current version before retrying.",
    ),
  ).toBeVisible();
  expect(screen.getByText(/Catalog version: 2/)).toBeVisible();
  expect(api.grant).toHaveBeenCalledWith(template.id, "user-2", template.etag);
});

test("failed typed version pagination keeps the known active version available", async () => {
  const history = Array.from({ length: 100 }, (_, at) => ({
    ...active,
    id: `version-${at}`,
    number: at + 1,
  }));
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    get: vi.fn().mockResolvedValue({ data: template }),
    versions: vi
      .fn()
      .mockResolvedValueOnce(history)
      .mockRejectedValue(new ApiError(503, "DOWN", "History unavailable.")),
    version: vi.fn().mockResolvedValue(active),
  };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Report (mine)" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Load older versions" }),
  );
  expect(await screen.findByText("History unavailable.")).toBeVisible();
  expect(screen.getByText(/Active immutable version/)).toBeVisible();
});

test("typed schema editor can clear a default and remove fields and repeats", async () => {
  const api = { list: vi.fn().mockResolvedValue([]) };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={vi.fn()}
      user={owner}
    />,
  );
  await waitFor(() => expect(api.list).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: "Add field" }));
  fireEvent.change(screen.getByLabelText(/Explicit default/), {
    target: { value: '"Example"' },
  });
  fireEvent.change(screen.getByLabelText(/Explicit default/), {
    target: { value: "" },
  });
  expect(screen.getByLabelText(/Explicit default/)).toHaveValue("");
  fireEvent.click(
    screen.getByRole("button", { name: "Add repeatable section" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Add repeat field" }));
  expect(screen.getAllByLabelText("Field name")).toHaveLength(3);
  fireEvent.click(
    screen.getByRole("button", { name: "Remove repeat 1 field 2" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Remove repeat" }));
  fireEvent.click(screen.getByRole("button", { name: "Remove field 1" }));
  expect(screen.queryByLabelText("Field name")).toBeNull();
  expect(screen.queryByLabelText("Repeat name")).toBeNull();
});

test("selected immutable DOCX version downloads only from a validated response", async () => {
  const createObjectURL = vi.fn().mockReturnValue("blob:typed-template");
  const revokeObjectURL = vi.fn();
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  const anchorClick = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  URL.createObjectURL = createObjectURL;
  URL.revokeObjectURL = revokeObjectURL;
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    get: vi.fn().mockResolvedValue({ data: template }),
    versions: vi.fn().mockResolvedValue([active, old]),
    version: vi.fn().mockResolvedValue(active),
    download: vi.fn().mockResolvedValue(
      new Response("docx", {
        headers: {
          "content-type":
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          "content-disposition": 'attachment; filename="report.docx"',
          "cache-control": "private, no-store",
          "x-content-type-options": "nosniff",
        },
      }),
    ),
  };
  try {
    render(
      <FillTemplatesWorkspace
        api={api as unknown as FillTemplateApi}
        expire={vi.fn()}
        user={owner}
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Report (mine)" }),
    );
    await screen.findByText(/Active immutable version/);
    fireEvent.change(screen.getByLabelText("Inspect template version"), {
      target: { value: old.id },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Download selected DOCX" }),
    );
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledOnce());
    expect(api.download).toHaveBeenCalledWith(template.id, old.id);
  } finally {
    anchorClick.mockRestore();
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  }
});

test("fill panel resumes the exact saved plan and freezes approved values", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([plan]),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
    publish: vi
      .fn()
      .mockResolvedValue({ id: "filled-2", number: 2, draft_id: draft.id }),
    getPlan: vi.fn().mockResolvedValue({
      ...plan,
      state: "published",
      result_revision_id: "filled-2",
    }),
  };
  const authorApi = { list: vi.fn().mockResolvedValue([author]) };
  const onPublished = vi.fn();
  render(
    <ComposerFillPanel
      authorApi={authorApi as unknown as AuthorDirectoryApi}
      draft={draft}
      expire={vi.fn()}
      onPublished={onPublished}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  expect(await screen.findByText(/Fill plan plan-1/)).toBeVisible();
  expect(screen.getByLabelText("title")).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Fill DOCX from approved values" }),
  ).toBeEnabled();
  fireEvent.click(
    screen.getByRole("button", { name: "Fill DOCX from approved values" }),
  );
  await waitFor(() => expect(onPublished).toHaveBeenCalledWith("filled-2"));
});

test("repeat rows stay bounded and a stale plan refreshes before another decision", async () => {
  const pending = {
    ...plan,
    state: "pending" as const,
    questions: [{ path: "title", text: "Title?", reason: "missing" as const }],
    values: {},
  };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([pending]),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
    updatePlan: vi.fn().mockRejectedValue(new ApiError(412, "STALE", "raw")),
    getPlan: vi.fn().mockResolvedValue(pending),
  };
  const authorApi = { list: vi.fn().mockResolvedValue([]) };
  render(
    <ComposerFillPanel
      authorApi={authorApi as unknown as AuthorDirectoryApi}
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  const add = await screen.findByRole("button", { name: "Add findings row" });
  fireEvent.click(add);
  fireEvent.click(add);
  expect(add).toBeDisabled();
  fireEvent.change(screen.getByLabelText("findings[0].summary"), {
    target: { value: "One" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Remove row 2" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Save answers and edits" }),
  );
  expect(
    await screen.findByText(
      "The draft or plan changed. Reload the current version before retrying.",
    ),
  ).toBeVisible();
  expect(api.getPlan).toHaveBeenCalledWith(draft.id, pending.id);
});

test("removing a repeat row shifts its remaining value and provenance together", async () => {
  const pending = {
    ...plan,
    state: "pending" as const,
    values: {
      title: "Report",
      findings: [{ summary: "Removed" }, { summary: "Kept" }],
    },
    provenance: {
      title: { kind: "supplied" as const },
      "findings[0].summary": {
        kind: "cited" as const,
        source_reference: "removed:1",
      },
      "findings[1].summary": { kind: "supplied" as const },
    },
  };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([pending]),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
    updatePlan: vi
      .fn()
      .mockImplementation((_draftId, _plan, values, provenance) =>
        Promise.resolve({ ...pending, values, provenance }),
      ),
  };
  render(
    <ComposerFillPanel
      authorApi={
        { list: vi.fn().mockResolvedValue([]) } as unknown as AuthorDirectoryApi
      }
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Remove row 1" }));
  expect(screen.getByLabelText("findings[0].summary")).toHaveValue("Kept");
  fireEvent.click(
    screen.getByRole("button", { name: "Save answers and edits" }),
  );
  await waitFor(() => expect(api.updatePlan).toHaveBeenCalledOnce());
  expect(api.updatePlan.mock.calls[0]![2]).toEqual({
    title: "Report",
    findings: [{ summary: "Kept" }],
  });
  expect(api.updatePlan.mock.calls[0]![3]).toEqual({
    title: { kind: "supplied" },
    "findings[0].summary": { kind: "supplied" },
  });
});

test("saved human-reviewed repeat paths cannot be removed or shifted", async () => {
  const pending = {
    ...plan,
    state: "pending" as const,
    values: {
      title: "Report",
      findings: [{ summary: "First" }, { summary: "Protected" }],
    },
    provenance: {
      title: { kind: "human_edited" as const },
      "findings[0].summary": { kind: "supplied" as const },
      "findings[1].summary": { kind: "human_approved" as const },
    },
  };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([pending]),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
    updatePlan: vi.fn(),
  };
  render(
    <ComposerFillPanel
      authorApi={
        { list: vi.fn().mockResolvedValue([]) } as unknown as AuthorDirectoryApi
      }
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  expect(
    await screen.findByText(
      /Saved human-reviewed rows cannot be removed or shifted/,
    ),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "Remove row 1" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Remove row 2" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("title"), { target: { value: "" } });
  expect(
    await screen.findByText(
      "A saved human-reviewed value cannot be cleared. Enter a replacement instead.",
    ),
  ).toBeVisible();
  expect(screen.getByLabelText("title")).toHaveValue("Report");
  expect(api.updatePlan).not.toHaveBeenCalled();
});

test("clearing an unprotected field also clears its exact provenance path", async () => {
  const pending = {
    ...plan,
    state: "pending" as const,
    values: { title: "Discard" },
    provenance: { title: { kind: "supplied" as const } },
  };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([pending]),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
    updatePlan: vi
      .fn()
      .mockImplementation((_draftId, _plan, values, provenance) =>
        Promise.resolve({ ...pending, values, provenance }),
      ),
  };
  render(
    <ComposerFillPanel
      authorApi={
        { list: vi.fn().mockResolvedValue([]) } as unknown as AuthorDirectoryApi
      }
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  fireEvent.change(await screen.findByLabelText("title"), {
    target: { value: "" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Save answers and edits" }),
  );
  await waitFor(() => expect(api.updatePlan).toHaveBeenCalledOnce());
  expect(api.updatePlan.mock.calls[0]![2]).toEqual({});
  expect(api.updatePlan.mock.calls[0]![3]).toEqual({});
});

test("fill plan captures selected author, exact older version, and typed values", async () => {
  const typed = {
    ...active,
    schema: {
      fields: [
        {
          name: "title",
          type: "text" as const,
          required: true,
          constraints: {},
        },
        {
          name: "approved",
          type: "boolean" as const,
          required: true,
          constraints: {},
        },
        {
          name: "count",
          type: "integer" as const,
          required: true,
          constraints: {},
        },
        {
          name: "date",
          type: "date" as const,
          required: false,
          constraints: {},
        },
      ],
      repeats: schema.repeats,
    },
  };
  const older = { ...typed, id: old.id, number: old.number };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([]),
    versions: vi.fn().mockResolvedValue([typed, older]),
    version: vi.fn().mockResolvedValue(typed),
    createPlan: vi.fn().mockImplementation((_draftId, _etag, _key, request) =>
      Promise.resolve({
        ...plan,
        values: request.values,
        template_version_id: request.template_version_id,
      }),
    ),
  };
  const authorApi = { list: vi.fn().mockResolvedValue([author]) };
  render(
    <ComposerFillPanel
      authorApi={authorApi as unknown as AuthorDirectoryApi}
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  fireEvent.change(await screen.findByLabelText("Typed filling template"), {
    target: { value: template.id },
  });
  await screen.findByText(/Exact template version: 2/);
  fireEvent.change(screen.getByLabelText("Typed template version"), {
    target: { value: older.id },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "Alice" }));
  fireEvent.change(screen.getByLabelText("title"), {
    target: { value: "Review" },
  });
  fireEvent.change(screen.getByLabelText("approved"), {
    target: { value: "false" },
  });
  fireEvent.change(screen.getByLabelText("count"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("date"), {
    target: { value: "2026-09-24" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add findings row" }));
  fireEvent.change(screen.getByLabelText("findings[0].summary"), {
    target: { value: "Found" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save fill plan" }));
  await waitFor(() => expect(api.createPlan).toHaveBeenCalledOnce());
  expect(api.createPlan.mock.calls[0]![3]).toEqual({
    source_revision_id: "source-1",
    template_id: template.id,
    template_version_id: older.id,
    author_ids: [author.id],
    values: {
      title: "Review",
      approved: false,
      count: 3,
      date: "2026-09-24",
      findings: [{ summary: "Found" }],
    },
  });
});

test("fill plan preserves ambiguous answers as questions and requires a saved review", async () => {
  const pending = {
    ...plan,
    state: "pending" as const,
    values: { title: null },
    provenance: { title: { kind: "unresolved" as const } },
    questions: [
      { path: "title", text: "Which title?", reason: "ambiguous" as const },
    ],
  };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([pending]),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
    updatePlan: vi
      .fn()
      .mockImplementation((_draftId, _plan, values, provenance) =>
        Promise.resolve({
          ...pending,
          values,
          provenance,
          questions: values.title == null ? pending.questions : [],
        }),
      ),
  };
  render(
    <ComposerFillPanel
      authorApi={
        { list: vi.fn().mockResolvedValue([]) } as unknown as AuthorDirectoryApi
      }
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  expect(await screen.findByText(/Which title\? \(ambiguous\)/)).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Approve reviewed values" }),
  ).toBeDisabled();
  fireEvent.change(screen.getByLabelText("title"), {
    target: { value: "Chosen" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Save answers and edits" }),
  );
  await waitFor(() => expect(api.updatePlan).toHaveBeenCalledOnce());
  expect(api.updatePlan.mock.calls[0]![2]).toEqual({ title: "Chosen" });
  expect(api.updatePlan.mock.calls[0]![3]).toEqual({
    title: { kind: "human_edited" },
  });
  expect(
    await screen.findByText("No missing or ambiguous values remain."),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Approve reviewed values" }),
  ).toBeEnabled();
});

test("fill panel blocks plan creation without a source revision and shows revoked selections", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([]),
    versions: vi
      .fn()
      .mockRejectedValue(new ApiError(403, "FORBIDDEN", "private detail")),
    createPlan: vi.fn(),
  };
  render(
    <ComposerFillPanel
      authorApi={
        { list: vi.fn().mockResolvedValue([]) } as unknown as AuthorDirectoryApi
      }
      draft={{ ...draft, current_revision_id: null }}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId={null}
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  expect(
    await screen.findByText("Capture a source revision before filling."),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "Save fill plan" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Typed filling template"), {
    target: { value: template.id },
  });
  expect(
    await screen.findByText(
      "An author, template, or plan is no longer available. Refresh your selections.",
    ),
  ).toBeVisible();
  expect(screen.queryByText("private detail")).toBeNull();
  expect(api.createPlan).not.toHaveBeenCalled();
});

test("fill plan list authentication failure expires the session", async () => {
  const expire = vi.fn();
  const api = {
    list: vi.fn().mockResolvedValue([]),
    plans: vi
      .fn()
      .mockRejectedValue(new ApiError(401, "UNAUTHORIZED", "private detail")),
  };
  render(
    <ComposerFillPanel
      authorApi={
        { list: vi.fn().mockResolvedValue([]) } as unknown as AuthorDirectoryApi
      }
      draft={draft}
      expire={expire}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  expect(
    await screen.findByText("Your session ended. Please sign in again."),
  ).toBeVisible();
  expect(expire).toHaveBeenCalledOnce();
});

test("fill plan selector can start a new plan after resuming a published one", async () => {
  const published = {
    ...plan,
    state: "published" as const,
    result_revision_id: "filled-1",
  };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([published]),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
    createPlan: vi.fn().mockResolvedValue({ ...plan, state: "pending" }),
  };
  render(
    <ComposerFillPanel
      authorApi={
        {
          list: vi.fn().mockResolvedValue([author]),
        } as unknown as AuthorDirectoryApi
      }
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  expect(await screen.findByText(/Fill plan plan-1/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("Saved fill plans"), {
    target: { value: "" },
  });
  expect(screen.getByRole("button", { name: "Save fill plan" })).toBeEnabled();
  fireEvent.change(screen.getByLabelText("Typed filling template"), {
    target: { value: template.id },
  });
  await screen.findByText(/Exact template version/);
  fireEvent.click(screen.getByRole("button", { name: "Save fill plan" }));
  await waitFor(() => expect(api.createPlan).toHaveBeenCalledOnce());
});

test("ambiguous boolean input is retained as null in a new plan", async () => {
  const booleanVersion = {
    ...active,
    schema: {
      fields: [
        {
          name: "approved",
          type: "boolean" as const,
          required: true,
          constraints: {},
        },
      ],
      repeats: [],
    },
  };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([]),
    versions: vi.fn().mockResolvedValue([booleanVersion]),
    version: vi.fn().mockResolvedValue(booleanVersion),
    createPlan: vi.fn().mockResolvedValue({ ...plan, state: "pending" }),
  };
  render(
    <ComposerFillPanel
      authorApi={
        { list: vi.fn().mockResolvedValue([]) } as unknown as AuthorDirectoryApi
      }
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  fireEvent.change(await screen.findByLabelText("Typed filling template"), {
    target: { value: template.id },
  });
  fireEvent.change(await screen.findByLabelText("approved"), {
    target: { value: "ambiguous" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save fill plan" }));
  await waitFor(() => expect(api.createPlan).toHaveBeenCalledOnce());
  expect(api.createPlan.mock.calls[0]![3].values).toEqual({ approved: null });
});

test("regeneration reports revoked access without publishing a revision", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([]),
    plans: vi.fn().mockResolvedValue([]),
    regenerate: vi
      .fn()
      .mockRejectedValue(new ApiError(403, "FORBIDDEN", "private detail")),
  };
  const onPublished = vi.fn();
  render(
    <ComposerFillPanel
      authorApi={
        { list: vi.fn().mockResolvedValue([]) } as unknown as AuthorDirectoryApi
      }
      draft={draft}
      expire={vi.fn()}
      onPublished={onPublished}
      ownerId={owner.id}
      revisions={[
        {
          id: "filled-1",
          draft_id: draft.id,
          number: 2,
          operation: "fill",
          provenance: "human",
          model_identity: null,
          restored_from_revision_id: null,
          created_at: "2026-09-24T00:00:00Z",
        },
      ]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  fireEvent.change(screen.getByLabelText("Filled revision"), {
    target: { value: "filled-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Regenerate DOCX" }));
  expect(
    await screen.findByText(
      "An author, template, or plan is no longer available. Refresh your selections.",
    ),
  ).toBeVisible();
  expect(onPublished).not.toHaveBeenCalled();
  expect(screen.queryByText("private detail")).toBeNull();
});

test("a stale fill plan whose details were revoked is cleared safely", async () => {
  const pending = { ...plan, state: "pending" as const };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([pending]),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
    updatePlan: vi
      .fn()
      .mockRejectedValue(new ApiError(412, "STALE", "private detail")),
    getPlan: vi
      .fn()
      .mockRejectedValue(new ApiError(403, "FORBIDDEN", "private detail")),
  };
  render(
    <ComposerFillPanel
      authorApi={
        { list: vi.fn().mockResolvedValue([]) } as unknown as AuthorDirectoryApi
      }
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Save answers and edits" }),
  );
  expect(
    await screen.findByText(
      "The draft or plan changed. Reload the current version before retrying.",
    ),
  ).toBeVisible();
  expect(
    await screen.findByRole("button", { name: "Save fill plan" }),
  ).toBeEnabled();
  expect(screen.queryByText("private detail")).toBeNull();
});

test("the active template version loads by ID when it is outside the newest history page", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([]),
    versions: vi.fn().mockResolvedValue([old]),
    version: vi.fn().mockResolvedValue(active),
  };
  render(
    <ComposerFillPanel
      authorApi={
        { list: vi.fn().mockResolvedValue([]) } as unknown as AuthorDirectoryApi
      }
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  fireEvent.change(await screen.findByLabelText("Typed filling template"), {
    target: { value: template.id },
  });
  expect(await screen.findByText(/Exact template version: 2/)).toBeVisible();
  expect(api.version).toHaveBeenCalledWith(template.id, active.id);
});

test("server rejects a newly created fill plan without exposing a private error body", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([]),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
    createPlan: vi
      .fn()
      .mockRejectedValue(new ApiError(403, "FORBIDDEN", "private detail")),
  };
  render(
    <ComposerFillPanel
      authorApi={
        { list: vi.fn().mockResolvedValue([]) } as unknown as AuthorDirectoryApi
      }
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  fireEvent.change(await screen.findByLabelText("Typed filling template"), {
    target: { value: template.id },
  });
  await screen.findByText(/Exact template version/);
  fireEvent.click(screen.getByRole("button", { name: "Save fill plan" }));
  expect(
    await screen.findByText(
      "An author, template, or plan is no longer available. Refresh your selections.",
    ),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "Save fill plan" })).toBeEnabled();
  expect(screen.queryByText("private detail")).toBeNull();
});

test("fill plan retains cited provenance and permits manual ambiguity edits before approval", async () => {
  const typed = {
    ...active,
    schema: {
      fields: [
        {
          name: "title",
          type: "text" as const,
          required: true,
          constraints: {},
        },
        {
          name: "approved",
          type: "boolean" as const,
          required: true,
          constraints: {},
        },
        {
          name: "count",
          type: "integer" as const,
          required: false,
          constraints: {},
        },
      ],
      repeats: [],
    },
  };
  const pending = {
    ...plan,
    state: "pending" as const,
    values: { title: "Draft", approved: null, count: 2 },
    provenance: {
      title: { kind: "cited" as const, source_reference: "source:1" },
    },
    questions: [
      {
        path: "approved",
        text: "Was it approved?",
        reason: "ambiguous" as const,
      },
    ],
  };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([pending]),
    versions: vi.fn().mockResolvedValue([typed]),
    version: vi.fn().mockResolvedValue(typed),
    updatePlan: vi
      .fn()
      .mockImplementation((_draftId, _plan, values, provenance) =>
        Promise.resolve({ ...pending, values, provenance, questions: [] }),
      ),
  };
  render(
    <ComposerFillPanel
      authorApi={
        { list: vi.fn().mockResolvedValue([]) } as unknown as AuthorDirectoryApi
      }
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  expect(await screen.findByText(/title: cited · source:1/)).toBeVisible();
  expect(screen.getByLabelText("approved")).toHaveValue("ambiguous");
  fireEvent.change(screen.getByLabelText("approved"), {
    target: { value: "true" },
  });
  fireEvent.change(screen.getByLabelText("count"), { target: { value: "" } });
  fireEvent.click(screen.getAllByRole("checkbox", { name: "Ambiguous" })[1]!);
  expect(
    screen.getByRole("button", { name: "Approve reviewed values" }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole("button", { name: "Save answers and edits" }),
  );
  await waitFor(() => expect(api.updatePlan).toHaveBeenCalledOnce());
  expect(api.updatePlan.mock.calls[0]![2]).toEqual({
    title: "Draft",
    approved: true,
    count: null,
  });
  expect(api.updatePlan.mock.calls[0]![3]).toEqual({
    title: { kind: "cited", source_reference: "source:1" },
    approved: { kind: "human_edited" },
    count: { kind: "human_edited" },
  });
});

test("a saved fill plan whose typed version was revoked cannot be approved", async () => {
  const pending = { ...plan, state: "pending" as const };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([pending]),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi
      .fn()
      .mockRejectedValue(new ApiError(403, "FORBIDDEN", "private detail")),
    approve: vi.fn(),
  };
  render(
    <ComposerFillPanel
      authorApi={
        { list: vi.fn().mockResolvedValue([]) } as unknown as AuthorDirectoryApi
      }
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  expect(
    await screen.findByText(
      "An author, template, or plan is no longer available. Refresh your selections.",
    ),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Approve reviewed values" }),
  ).toBeDisabled();
  expect(api.approve).not.toHaveBeenCalled();
  expect(screen.queryByText("private detail")).toBeNull();
});

test("an explicitly saved plan resumes ahead of another pending plan", async () => {
  const saved = { ...plan, id: "plan-saved", state: "approved" as const };
  const pending = { ...plan, id: "plan-pending", state: "pending" as const };
  const key = `composer:fill-plan:${owner.id}:${draft.id}`;
  sessionStorage.setItem(key, saved.id);
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([pending, saved]),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
  };
  try {
    render(
      <ComposerFillPanel
        authorApi={
          {
            list: vi.fn().mockResolvedValue([]),
          } as unknown as AuthorDirectoryApi
        }
        draft={draft}
        expire={vi.fn()}
        onPublished={vi.fn()}
        ownerId={owner.id}
        revisions={[]}
        sourceRevisionId="source-1"
        templateApi={api as unknown as FillTemplateApi}
      />,
    );
    expect(await screen.findByText(/Fill plan plan-saved/)).toBeVisible();
    expect(screen.getByLabelText("Saved fill plans")).toHaveValue(saved.id);
  } finally {
    sessionStorage.removeItem(key);
  }
});

test("a server validation error leaves the reviewed fill plan pending", async () => {
  const pending = { ...plan, state: "pending" as const };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([pending]),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
    updatePlan: vi
      .fn()
      .mockRejectedValue(
        new ApiError(422, "INVALID_VALUE", "Check the value."),
      ),
  };
  render(
    <ComposerFillPanel
      authorApi={
        { list: vi.fn().mockResolvedValue([]) } as unknown as AuthorDirectoryApi
      }
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Save answers and edits" }),
  );
  expect(await screen.findByText("Check the value.")).toBeVisible();
  expect(screen.getByText(/Fill plan plan-1 · pending/)).toBeVisible();
});

test("fill selectors can choose authorized templates and authors beyond the first page", async () => {
  const firstTemplates = Array.from({ length: 100 }, (_, at) => ({
    ...template,
    id: `page-template-${at}`,
    name: `Template ${at}`,
  }));
  const firstAuthors = Array.from({ length: 100 }, (_, at) => ({
    ...author,
    id: `page-author-${at}`,
    name: `Author ${at}`,
  }));
  const api = {
    list: vi
      .fn()
      .mockImplementation((offset: number) =>
        Promise.resolve(offset === 0 ? firstTemplates : [template]),
      ),
    plans: vi.fn().mockResolvedValue([]),
    versions: vi.fn().mockResolvedValue([active]),
    version: vi.fn().mockResolvedValue(active),
    createPlan: vi.fn().mockResolvedValue(plan),
  };
  const authorApi = {
    list: vi
      .fn()
      .mockImplementation((offset: number) =>
        Promise.resolve(offset === 0 ? firstAuthors : [author]),
      ),
  };
  render(
    <ComposerFillPanel
      authorApi={authorApi as unknown as AuthorDirectoryApi}
      draft={draft}
      expire={vi.fn()}
      onPublished={vi.fn()}
      ownerId={owner.id}
      revisions={[]}
      sourceRevisionId="source-1"
      templateApi={api as unknown as FillTemplateApi}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Load more typed templates" }),
  );
  await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(100));
  const moreAuthorsButton = screen.getByRole("button", {
    name: "Load more fill authors",
  });
  await waitFor(() => expect(moreAuthorsButton).toBeEnabled());
  fireEvent.click(moreAuthorsButton);
  expect(api.list).toHaveBeenLastCalledWith(100);
  expect(authorApi.list).toHaveBeenLastCalledWith(100);
  fireEvent.change(screen.getByLabelText("Typed filling template"), {
    target: { value: template.id },
  });
  fireEvent.click(await screen.findByRole("checkbox", { name: author.name }));
  await screen.findByText(/Exact template version/);
  fireEvent.click(screen.getByRole("button", { name: "Save fill plan" }));
  await waitFor(() =>
    expect(api.createPlan).toHaveBeenCalledWith(
      draft.id,
      draft.etag,
      expect.any(String),
      expect.objectContaining({
        template_id: template.id,
        author_ids: [author.id],
      }),
    ),
  );
});
