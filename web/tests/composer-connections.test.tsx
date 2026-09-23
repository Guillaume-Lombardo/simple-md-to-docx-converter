import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComposerConnectionsApi } from "../src/composer/api";
import { ComposerConnectionsWorkspace } from "../src/composer/connections";
import { ApiError } from "../src/api/transport";

const user = {
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};
const admin = { ...user, role: "admin" as const, username: "Admin" };
const connection = {
  allowed_user_ids: [],
  authorized: true,
  client_certificate_present: false,
  credential_present: true,
  enabled: true,
  endpoint: "https://llm.example/v1",
  etag: '"connection-1"',
  id: "00000000-0000-4000-8000-000000000100",
  identity_mode: "shared" as const,
  internal_ca_present: false,
  name: "Primary",
  permitted_models: ["small-model"],
  scope: "personal" as const,
  selected_model: "small-model",
  status: "outage" as const,
  status_message: "The provider is temporarily unavailable.",
};

function api(overrides: Record<string, unknown> = {}) {
  return {
    capabilities: vi.fn().mockResolvedValue({
      instance_connections_manageable: false,
      maximum_upload_bytes: null,
      personal_connections_allowed: false,
      status: "outage",
      status_message: "Model calls are temporarily unavailable.",
    }),
    connections: vi.fn().mockResolvedValue([connection]),
    create: vi.fn(),
    credentials: vi.fn(),
    delete: vi.fn(),
    models: vi.fn().mockResolvedValue(["small-model"]),
    personalPermissions: vi.fn().mockResolvedValue([]),
    revokeCredentials: vi.fn(),
    test: vi.fn().mockResolvedValue({ status: "ready", status_message: null }),
    update: vi.fn(),
    updatePersonalPermission: vi.fn(),
    ...overrides,
  };
}

test("outage remains explicit while authorized connection metadata stays usable", async () => {
  const service = api({
    connections: vi.fn().mockResolvedValue([
      {
        ...connection,
        client_certificate_present: true,
        internal_ca_present: true,
      },
    ]),
  });
  render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  expect(await screen.findByText(/Composer status:/)).toHaveTextContent(
    "outage",
  );
  expect(screen.getByText("Primary")).toBeVisible();
  expect(
    screen.getByText("The provider is temporarily unavailable."),
  ).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: "Add a connection" }),
  ).toBeNull();
  expect(service.personalPermissions).not.toHaveBeenCalled();
});

test("authorized connection pages remain reachable beyond the first page", async () => {
  const firstPage = Array.from({ length: 50 }, (_, index) => ({
    ...connection,
    id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    name: `Connection ${index + 1}`,
  }));
  const connections = vi
    .fn()
    .mockImplementation((offset: number) =>
      Promise.resolve(
        offset === 0 ? firstPage : [{ ...connection, name: "Connection 51" }],
      ),
    );
  const service = api({ connections });
  render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Next" }));
  expect(await screen.findByText("Connection 51")).toBeVisible();
  expect(connections).toHaveBeenLastCalledWith(50, 50, expect.any(AbortSignal));
  expect(screen.getByText("Page 2")).toBeVisible();
}, 10_000);

test("admin can change a personal connection grant using its exact revision", async () => {
  const service = api({
    capabilities: vi.fn().mockResolvedValue({
      instance_connections_manageable: true,
      maximum_upload_bytes: 1_000_000,
      personal_connections_allowed: true,
      status: "ready",
      status_message: null,
    }),
    personalPermissions: vi.fn().mockResolvedValue([
      {
        allowed: false,
        etag: '"personal-permission-2"',
        user_id: user.id,
        username: "Alice",
      },
      {
        allowed: true,
        etag: '"personal-permission-3"',
        user_id: "00000000-0000-4000-8000-000000000003",
        username: "Bob",
      },
    ]),
    updatePersonalPermission: vi.fn().mockResolvedValue({ data: {} }),
  });
  render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Grant" }));
  expect(screen.getByRole("button", { name: "Revoke" })).toBeVisible();
  await waitFor(() =>
    expect(service.updatePersonalPermission).toHaveBeenCalledWith(
      user.id,
      true,
      '"personal-permission-2"',
      expect.any(AbortSignal),
    ),
  );
  await screen.findByText("Personal connection permission granted.");
  fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
  await waitFor(() =>
    expect(service.updatePersonalPermission).toHaveBeenCalledWith(
      "00000000-0000-4000-8000-000000000003",
      false,
      '"personal-permission-3"',
      expect.any(AbortSignal),
    ),
  );
});

test("creation submits secrets once and clears every write-only field", async () => {
  const created = { ...connection, status: "ready" as const };
  const service = api({
    capabilities: vi.fn().mockResolvedValue({
      instance_connections_manageable: false,
      maximum_upload_bytes: 1_000_000,
      personal_connections_allowed: true,
      status: "unconfigured",
      status_message: null,
    }),
    connections: vi.fn().mockResolvedValue([]),
    create: vi.fn().mockResolvedValue({ data: created, etag: created.etag }),
    credentials: vi.fn().mockResolvedValue({ data: created }),
  });
  render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  fireEvent.change(await screen.findByLabelText("Connection name"), {
    target: { value: "Private" },
  });
  fireEvent.change(screen.getByLabelText("Endpoint URL"), {
    target: { value: "https://llm.example/v1" },
  });
  fireEvent.change(screen.getByLabelText("API key"), {
    target: { value: "api-secret" },
  });
  fireEvent.change(screen.getByLabelText("Client private key (PEM)"), {
    target: { value: "private-key-secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create connection" }));
  await waitFor(() => expect(service.credentials).toHaveBeenCalled());
  expect(service.create.mock.calls[0]![0]).toMatchObject({
    identity_mode: "individual",
    scope: "personal",
  });
  expect(screen.queryByLabelText("Identity mode")).toBeNull();
  expect(service.credentials.mock.calls[0]!.slice(0, 3)).toEqual([
    created.id,
    created.etag,
    {
      api_key: "api-secret",
      client_private_key: "private-key-secret",
    },
  ]);
  expect(screen.getByLabelText("API key")).toHaveValue("");
  expect(screen.getByLabelText("Client private key (PEM)")).toHaveValue("");
  expect(document.body).not.toHaveTextContent("api-secret");
  expect(document.body).not.toHaveTextContent("private-key-secret");
});

test.each([
  ["initial create", true],
  ["credential write after create", false],
])(
  "failed %s clears secrets while preserving metadata",
  async (_name, failCreate) => {
    const created = {
      ...connection,
      identity_mode: "individual" as const,
      status: "disabled" as const,
    };
    const service = api({
      capabilities: vi.fn().mockResolvedValue({
        instance_connections_manageable: false,
        maximum_upload_bytes: 1_000_000,
        personal_connections_allowed: true,
        status: "unconfigured",
        status_message: null,
      }),
      connections: failCreate
        ? vi.fn().mockResolvedValue([])
        : vi.fn().mockResolvedValueOnce([]).mockResolvedValue([created]),
      create: failCreate
        ? vi.fn().mockRejectedValue(new Error("private create failure"))
        : vi.fn().mockResolvedValue({ data: created, etag: created.etag }),
      credentials: vi
        .fn()
        .mockRejectedValue(new Error("private credential failure")),
    });
    render(
      <ComposerConnectionsWorkspace
        api={service as unknown as ComposerConnectionsApi}
        expire={vi.fn()}
        user={user}
      />,
    );
    fireEvent.change(await screen.findByLabelText("Connection name"), {
      target: { value: "Preserved metadata" },
    });
    fireEvent.change(screen.getByLabelText("Endpoint URL"), {
      target: { value: "https://llm.example/v1" },
    });
    fireEvent.change(screen.getByLabelText("API key"), {
      target: { value: "failed-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create connection" }));
    if (failCreate) {
      await screen.findByText(
        "The connection operation could not be completed. Try again.",
      );
    } else {
      await screen.findByText(
        new RegExp(`Connection ${created.id} was created disabled`),
      );
      expect(await screen.findByText("Primary")).toBeVisible();
      expect(screen.getByText("Rotate or revoke credentials")).toBeVisible();
    }
    expect(screen.getAllByLabelText("API key")[0]).toHaveValue("");
    expect(screen.getByLabelText("Connection name")).toHaveValue(
      "Preserved metadata",
    );
    expect(document.body).not.toHaveTextContent("failed-secret");
    if (failCreate) expect(service.credentials).not.toHaveBeenCalled();
    else expect(service.credentials).toHaveBeenCalledOnce();
  },
);

test("partial creation keeps the created ID when recovery refresh fails", async () => {
  const created = {
    ...connection,
    identity_mode: "individual" as const,
    status: "disabled" as const,
  };
  const service = api({
    capabilities: vi.fn().mockResolvedValue({
      instance_connections_manageable: false,
      maximum_upload_bytes: 1_000_000,
      personal_connections_allowed: true,
      status: "unconfigured",
      status_message: null,
    }),
    connections: vi
      .fn()
      .mockResolvedValueOnce([])
      .mockRejectedValueOnce(new Error("private refresh failure")),
    create: vi.fn().mockResolvedValue({ data: created, etag: created.etag }),
    credentials: vi
      .fn()
      .mockRejectedValue(new Error("private credential failure")),
  });
  render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  fireEvent.change(await screen.findByLabelText("Connection name"), {
    target: { value: "Recovery" },
  });
  fireEvent.change(screen.getByLabelText("Endpoint URL"), {
    target: { value: "https://llm.example/v1" },
  });
  fireEvent.change(screen.getByLabelText("API key"), {
    target: { value: "failed-secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create connection" }));
  await screen.findByText(
    new RegExp(`Connection ${created.id} was created disabled`),
  );
  expect(screen.getByLabelText("API key")).toHaveValue("");
  expect(document.body).not.toHaveTextContent("private refresh failure");
  expect(document.body).not.toHaveTextContent("failed-secret");
});

test("test, model discovery, disable, credential revocation, and connection revocation call safe operations", async () => {
  const service = api({
    update: vi.fn().mockResolvedValue({ data: connection }),
    revokeCredentials: vi.fn().mockResolvedValue(undefined),
    delete: vi.fn().mockResolvedValue(undefined),
  });
  render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  await screen.findByText("Primary");
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  await waitFor(() => expect(service.test).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: "Discover models" }));
  await waitFor(() => expect(service.models).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: "Disable" }));
  await waitFor(() =>
    expect(service.update).toHaveBeenCalledWith(
      connection.id,
      connection.etag,
      { enabled: false },
      expect.any(AbortSignal),
    ),
  );
  fireEvent.click(screen.getByText("Rotate or revoke credentials"));
  const rotationKey = screen.getAllByLabelText("API key").at(-1)!;
  fireEvent.change(rotationKey, { target: { value: "must-not-rotate" } });
  fireEvent.click(screen.getByRole("button", { name: "Revoke credentials" }));
  await waitFor(() =>
    expect(service.revokeCredentials).toHaveBeenCalledWith(
      connection.id,
      connection.etag,
      expect.any(AbortSignal),
    ),
  );
  expect(service.revokeCredentials).toHaveBeenCalledOnce();
  expect(service.credentials).not.toHaveBeenCalled();
  expect(rotationKey).toHaveValue("");
  fireEvent.click(screen.getByRole("button", { name: "Revoke connection" }));
  await waitFor(() => expect(service.delete).toHaveBeenCalled());
});

test("loading, expired-session, and ordinary failure states stay safe", async () => {
  let finish!: (value: unknown) => void;
  const expire = vi.fn();
  const delayed = api({
    capabilities: vi.fn().mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    ),
  });
  const view = render(
    <ComposerConnectionsWorkspace
      api={delayed as unknown as ComposerConnectionsApi}
      expire={expire}
      user={user}
    />,
  );
  expect(screen.getByText("Loading…")).toBeVisible();
  await waitFor(() => expect(finish).toBeTypeOf("function"));
  finish({
    instance_connections_manageable: false,
    maximum_upload_bytes: null,
    personal_connections_allowed: false,
    status: "unconfigured",
    status_message: null,
  });
  await screen.findByText(/Composer status:/);
  view.unmount();

  const expired = api({
    capabilities: vi
      .fn()
      .mockRejectedValue(
        new ApiError(401, "SESSION_EXPIRED", "Your session ended."),
      ),
  });
  const expiredView = render(
    <ComposerConnectionsWorkspace
      api={expired as unknown as ComposerConnectionsApi}
      expire={expire}
      user={user}
    />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Your session ended.",
  );
  expect(expire).toHaveBeenCalledOnce();
  expiredView.unmount();

  const failed = api({
    capabilities: vi
      .fn()
      .mockRejectedValue(new Error("private implementation detail")),
  });
  const failedView = render(
    <ComposerConnectionsWorkspace
      api={failed as unknown as ComposerConnectionsApi}
      expire={expire}
      user={user}
    />,
  );
  await screen.findByText(
    "The connection operation could not be completed. Try again.",
  );
  failedView.unmount();

  const denied = api({
    capabilities: vi
      .fn()
      .mockRejectedValue(
        new ApiError(403, "FORBIDDEN", "Composer access is not granted."),
      ),
  });
  render(
    <ComposerConnectionsWorkspace
      api={denied as unknown as ComposerConnectionsApi}
      expire={expire}
      user={user}
    />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Composer access is not granted.",
  );
});

test("admin instance creation sends policy metadata without a credential call when fields are blank", async () => {
  const service = api({
    capabilities: vi.fn().mockResolvedValue({
      instance_connections_manageable: true,
      maximum_upload_bytes: 1_000_000,
      personal_connections_allowed: false,
      status: "unconfigured",
      status_message: null,
    }),
    connections: vi.fn().mockResolvedValue([]),
    create: vi.fn().mockResolvedValue({ data: connection }),
  });
  render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  fireEvent.change(await screen.findByLabelText("Connection name"), {
    target: { value: "Instance" },
  });
  fireEvent.change(screen.getByLabelText("Endpoint URL"), {
    target: { value: "https://instance.example/v1" },
  });
  fireEvent.change(screen.getByLabelText("Identity mode"), {
    target: { value: "individual" },
  });
  fireEvent.change(
    screen.getByLabelText("Permitted models (comma separated)"),
    {
      target: { value: " alpha, beta, " },
    },
  );
  fireEvent.change(
    screen.getByLabelText("Allowed user IDs (comma separated)"),
    {
      target: { value: `${user.id}, ` },
    },
  );
  fireEvent.click(screen.getByRole("button", { name: "Create connection" }));
  await waitFor(() => expect(service.create).toHaveBeenCalled());
  expect(service.create.mock.calls[0]![0]).toEqual({
    allowed_user_ids: [user.id],
    enabled: false,
    endpoint: "https://instance.example/v1",
    identity_mode: "individual",
    name: "Instance",
    permitted_models: ["alpha", "beta"],
    scope: "instance",
    selected_model: null,
  });
  expect(service.credentials).not.toHaveBeenCalled();
});

test("catalog selection and credential rotation use current identity and clear secrets", async () => {
  const service = api({
    update: vi.fn().mockResolvedValue({ data: connection }),
    credentials: vi.fn().mockResolvedValue({ data: connection }),
  });
  render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  await screen.findByText("Primary");
  fireEvent.click(screen.getByRole("button", { name: "Discover models" }));
  expect(
    await screen.findByRole("combobox", { name: "Authorized model" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Save model" }));
  await waitFor(() =>
    expect(service.update).toHaveBeenCalledWith(
      connection.id,
      connection.etag,
      { selected_model: "small-model" },
      expect.any(AbortSignal),
    ),
  );
  await screen.findByText("Selected model updated.");
  fireEvent.click(screen.getByText("Rotate or revoke credentials"));
  const keyFields = screen.getAllByLabelText("API key");
  fireEvent.change(keyFields.at(-1)!, {
    target: { value: "replacement-secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Rotate credentials" }));
  await waitFor(() =>
    expect(service.credentials).toHaveBeenCalledWith(
      connection.id,
      connection.etag,
      { api_key: "replacement-secret" },
      expect.any(AbortSignal),
    ),
  );
  expect(keyFields.at(-1)).toHaveValue("");
  expect(document.body).not.toHaveTextContent("replacement-secret");
});

test("failed credential rotation clears submitted secrets", async () => {
  const service = api({
    credentials: vi
      .fn()
      .mockRejectedValue(new Error("private provider detail")),
  });
  render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  await screen.findByText("Primary");
  fireEvent.click(screen.getByText("Rotate or revoke credentials"));
  const rotationKey = screen.getAllByLabelText("API key").at(-1)!;
  fireEvent.change(rotationKey, {
    target: { value: "failed-rotation-secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Rotate credentials" }));
  await screen.findByText(
    "The connection operation could not be completed. Try again.",
  );
  expect(rotationKey).toHaveValue("");
  expect(document.body).not.toHaveTextContent("failed-rotation-secret");
});

test("missing credential, CA, model, and status detail render explicit safe states", async () => {
  const service = api({
    capabilities: vi.fn().mockResolvedValue({
      instance_connections_manageable: false,
      maximum_upload_bytes: null,
      personal_connections_allowed: true,
      status: "disabled",
      status_message: null,
    }),
    connections: vi.fn().mockResolvedValue([
      {
        ...connection,
        client_certificate_present: false,
        credential_present: false,
        enabled: false,
        internal_ca_present: false,
        selected_model: null,
        status: "disabled",
        status_message: null,
      },
    ]),
  });
  render(
    <ComposerConnectionsWorkspace
      api={service as unknown as ComposerConnectionsApi}
      expire={vi.fn()}
      user={user}
    />,
  );
  await screen.findByText("Primary");
  expect(screen.getAllByText("Missing")).toHaveLength(2);
  expect(screen.getByText("Default trust")).toBeVisible();
  expect(screen.getByText("None")).toBeVisible();
  expect(screen.getByRole("button", { name: "Enable" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Enable" }));
  await waitFor(() =>
    expect(service.update).toHaveBeenCalledWith(
      connection.id,
      connection.etag,
      { enabled: true },
      expect.any(AbortSignal),
    ),
  );
});
