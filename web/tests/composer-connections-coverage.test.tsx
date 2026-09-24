import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ApiError } from "../src/api/transport";
import type { ComposerConnectionsApi } from "../src/composer/api";
import { ComposerConnectionsWorkspace } from "../src/composer/connections";

const user = {
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};
const admin = { ...user, role: "admin" as const, username: "Admin" };
const first = {
  allowed_user_ids: [],
  authorized: true,
  client_certificate_present: false,
  credential_present: true,
  enabled: true,
  endpoint: "https://model.example/v1",
  etag: '"connection-1"',
  id: "00000000-0000-4000-8000-000000000100",
  identity_mode: "individual" as const,
  internal_ca_present: false,
  name: "Private connection",
  permitted_models: ["model-small"],
  scope: "personal" as const,
  selected_model: "model-small",
  status: "ready" as const,
  status_message: null,
};

function capabilities(overrides: Record<string, unknown> = {}) {
  return {
    instance_connections_manageable: false,
    maximum_upload_bytes: 1024,
    maximum_output_tokens: 2048,
    personal_connections_allowed: true,
    status: "ready",
    status_message: null,
    ...overrides,
  };
}

function api(overrides: Record<string, unknown> = {}) {
  return {
    capabilities: vi.fn().mockResolvedValue(capabilities()),
    connections: vi.fn().mockResolvedValue([first]),
    create: vi.fn(),
    credentials: vi.fn(),
    delete: vi.fn(),
    models: vi.fn().mockResolvedValue(["model-small"]),
    personalPermissions: vi.fn().mockResolvedValue([]),
    revokeCredentials: vi.fn(),
    test: vi.fn().mockResolvedValue({ status: "ready", status_message: null }),
    update: vi.fn(),
    updatePersonalPermission: vi.fn(),
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

test("a forbidden model operation retains the session, while a 401 expires it", async () => {
  const expire = vi.fn();
  const service = api({
    models: vi
      .fn()
      .mockRejectedValue(
        new ApiError(403, "FORBIDDEN", "Model use is not granted."),
      ),
    test: vi
      .fn()
      .mockRejectedValue(
        new ApiError(401, "SESSION_EXPIRED", "Your session ended."),
      ),
  });
  render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={expire}
      user={user}
    />,
  );
  const card = (await screen.findByText("Private connection")).closest(
    "article",
  )!;
  fireEvent.click(
    within(card).getByRole("button", { name: "Discover models" }),
  );
  expect(await screen.findByText("Model use is not granted.")).toBeVisible();
  expect(expire).not.toHaveBeenCalled();
  expect(card).toBeVisible();
  expect(service.update).not.toHaveBeenCalled();

  fireEvent.click(
    within(card).getByRole("button", { name: "Test connection" }),
  );
  expect(await screen.findByText("Your session ended.")).toBeVisible();
  expect(expire).toHaveBeenCalledOnce();
});

test("disable, credential revocation, and deletion use the latest owner revision", async () => {
  const disabled = {
    ...first,
    enabled: false,
    etag: '"connection-2"',
    status: "disabled" as const,
  };
  const revoked = {
    ...disabled,
    credential_present: false,
    etag: '"connection-3"',
  };
  const service = api({
    connections: vi
      .fn()
      .mockResolvedValueOnce([first])
      .mockResolvedValueOnce([disabled])
      .mockResolvedValueOnce([revoked])
      .mockResolvedValueOnce([]),
    update: vi.fn().mockResolvedValue({ data: disabled }),
    revokeCredentials: vi.fn().mockResolvedValue({ data: revoked }),
    delete: vi.fn().mockResolvedValue(undefined),
  });
  render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  const card = (await screen.findByText("Private connection")).closest(
    "article",
  )!;
  fireEvent.click(within(card).getByRole("button", { name: "Disable" }));
  await waitFor(() =>
    expect(service.update).toHaveBeenCalledWith(
      first.id,
      first.etag,
      { enabled: false },
      expect.any(AbortSignal),
    ),
  );
  await screen.findByRole("button", { name: "Enable" });
  expect(
    screen.getByRole("button", { name: "Discover models" }),
  ).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Test connection" }),
  ).toBeDisabled();

  fireEvent.click(screen.getByText("Rotate or revoke credentials"));
  fireEvent.click(screen.getByRole("button", { name: "Revoke credentials" }));
  await waitFor(() =>
    expect(service.revokeCredentials).toHaveBeenCalledWith(
      first.id,
      disabled.etag,
      expect.any(AbortSignal),
    ),
  );
  await waitFor(() =>
    expect(screen.getAllByText("Missing").length).toBeGreaterThan(0),
  );

  fireEvent.click(screen.getByRole("button", { name: "Revoke connection" }));
  await waitFor(() =>
    expect(service.delete).toHaveBeenCalledWith(
      first.id,
      revoked.etag,
      expect.any(AbortSignal),
    ),
  );
  expect(
    await screen.findByText(
      "No authorized connections are visible to this account.",
    ),
  ).toBeVisible();
});

test("a late response after the owner view unmounts cannot replace the next owner's connections", async () => {
  const late = deferred<(typeof first)[]>();
  const adminConnection = {
    ...first,
    id: "00000000-0000-4000-8000-000000000200",
    name: "Admin connection",
    scope: "instance" as const,
  };
  const connections = vi
    .fn()
    .mockReturnValueOnce(late.promise)
    .mockResolvedValueOnce([adminConnection]);
  const service = api({ connections });
  const view = render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  await waitFor(() => expect(connections).toHaveBeenCalledTimes(1));
  const firstSignal = connections.mock.calls[0]![2] as AbortSignal;
  view.unmount();
  render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  expect(await screen.findByText("Admin connection")).toBeVisible();
  expect(firstSignal.aborted).toBe(true);
  await act(async () => late.resolve([first]));
  expect(screen.getByText("Admin connection")).toBeVisible();
  expect(screen.queryByText("Private connection")).toBeNull();
  expect(service.personalPermissions).toHaveBeenCalled();
});

test("a personal-connection grant does not grant model use or select a model", async () => {
  const permission = {
    allowed: false,
    etag: '"permission-1"',
    user_id: user.id,
    username: user.username,
  };
  const service = api({
    capabilities: vi.fn().mockResolvedValue(
      capabilities({
        instance_connections_manageable: true,
      }),
    ),
    personalPermissions: vi.fn().mockResolvedValue([permission]),
    updatePersonalPermission: vi.fn().mockResolvedValue({
      data: { ...permission, allowed: true },
    }),
  });
  render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Grant" }));
  await waitFor(() =>
    expect(service.updatePersonalPermission).toHaveBeenCalledWith(
      user.id,
      true,
      permission.etag,
      expect.any(AbortSignal),
    ),
  );
  expect(service.models).not.toHaveBeenCalled();
  expect(service.test).not.toHaveBeenCalled();
  expect(service.update).not.toHaveBeenCalled();
  expect(screen.getByText("model-small")).toBeVisible();
});
