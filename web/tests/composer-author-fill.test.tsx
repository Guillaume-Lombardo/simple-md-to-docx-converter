import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApiError } from "../src/api/transport";
import type { AuthorDirectoryApi } from "../src/composer/author-directory-api";
import { AuthorDirectory } from "../src/composer/author-directory";
import type { FillTemplateApi } from "../src/composer/fill-template-api";
import { FillTemplatesWorkspace } from "../src/composer/fill-templates";
import { ComposerFillPanel } from "../src/composer/fill-panel";

const user = {
  active: true,
  effective_idle_minutes: 30,
  id: "user-1",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};
const author = {
  id: "author-1",
  owner_id: user.id,
  name: "Alice Author",
  fields: {
    organization: { value: "Original", provenance: "supplied" },
  },
  version: 2,
  shared_with: [] as string[],
};
const schema = {
  fields: [
    {
      name: "author_name",
      type: "text" as const,
      required: true,
      constraints: {},
    },
  ],
  repeats: [
    {
      name: "findings",
      min_items: 0,
      max_items: 2,
      fields: [
        {
          name: "title",
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
  owner_id: user.id,
  name: "Structured report",
  version: 1,
  etag: '"template-1"',
  active_version_id: "version-1",
  shared_with: [] as string[],
  created_at: "2026-09-24T00:00:00Z",
  updated_at: "2026-09-24T00:00:00Z",
};
const version = {
  id: "version-1",
  template_id: template.id,
  number: 1,
  schema_version: 1,
  schema,
  schema_sha256: "a".repeat(64),
  docx_sha256: "b".repeat(64),
  size: 100,
  created_at: "2026-09-24T00:00:00Z",
};
const draft = {
  id: "draft-1",
  title: "Draft",
  content: "# Draft",
  version: 2,
  current_revision_id: "source-1",
  source_kind: "upload",
  source_media_type: "text/markdown",
  created_at: "2026-09-24T00:00:00Z",
  updated_at: "2026-09-24T00:00:00Z",
  etag: '"draft-2"',
};

test("owner edits structured facts and explicitly grants then revokes one account", async () => {
  const current = { ...author };
  const api = {
    list: vi.fn().mockResolvedValue([current]),
    get: vi.fn().mockImplementation(() =>
      Promise.resolve({
        data: { ...current },
        etag: `"author-${current.version}"`,
      }),
    ),
    create: vi.fn(),
    update: vi.fn().mockImplementation((_id, _etag, input) => {
      Object.assign(current, input, { version: 3 });
      return Promise.resolve({ data: { ...current }, etag: '"author-3"' });
    }),
    grant: vi.fn().mockImplementation(() => {
      current.shared_with = ["user-2"];
      current.version += 1;
      return Promise.resolve({ data: current });
    }),
    revoke: vi.fn().mockImplementation(() => {
      current.shared_with = [];
      current.version += 1;
      return Promise.resolve({ data: current });
    }),
  };
  render(
    <AuthorDirectory
      api={api as unknown as AuthorDirectoryApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  await waitFor(() => expect(api.list).toHaveBeenCalled());
  fireEvent.click(
    await screen.findByRole("button", { name: "Alice Author (mine)" }),
  );
  fireEvent.change(await screen.findByDisplayValue("Original"), {
    target: { value: "Corrected" },
  });
  fireEvent.change(screen.getByLabelText("Provenance"), {
    target: { value: "human_edited" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save author" }));
  await waitFor(() =>
    expect(api.update).toHaveBeenCalledWith(
      "author-1",
      '"author-2"',
      expect.objectContaining({
        fields: {
          organization: expect.objectContaining({
            value: "Corrected",
            provenance: "human_edited",
          }),
        },
      }),
    ),
  );
  fireEvent.change(screen.getByLabelText("Account ID"), {
    target: { value: "user-2" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Grant access" }));
  await waitFor(() =>
    expect(api.grant).toHaveBeenCalledWith("author-1", "user-2", '"author-3"'),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Revoke user-2" }));
  await waitFor(() =>
    expect(api.revoke).toHaveBeenCalledWith("author-1", "user-2", '"author-4"'),
  );
});

test("revoked author access is displayed as safe stale state", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([author]),
    get: vi
      .fn()
      .mockRejectedValue(new ApiError(403, "FORBIDDEN", "private detail")),
  };
  render(
    <AuthorDirectory
      api={api as unknown as AuthorDirectoryApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Alice Author (mine)" }),
  );
  expect(
    await screen.findByText("This author entry is no longer available to you."),
  ).toBeVisible();
  expect(screen.queryByText("private detail")).toBeNull();
});

test("unknown author facts keep value and provenance coherent in both directions", async () => {
  const current = { ...author, fields: { ...author.fields } };
  const api = {
    list: vi.fn().mockResolvedValue([current]),
    get: vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve({ data: current, etag: '"author-2"' }),
      ),
    update: vi.fn().mockImplementation((_id, _etag, input) => {
      current.fields = input.fields;
      return Promise.resolve({ data: current, etag: '"author-3"' });
    }),
  };
  render(
    <AuthorDirectory
      api={api as unknown as AuthorDirectoryApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Alice Author (mine)" }),
  );
  await screen.findByDisplayValue("Original");
  const unknown = screen.getByRole("checkbox", {
    name: "Unknown or unresolved",
  });
  fireEvent.click(unknown);
  expect(screen.getByLabelText("Provenance")).toHaveValue("unresolved");
  fireEvent.click(unknown);
  expect(screen.getByLabelText("Value")).toHaveValue("Original");
  expect(screen.getByLabelText("Provenance")).toHaveValue("supplied");
  fireEvent.click(unknown);
  fireEvent.click(screen.getByRole("button", { name: "Save author" }));
  await waitFor(() => expect(api.update).toHaveBeenCalledOnce());
  await screen.findByRole("status");
  expect(api.update.mock.calls[0]![2].fields.organization).toMatchObject({
    value: null,
    provenance: "unresolved",
  });
  fireEvent.click(unknown);
  expect(screen.getByLabelText("Value")).toHaveValue("");
  expect(screen.getByLabelText("Provenance")).toHaveValue("supplied");
  fireEvent.click(screen.getByRole("button", { name: "Save author" }));
  expect(api.update).toHaveBeenCalledOnce();
  expect(
    await screen.findByText(
      "Enter a value or mark the author field unresolved.",
    ),
  ).toBeVisible();
  fireEvent.change(screen.getByLabelText("Value"), {
    target: { value: "Restored" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save author" }));
  await waitFor(() => expect(api.update).toHaveBeenCalledTimes(2));
  expect(api.update.mock.calls[1]![2].fields.organization).toMatchObject({
    value: "Restored",
    provenance: "supplied",
  });
});

test("typed template authoring keeps named fields and repeat bounds in its multipart schema", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([]),
    create: vi.fn().mockResolvedValue({ data: template }),
    get: vi.fn().mockResolvedValue({ data: template }),
    version: vi.fn().mockResolvedValue(version),
    versions: vi.fn().mockResolvedValue([version]),
  };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  await waitFor(() => expect(api.list).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Template name"), {
    target: { value: "Structured report" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add field" }));
  fireEvent.change(screen.getByLabelText("Field name"), {
    target: { value: "author_name" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Add repeatable section" }),
  );
  fireEvent.change(screen.getByLabelText("Repeat name"), {
    target: { value: "findings" },
  });
  const names = screen.getAllByLabelText("Field name");
  fireEvent.change(names[1]!, { target: { value: "title" } });
  fireEvent.change(screen.getByLabelText("Maximum rows"), {
    target: { value: "2" },
  });
  fireEvent.change(screen.getByLabelText("DOCX file"), {
    target: { files: [new File(["DOCX"], "report.docx")] },
  });
  fireEvent.submit(
    screen
      .getByRole("button", { name: "Create typed template" })
      .closest("form")!,
  );
  await waitFor(() =>
    expect(api.create).toHaveBeenCalledWith(
      "Structured report",
      expect.objectContaining({
        fields: [
          expect.objectContaining({
            name: "author_name",
            type: "text",
            required: true,
          }),
        ],
        repeats: [
          expect.objectContaining({
            name: "findings",
            max_items: 2,
            fields: [expect.objectContaining({ name: "title" })],
          }),
        ],
      }),
      expect.any(File),
    ),
  );
});

test("removing an earlier typed field keeps the next field's visible JSON and saved schema", async () => {
  const api = {
    list: vi.fn().mockResolvedValue([]),
    create: vi.fn().mockResolvedValue({ data: template }),
    get: vi.fn().mockResolvedValue({ data: template }),
    version: vi.fn().mockResolvedValue(version),
    versions: vi.fn().mockResolvedValue([version]),
  };
  render(
    <FillTemplatesWorkspace
      api={api as unknown as FillTemplateApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  await waitFor(() => expect(api.list).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Template name"), {
    target: { value: "Structured report" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add field" }));
  fireEvent.click(screen.getByRole("button", { name: "Add field" }));
  const names = screen.getAllByLabelText("Field name");
  fireEvent.change(names[0]!, { target: { value: "discarded" } });
  fireEvent.change(names[1]!, { target: { value: "retained" } });
  const constraints = screen.getAllByLabelText("Constraints (JSON object)");
  fireEvent.change(constraints[0]!, { target: { value: '{"max_length":4}' } });
  fireEvent.change(constraints[1]!, { target: { value: '{"max_length":12}' } });
  const defaults = screen.getAllByLabelText(
    "Explicit default (JSON value; blank means none)",
  );
  fireEvent.change(defaults[0]!, { target: { value: '"old"' } });
  fireEvent.change(defaults[1]!, { target: { value: '"kept"' } });
  fireEvent.change(constraints[1]!, { target: { value: '{"max_length":' } });
  fireEvent.click(screen.getByRole("button", { name: "Remove field 1" }));
  expect(screen.getByLabelText("Constraints (JSON object)")).toHaveValue(
    '{"max_length":',
  );
  expect(screen.getByLabelText("Constraints (JSON object)")).toBeInvalid();
  fireEvent.change(screen.getByLabelText("Constraints (JSON object)"), {
    target: { value: '{"max_length":12}' },
  });
  expect(
    screen.getByLabelText("Explicit default (JSON value; blank means none)"),
  ).toHaveValue('"kept"');
  fireEvent.click(
    screen.getByRole("button", { name: "Add repeatable section" }),
  );
  fireEvent.change(screen.getByLabelText("Repeat name"), {
    target: { value: "findings" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add repeat field" }));
  const repeatNames = screen.getAllByLabelText("Field name");
  fireEvent.change(repeatNames[1]!, { target: { value: "discarded_child" } });
  fireEvent.change(repeatNames[2]!, { target: { value: "retained_child" } });
  const repeatConstraints = screen.getAllByLabelText(
    "Constraints (JSON object)",
  );
  fireEvent.change(repeatConstraints[1]!, {
    target: { value: '{"max_length":5}' },
  });
  fireEvent.change(repeatConstraints[2]!, {
    target: { value: '{"max_length":20}' },
  });
  const repeatDefaults = screen.getAllByLabelText(
    "Explicit default (JSON value; blank means none)",
  );
  fireEvent.change(repeatDefaults[1]!, { target: { value: '"before"' } });
  fireEvent.change(repeatDefaults[2]!, { target: { value: '"after"' } });
  fireEvent.click(
    screen.getByRole("button", { name: "Remove repeat 1 field 1" }),
  );
  expect(screen.getAllByLabelText("Constraints (JSON object)")[1]).toHaveValue(
    '{"max_length":20}',
  );
  expect(
    screen.getAllByLabelText(
      "Explicit default (JSON value; blank means none)",
    )[1],
  ).toHaveValue('"after"');
  fireEvent.change(screen.getByLabelText("DOCX file"), {
    target: { files: [new File(["DOCX"], "report.docx")] },
  });
  fireEvent.submit(
    screen
      .getByRole("button", { name: "Create typed template" })
      .closest("form")!,
  );
  await waitFor(() => expect(api.create).toHaveBeenCalledOnce());
  expect(api.create.mock.calls[0]![1].fields).toEqual([
    {
      name: "retained",
      type: "text",
      required: true,
      constraints: { max_length: 12 },
      default: "kept",
    },
  ]);
  expect(api.create.mock.calls[0]![1].repeats[0].fields).toEqual([
    {
      name: "retained_child",
      type: "text",
      required: true,
      constraints: { max_length: 20 },
      default: "after",
    },
  ]);
});

test("fill plan asks missing facts, saves a manual answer, approves, publishes, and regenerates", async () => {
  const pending = {
    id: "plan-1",
    draft_id: draft.id,
    source_revision_id: "source-1",
    template_id: template.id,
    template_version_id: version.id,
    author_refs: [],
    version: 1,
    etag: '"plan-1"',
    values: {},
    provenance: {},
    questions: [
      {
        path: "author_name",
        text: "Who is the author?",
        reason: "missing" as const,
      },
    ],
    state: "pending" as const,
    result_revision_id: null,
  };
  const answered = {
    ...pending,
    values: { author_name: "Alice" },
    provenance: { author_name: { kind: "human_edited" as const } },
    questions: [],
    etag: '"plan-2"',
  };
  const approved = {
    ...answered,
    state: "approved" as const,
    etag: '"plan-3"',
  };
  const api = {
    list: vi.fn().mockResolvedValue([template]),
    plans: vi.fn().mockResolvedValue([]),
    versions: vi.fn().mockResolvedValue([version]),
    version: vi.fn().mockResolvedValue(version),
    createPlan: vi.fn().mockResolvedValue(pending),
    updatePlan: vi.fn().mockResolvedValue(answered),
    approve: vi.fn().mockResolvedValue(approved),
    publish: vi
      .fn()
      .mockResolvedValue({ id: "filled-1", number: 2, draft_id: draft.id }),
    getPlan: vi.fn().mockResolvedValue({
      ...approved,
      state: "published",
      result_revision_id: "filled-1",
    }),
    regenerate: vi
      .fn()
      .mockResolvedValue({ id: "filled-2", number: 3, draft_id: draft.id }),
  };
  const authorApi = { list: vi.fn().mockResolvedValue([author]) };
  const onPublished = vi.fn();
  render(
    <ComposerFillPanel
      authorApi={authorApi as unknown as AuthorDirectoryApi}
      draft={draft}
      expire={vi.fn()}
      onPublished={onPublished}
      ownerId={user.id}
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
  fireEvent.change(await screen.findByLabelText("Typed filling template"), {
    target: { value: template.id },
  });
  await screen.findByText(/Exact template version:/);
  fireEvent.click(screen.getByRole("button", { name: "Save fill plan" }));
  expect(await screen.findByText(/Who is the author/)).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Approve reviewed values" }),
  ).toBeDisabled();
  fireEvent.change(screen.getByLabelText("author_name"), {
    target: { value: "Alice" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Save answers and edits" }),
  );
  await waitFor(() =>
    expect(api.updatePlan).toHaveBeenCalledWith(
      draft.id,
      pending,
      { author_name: "Alice" },
      { author_name: { kind: "human_edited" } },
      expect.any(String),
    ),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Approve reviewed values" }),
  );
  await waitFor(() =>
    expect(api.approve).toHaveBeenCalledWith(
      draft.id,
      answered,
      expect.any(String),
    ),
  );
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Fill DOCX from approved values",
    }),
  );
  await waitFor(() => expect(onPublished).toHaveBeenCalledWith("filled-1"));
  fireEvent.change(screen.getByLabelText("Filled revision"), {
    target: { value: "filled-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Regenerate DOCX" }));
  await waitFor(() => expect(onPublished).toHaveBeenCalledWith("filled-2"));
});
