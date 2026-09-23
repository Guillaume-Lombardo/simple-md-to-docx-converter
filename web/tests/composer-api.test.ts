import { ComposerConnectionsApi } from "../src/composer/api";
import type { ApiTransport } from "../src/api/transport";

function setup() {
  const transport = { json: vi.fn(), jsonWithMetadata: vi.fn() };
  return {
    api: new ComposerConnectionsApi(transport as unknown as ApiTransport),
    transport,
  };
}

test("connection lifecycle uses exact API paths, CSRF, and revision preconditions", async () => {
  const { api, transport } = setup();
  transport.json.mockResolvedValue({
    connections: [],
    limit: 50,
    models: [],
    offset: 0,
  });
  transport.jsonWithMetadata.mockResolvedValue({ data: {}, etag: '"next"' });
  const input = {
    allowed_user_ids: [],
    enabled: true,
    endpoint: "https://llm.example/v1",
    identity_mode: "shared" as const,
    name: "Primary",
    permitted_models: ["small-model"],
    scope: "personal" as const,
    selected_model: "small-model",
  };
  await api.connections();
  await api.create(input);
  await api.update("id", '"revision"', { enabled: false });
  await api.credentials("id", '"revision"', { api_key: "write-only" });
  await api.revokeCredentials("id", '"revision"');
  await api.test("id");
  await api.models("id");
  await api.delete("id", '"revision"');

  expect(transport.jsonWithMetadata.mock.calls.map(([path]) => path)).toEqual([
    "/api/v1/composer/connections",
    "/api/v1/composer/connections/id",
    "/api/v1/composer/connections/id/credentials",
    "/api/v1/composer/connections/id/credentials",
    "/api/v1/composer/connections/id",
  ]);
  expect(
    transport.jsonWithMetadata.mock.calls
      .slice(0, 5)
      .every((call) => call[2].csrf),
  ).toBe(true);
  expect(transport.jsonWithMetadata.mock.calls[1]![2]).toMatchObject({
    etag: '"revision"',
    method: "PATCH",
  });
  expect(transport.jsonWithMetadata.mock.calls[2]![2].etag).toBe('"revision"');
  expect(transport.jsonWithMetadata.mock.calls[3]![2].etag).toBe('"revision"');
  expect(JSON.parse(transport.jsonWithMetadata.mock.calls[2]![2].body)).toEqual(
    {
      api_key: "write-only",
    },
  );
  expect(JSON.parse(transport.jsonWithMetadata.mock.calls[3]![2].body)).toEqual(
    {
      revoke: true,
    },
  );
  expect(transport.json.mock.calls.map(([path]) => path)).toEqual([
    "/api/v1/composer/connections?offset=0&limit=50",
    "/api/v1/composer/connections/id/test",
    "/api/v1/composer/connections/id/models",
  ]);
  expect(transport.jsonWithMetadata.mock.calls[4]![2]).toMatchObject({
    csrf: true,
    etag: '"revision"',
    method: "DELETE",
  });
  expect(JSON.parse(transport.json.mock.calls[1]![2].body)).toEqual({});
});

test("personal permission methods preserve admin list, exact user path, CSRF, and ETag", async () => {
  const { api, transport } = setup();
  transport.json.mockResolvedValue({ limit: 50, offset: 0, permissions: [] });
  transport.jsonWithMetadata.mockResolvedValue({ data: {}, etag: '"next"' });
  await api.personalPermissions();
  await api.personalPermission("user-id");
  await api.updatePersonalPermission("user-id", true, '"permission-1"');
  expect(transport.json.mock.calls[0]![0]).toBe(
    "/api/v1/composer/personal-permissions?offset=0&limit=50",
  );
  expect(transport.jsonWithMetadata.mock.calls[0]![0]).toBe(
    "/api/v1/composer/personal-permissions/user-id",
  );
  expect(transport.jsonWithMetadata.mock.calls[1]![0]).toBe(
    "/api/v1/composer/personal-permissions/user-id",
  );
  expect(transport.jsonWithMetadata.mock.calls[1]![2]).toMatchObject({
    csrf: true,
    etag: '"permission-1"',
    method: "PUT",
  });
  expect(JSON.parse(transport.jsonWithMetadata.mock.calls[1]![2].body)).toEqual(
    {
      allowed: true,
    },
  );
});
