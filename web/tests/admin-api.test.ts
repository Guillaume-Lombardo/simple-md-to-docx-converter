import { AdministrationApi } from "../src/admin/api";
import type { ApiTransport } from "../src/api/transport";

function setup() {
  const transport = {
    json: vi.fn(),
    jsonWithMetadata: vi.fn(),
    multipart: vi.fn(),
  };
  return {
    api: new AdministrationApi(transport as unknown as ApiTransport),
    transport,
  };
}

test("template library follows every page and carries encoded filters", async () => {
  const { api, transport } = setup();
  transport.json
    .mockResolvedValueOnce({
      items: [{ id: "one" }],
      limit: 100,
      offset: 0,
      total: 2,
    })
    .mockResolvedValueOnce({
      items: [{ id: "two" }],
      limit: 100,
      offset: 1,
      total: 2,
    });
  await expect(
    api.allTemplates({
      description: "body & more",
      name: "name/value",
      ownerId: "00000000-0000-4000-8000-000000000001",
      status: "archived",
    }),
  ).resolves.toEqual([{ id: "one" }, { id: "two" }]);
  expect(transport.json.mock.calls[0]![0]).toContain("name=name%2Fvalue");
  expect(transport.json.mock.calls[0]![0]).toContain(
    "description=body+%26+more",
  );
  expect(transport.json.mock.calls[1]![0]).toContain("offset=1");
});

test("template library omits absent filters and stops on an empty page", async () => {
  const { api, transport } = setup();
  transport.json.mockResolvedValue({
    items: [],
    limit: 100,
    offset: 0,
    total: 3,
  });
  await expect(api.allTemplates({})).resolves.toEqual([]);
  const path = transport.json.mock.calls[0]![0] as string;
  expect(path).toBe("/api/v1/templates?limit=100&offset=0");
});

test("template creation and replacement preserve expected-font semantics", async () => {
  const { api, transport } = setup();
  transport.multipart.mockResolvedValue({});
  transport.jsonWithMetadata.mockResolvedValue({ data: {}, etag: '"next"' });
  const file = new File(["docx"], "template.docx");
  await api.create({
    content: file,
    description: "Description",
    expectedFonts: " A, B ",
    name: "Name",
  });
  const createForm = transport.multipart.mock.calls[0]![1] as FormData;
  expect(createForm.getAll("expected_fonts")).toEqual(["A", "B"]);
  expect(createForm.get("content")).toBe(file);
  await api.replace("template-id", '"etag"', file, "  ");
  const replaceCall = transport.jsonWithMetadata.mock.calls[0]!;
  expect((replaceCall[2].body as FormData).getAll("expected_fonts")).toEqual([
    "",
  ]);
  expect(replaceCall[2]).toMatchObject({
    csrf: true,
    etag: '"etag"',
    method: "PUT",
  });
});

test("template lifecycle methods preserve CSRF, If-Match, and exact paths", async () => {
  const { api, transport } = setup();
  transport.json.mockResolvedValue(undefined);
  transport.jsonWithMetadata.mockResolvedValue({ data: {}, etag: '"next"' });
  await api.template("id");
  await api.versions("id");
  await api.updateMetadata("id", '"e"', "N", "D");
  await api.restore("id", "version", '"e"');
  await api.archive("id", '"e"');
  await api.delete("id", '"e"');
  await api.setPreferred("id");
  await api.clearPreferred();
  await api.setFallback("id");
  expect(transport.jsonWithMetadata.mock.calls.map(([path]) => path)).toEqual([
    "/api/v1/templates/id",
    "/api/v1/templates/id",
    "/api/v1/templates/id/versions/version/restore",
    "/api/v1/templates/id/archive",
  ]);
  expect(transport.json.mock.calls.map(([path]) => path)).toEqual([
    "/api/v1/templates/id/versions",
    "/api/v1/templates/id",
    "/api/v1/templates/id/preferred",
    "/api/v1/template-preference",
    "/api/v1/templates/id/system-fallback",
  ]);
});

test("user methods send exact safe JSON mutations", async () => {
  const { api, transport } = setup();
  transport.json.mockResolvedValue(undefined);
  await api.users();
  await api.createUser("Alice", "temporary", true);
  await api.setActive("id", false);
  await api.resetPassword("id", "replacement", true);
  await api.setPasswordChangeRequired("id", false);
  expect(transport.json.mock.calls.map(([path]) => path)).toEqual([
    "/api/v1/admin/users",
    "/api/v1/admin/users",
    "/api/v1/admin/users/id/active",
    "/api/v1/admin/users/id/password",
    "/api/v1/admin/users/id/password-change-required",
  ]);
  expect(JSON.parse(transport.json.mock.calls[1]![2].body)).toEqual({
    password: "temporary",
    password_change_required: true,
    username: "Alice",
  });
  expect(transport.json.mock.calls.slice(1).every((call) => call[2].csrf)).toBe(
    true,
  );
});

test("session policy methods require metadata, CSRF, and the current ETag", async () => {
  const { api, transport } = setup();
  transport.jsonWithMetadata.mockResolvedValue({
    data: {},
    etag: '"policy-8"',
  });
  await api.sessionPolicy();
  await api.updateSessionPolicy('"policy-8"', 26, 11);
  expect(transport.jsonWithMetadata.mock.calls[0]).toEqual([
    "/api/v1/admin/session-policy",
    expect.anything(),
    { signal: undefined },
  ]);
  expect(transport.jsonWithMetadata.mock.calls[1]![0]).toBe(
    "/api/v1/admin/session-policy",
  );
  expect(transport.jsonWithMetadata.mock.calls[1]![2]).toMatchObject({
    csrf: true,
    etag: '"policy-8"',
    method: "PUT",
  });
  expect(JSON.parse(transport.jsonWithMetadata.mock.calls[1]![2].body)).toEqual(
    {
      admin_idle_minutes: 11,
      user_idle_minutes: 26,
    },
  );
});
