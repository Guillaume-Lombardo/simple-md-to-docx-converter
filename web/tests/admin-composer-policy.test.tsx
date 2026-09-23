import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ApiError, type ApiTransport } from "../src/api/transport";
import {
  AdminComposerPolicyApi,
  type ComposerPolicy,
} from "../src/admin/composer-policy-api";
import { AdminComposerPolicyWorkspace } from "../src/admin/composer-policy";
import type { ComposerConnectionsApi } from "../src/composer/api";
import { ComposerConnectionsWorkspace } from "../src/composer/connections";

const policy: ComposerPolicy = {
  mode: "delegated",
  enabled: false,
  allowed_destinations: [],
  allowed_networks: [],
  editable_destinations: true,
  etag: '"policy-1"',
};

function policyApi(overrides: Record<string, unknown> = {}) {
  return {
    get: vi.fn().mockResolvedValue(policy),
    resolve: vi.fn().mockResolvedValue({
      destination: "llm.example:443",
      addresses: ["192.0.2.10/32", "2001:db8::10/128"],
    }),
    update: vi
      .fn()
      .mockResolvedValue({ ...policy, enabled: true, etag: '"policy-2"' }),
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((accept, deny) => {
    resolve = accept;
    reject = deny;
  });
  return { promise, resolve, reject };
}

test("LLM setup keeps policy, connection, and credential labels bound to unique inputs", async () => {
  const admin = {
    active: true,
    effective_idle_minutes: 30,
    id: "00000000-0000-4000-8000-000000000001",
    password_change_required: false,
    role: "admin" as const,
    username: "Admin",
  };
  const connection = {
    allowed_user_ids: [],
    authorized: true,
    client_certificate_present: false,
    credential_present: true,
    enabled: false,
    endpoint: "https://llm.example/v1",
    etag: '"connection-1"',
    id: "00000000-0000-4000-8000-000000000100",
    identity_mode: "shared" as const,
    internal_ca_present: false,
    name: "Shared model",
    permitted_models: ["small-model"],
    scope: "instance" as const,
    selected_model: "small-model",
    status: "ready" as const,
    status_message: null,
  };
  const connectionsApi = {
    capabilities: vi.fn().mockResolvedValue({
      instance_connections_manageable: true,
      maximum_upload_bytes: 1_000_000,
      personal_connections_allowed: true,
      status: "ready",
      status_message: null,
    }),
    connections: vi
      .fn()
      .mockResolvedValue([
        connection,
        { ...connection, id: "00000000-0000-4000-8000-000000000101" },
      ]),
    personalPermissions: vi.fn().mockResolvedValue([]),
  };
  render(
    <>
      <AdminComposerPolicyWorkspace
        api={
          policyApi({
            get: vi.fn().mockResolvedValue({ ...policy, enabled: true }),
          }) as unknown as AdminComposerPolicyApi
        }
        expire={vi.fn()}
      />
      <ComposerConnectionsWorkspace
        api={connectionsApi as unknown as ComposerConnectionsApi}
        expire={vi.fn()}
        user={admin}
      />
    </>,
  );

  const addForm = (
    await screen.findByRole("heading", {
      name: "Add a connection",
    })
  ).closest("form");
  expect(addForm).not.toBeNull();
  const policyEndpoint = screen.getByRole("textbox", {
    name: "OpenAI-compatible endpoint URL",
  });
  const connectionEndpoint = within(addForm!).getByRole("textbox", {
    name: /^Endpoint URL$/,
  });
  expect(policyEndpoint).not.toBe(connectionEndpoint);

  const labeledInputs = Array.from(
    document.querySelectorAll<HTMLInputElement>("input[id]"),
  );
  expect(new Set(labeledInputs.map((input) => input.id)).size).toBe(
    labeledInputs.length,
  );
  const apiKeys = Array.from(
    document.querySelectorAll<HTMLInputElement>('input[name="api_key"]'),
  );
  expect(apiKeys).toHaveLength(3);
  for (const input of [policyEndpoint, connectionEndpoint, ...apiKeys]) {
    const label = input.closest("label");
    expect(label?.htmlFor).toBe(input.id);
    expect(document.getElementById(input.id)).toBe(input);
  }
});

test("policy API uses exact admin paths, CSRF, and policy revision", async () => {
  const transport = { json: vi.fn().mockResolvedValue(policy) };
  const api = new AdminComposerPolicyApi(transport as unknown as ApiTransport);
  await api.get();
  await api.resolve("https://llm.example/v1");
  await api.update(policy, {
    enabled: true,
    allowed_destinations: ["llm.example:443"],
    allowed_networks: ["192.0.2.10/32"],
  });
  expect(transport.json.mock.calls.map(([path]) => path)).toEqual([
    "/api/v1/admin/composer-policy",
    "/api/v1/admin/composer-policy/resolve",
    "/api/v1/admin/composer-policy",
  ]);
  expect(transport.json.mock.calls[1]![2]).toMatchObject({
    csrf: true,
    method: "POST",
  });
  expect(transport.json.mock.calls[2]![2]).toMatchObject({
    csrf: true,
    etag: policy.etag,
    method: "PUT",
  });
  expect(JSON.parse(transport.json.mock.calls[2]![2].body)).toEqual({
    enabled: true,
    allowed_destinations: ["llm.example:443"],
    allowed_networks: ["192.0.2.10/32"],
  });
});

test("admin must preview and explicitly approve exact destination and addresses", async () => {
  const api = policyApi();
  render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={vi.fn()}
    />,
  );
  expect(await screen.findByText(/Policy mode:/)).toHaveTextContent(
    "delegated",
  );
  expect(api.update).not.toHaveBeenCalled();
  fireEvent.change(
    screen.getByRole("textbox", { name: "OpenAI-compatible endpoint URL" }),
    {
      target: { value: "https://llm.example/v1" },
    },
  );
  fireEvent.click(
    screen.getByRole("button", {
      name: "Preview destination and DNS addresses",
    }),
  );
  expect(await screen.findByText(/llm.example:443/)).toBeVisible();
  expect(api.resolve).toHaveBeenCalledWith(
    "https://llm.example/v1",
    expect.any(AbortSignal),
  );
  expect(api.update).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("button", { name: "Approve destination and addresses" }),
  );
  await waitFor(() =>
    expect(api.update).toHaveBeenCalledWith(
      policy,
      {
        enabled: true,
        allowed_destinations: ["llm.example:443"],
        allowed_networks: ["192.0.2.10/32", "2001:db8::10/128"],
      },
      expect.any(AbortSignal),
    ),
  );
});

test("editing the endpoint discards an earlier DNS preview", async () => {
  const api = policyApi();
  render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={vi.fn()}
    />,
  );
  const field = await screen.findByRole("textbox", {
    name: "OpenAI-compatible endpoint URL",
  });
  fireEvent.change(field, { target: { value: "https://llm.example" } });
  fireEvent.click(
    screen.getByRole("button", {
      name: "Preview destination and DNS addresses",
    }),
  );
  expect(
    await screen.findByRole("button", {
      name: "Approve destination and addresses",
    }),
  ).toBeVisible();
  fireEvent.change(field, { target: { value: "https://different.example" } });
  expect(
    screen.queryByRole("button", { name: "Approve destination and addresses" }),
  ).toBeNull();
  expect(api.update).not.toHaveBeenCalled();
});

test("an in-flight DNS result cannot restore a preview for an edited endpoint", async () => {
  const pending = deferred<{ destination: string; addresses: string[] }>();
  const api = policyApi({ resolve: vi.fn().mockReturnValue(pending.promise) });
  render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={vi.fn()}
    />,
  );
  const field = await screen.findByRole("textbox", {
    name: "OpenAI-compatible endpoint URL",
  });
  fireEvent.change(field, { target: { value: "https://first.example" } });
  fireEvent.click(
    screen.getByRole("button", {
      name: "Preview destination and DNS addresses",
    }),
  );
  fireEvent.change(field, { target: { value: "https://second.example" } });
  await act(async () =>
    pending.resolve({
      destination: "first.example:443",
      addresses: ["192.0.2.10/32"],
    }),
  );
  expect(screen.queryByText(/first.example:443/)).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Approve destination and addresses" }),
  ).toBeNull();
  expect(api.update).not.toHaveBeenCalled();
});

test("operator destination ceiling is visible but cannot be edited", async () => {
  const api = policyApi({
    get: vi.fn().mockResolvedValue({
      ...policy,
      mode: "operator",
      editable_destinations: false,
    }),
  });
  render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={vi.fn()}
    />,
  );
  expect(await screen.findByText(/managed by the operator/)).toBeVisible();
  expect(
    screen.queryByRole("textbox", { name: "OpenAI-compatible endpoint URL" }),
  ).toBeNull();
  expect(api.update).not.toHaveBeenCalled();
});

test("operator can enable a fixed destination ceiling without changing it", async () => {
  const configured = {
    ...policy,
    mode: "operator" as const,
    editable_destinations: false,
    allowed_destinations: ["operator.example:443"],
    allowed_networks: ["192.0.2.20/32"],
  };
  const api = policyApi({ get: vi.fn().mockResolvedValue(configured) });
  render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={vi.fn()}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Enable approved destinations" }),
  );
  await waitFor(() =>
    expect(api.update).toHaveBeenCalledWith(
      configured,
      {
        enabled: true,
        allowed_destinations: ["operator.example:443"],
        allowed_networks: ["192.0.2.20/32"],
      },
      expect.any(AbortSignal),
    ),
  );
});

test("enabled policy can be disabled while preserving its ceiling", async () => {
  const configured = {
    ...policy,
    enabled: true,
    allowed_destinations: ["operator.example:443"],
    allowed_networks: ["192.0.2.20/32"],
  };
  const onPolicyEnabled = vi.fn();
  const api = policyApi({
    get: vi.fn().mockResolvedValue(configured),
    update: vi.fn().mockResolvedValue({ ...configured, enabled: false }),
  });
  render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={vi.fn()}
      onPolicyEnabled={onPolicyEnabled}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Disable Composer egress" }),
  );
  await waitFor(() => expect(onPolicyEnabled).toHaveBeenLastCalledWith(false));
  expect(api.update).toHaveBeenCalledWith(
    configured,
    {
      enabled: false,
      allowed_destinations: ["operator.example:443"],
      allowed_networks: ["192.0.2.20/32"],
    },
    expect.any(AbortSignal),
  );
});

test("a failed DNS preview cannot approve a destination", async () => {
  const api = policyApi({
    resolve: vi
      .fn()
      .mockRejectedValue(
        new ApiError(422, "INVALID_ENDPOINT", "Endpoint is invalid."),
      ),
  });
  render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={vi.fn()}
    />,
  );
  const field = await screen.findByRole("textbox", {
    name: "OpenAI-compatible endpoint URL",
  });
  fireEvent.change(field, { target: { value: "https://bad.example" } });
  fireEvent.click(
    screen.getByRole("button", {
      name: "Preview destination and DNS addresses",
    }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Endpoint is invalid.",
  );
  expect(
    screen.queryByRole("button", { name: "Approve destination and addresses" }),
  ).toBeNull();
  expect(api.update).not.toHaveBeenCalled();
});

test("an empty DNS result cannot be approved", async () => {
  const api = policyApi({
    resolve: vi
      .fn()
      .mockResolvedValue({ destination: "empty.example:443", addresses: [] }),
  });
  render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={vi.fn()}
    />,
  );
  const field = await screen.findByRole("textbox", {
    name: "OpenAI-compatible endpoint URL",
  });
  fireEvent.change(field, { target: { value: "https://empty.example" } });
  fireEvent.click(
    screen.getByRole("button", {
      name: "Preview destination and DNS addresses",
    }),
  );
  expect(
    await screen.findByRole("button", {
      name: "Approve destination and addresses",
    }),
  ).toBeDisabled();
  expect(api.update).not.toHaveBeenCalled();
});

test("a stale policy revision reloads the ceiling and discards the preview", async () => {
  const latest = {
    ...policy,
    etag: '"policy-2"',
    allowed_destinations: ["new.example:443"],
  };
  const api = policyApi({
    get: vi.fn().mockResolvedValueOnce(policy).mockResolvedValueOnce(latest),
    update: vi
      .fn()
      .mockRejectedValue(new ApiError(412, "PRECONDITION_FAILED", "Changed")),
  });
  render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={vi.fn()}
    />,
  );
  const field = await screen.findByRole("textbox", {
    name: "OpenAI-compatible endpoint URL",
  });
  fireEvent.change(field, { target: { value: "https://llm.example" } });
  fireEvent.click(
    screen.getByRole("button", {
      name: "Preview destination and DNS addresses",
    }),
  );
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Approve destination and addresses",
    }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "changed on the server",
  );
  await waitFor(() =>
    expect(screen.getByText(/new.example:443/)).toBeVisible(),
  );
  expect(
    screen.queryByRole("button", { name: "Approve destination and addresses" }),
  ).toBeNull();
});

test("unavailable policy leaves the connection step disabled", async () => {
  const api = policyApi({
    get: vi.fn().mockRejectedValue(new Error("offline")),
  });
  const onPolicyEnabled = vi.fn();
  render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={vi.fn()}
      onPolicyEnabled={onPolicyEnabled}
    />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "could not be loaded",
  );
  expect(onPolicyEnabled).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("button", { name: "Enable approved destinations" }),
  ).toBeNull();
  expect(
    screen.getByRole("button", { name: "Retry loading policy" }),
  ).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("ask the operator");
});

test("retry reloads the policy after an initial service failure", async () => {
  const api = policyApi({
    get: vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(policy),
  });
  render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={vi.fn()}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Retry loading policy" }),
  );
  expect(await screen.findByText(/Policy mode:/)).toHaveTextContent(
    "delegated",
  );
  expect(api.get).toHaveBeenCalledTimes(2);
  expect(
    screen.queryByRole("button", { name: "Retry loading policy" }),
  ).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a policy response arriving after navigation does not publish stale state", async () => {
  const pending = deferred<ComposerPolicy>();
  const onPolicyEnabled = vi.fn();
  const api = policyApi({ get: vi.fn().mockReturnValue(pending.promise) });
  const view = render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={vi.fn()}
      onPolicyEnabled={onPolicyEnabled}
    />,
  );
  await waitFor(() => expect(api.get).toHaveBeenCalledOnce());
  view.unmount();
  await act(async () => pending.resolve(policy));
  expect(onPolicyEnabled).not.toHaveBeenCalled();
});

test("a policy error arriving after navigation does not expire the next page", async () => {
  const pending = deferred<ComposerPolicy>();
  const expire = vi.fn();
  const api = policyApi({ get: vi.fn().mockReturnValue(pending.promise) });
  const view = render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={expire}
    />,
  );
  await waitFor(() => expect(api.get).toHaveBeenCalledOnce());
  view.unmount();
  await act(async () =>
    pending.reject(new ApiError(401, "SESSION_EXPIRED", "Expired")),
  );
  expect(expire).not.toHaveBeenCalled();
});

test("a DNS result arriving after navigation is ignored", async () => {
  const pending = deferred<{ destination: string; addresses: string[] }>();
  const api = policyApi({ resolve: vi.fn().mockReturnValue(pending.promise) });
  const view = render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={vi.fn()}
    />,
  );
  const field = await screen.findByRole("textbox", {
    name: "OpenAI-compatible endpoint URL",
  });
  fireEvent.change(field, { target: { value: "https://llm.example" } });
  fireEvent.click(
    screen.getByRole("button", {
      name: "Preview destination and DNS addresses",
    }),
  );
  view.unmount();
  await act(async () =>
    pending.resolve({
      destination: "llm.example:443",
      addresses: ["192.0.2.10/32"],
    }),
  );
  expect(api.update).not.toHaveBeenCalled();
});

test("a saved policy response arriving after navigation cannot reopen connection setup", async () => {
  const pending = deferred<ComposerPolicy>();
  const onPolicyEnabled = vi.fn();
  const api = policyApi({ update: vi.fn().mockReturnValue(pending.promise) });
  const view = render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={vi.fn()}
      onPolicyEnabled={onPolicyEnabled}
    />,
  );
  const field = await screen.findByRole("textbox", {
    name: "OpenAI-compatible endpoint URL",
  });
  fireEvent.change(field, { target: { value: "https://llm.example" } });
  fireEvent.click(
    screen.getByRole("button", {
      name: "Preview destination and DNS addresses",
    }),
  );
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Approve destination and addresses",
    }),
  );
  view.unmount();
  await act(async () => pending.resolve({ ...policy, enabled: true }));
  expect(onPolicyEnabled).toHaveBeenCalledTimes(1);
  expect(onPolicyEnabled).toHaveBeenLastCalledWith(false);
});

test("expired admin session is reported and revoked", async () => {
  const expire = vi.fn();
  const api = policyApi({
    get: vi
      .fn()
      .mockRejectedValue(new ApiError(401, "SESSION_EXPIRED", "Expired")),
  });
  render(
    <AdminComposerPolicyWorkspace
      api={api as unknown as AdminComposerPolicyApi}
      expire={expire}
    />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Your session ended",
  );
  expect(expire).toHaveBeenCalledOnce();
});
