import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { AdministrationApi } from "../src/admin/api";
import { SessionPolicyWorkspace } from "../src/admin/session-policy";
import { ApiError } from "../src/api/transport";

const alice = {
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};
const admin = {
  ...alice,
  effective_idle_minutes: 15,
  id: "00000000-0000-4000-8000-000000000099",
  role: "admin" as const,
  username: "Admin",
};
const policy = {
  absolute_lifetime_seconds: 6_000,
  admin_idle_minutes: 13,
  admin_idle_minutes_bounds: {
    default_minutes: 11,
    maximum_minutes: 25,
    minimum_minutes: 5,
  },
  idle_minutes_granularity: 2,
  revision: 7,
  user_idle_minutes: 27,
  user_idle_minutes_bounds: {
    default_minutes: 29,
    maximum_minutes: 101,
    minimum_minutes: 5,
  },
};

function policyApi(overrides: Record<string, unknown> = {}) {
  return {
    sessionPolicy: vi.fn().mockResolvedValue({
      data: policy,
      etag: '"idle-session-policy-7"',
    }),
    updateSessionPolicy: vi.fn().mockResolvedValue({
      data: { ...policy, revision: 8 },
      etag: '"idle-session-policy-8"',
    }),
    ...overrides,
  };
}

test("administrator sees authoritative effective values and all metadata", async () => {
  const api = policyApi();
  render(
    <SessionPolicyWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  expect(await screen.findByDisplayValue("27")).toBeVisible();
  expect(screen.getByDisplayValue("13")).toBeVisible();
  expect(screen.getByText("7")).toBeVisible();
  expect(screen.getByText("6000 seconds")).toBeVisible();
  expect(screen.getByText("2 minute(s)")).toBeVisible();
  expect(
    screen.getByText(/Default 29; approved range 5–101.*at most 100 minutes/),
  ).toBeVisible();
  expect(
    screen.getByText(/Default 11; approved range 5–25.*at most 25 minutes/),
  ).toBeVisible();
  expect(screen.getByText(/including you/)).toBeVisible();
});

test("ordinary users see a forbidden workspace without requesting policy data", () => {
  const api = policyApi();
  render(
    <SessionPolicyWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={alice}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Administrator access is required.",
  );
  expect(api.sessionPolicy).not.toHaveBeenCalled();
});

test("dynamic validation rejects malformed, out-of-range, and off-step values without a PUT", async () => {
  const api = policyApi();
  render(
    <SessionPolicyWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  const userInput = await screen.findByRole("textbox", {
    name: "User inactivity duration (minutes)",
  });
  const form = screen
    .getByRole("button", { name: "Save session policy" })
    .closest("form")!;
  fireEvent.change(userInput, { target: { value: "9.5" } });
  fireEvent.submit(form);
  expect(screen.getByText(/must be a whole number/)).toBeVisible();
  fireEvent.change(userInput, { target: { value: "101" } });
  fireEvent.submit(form);
  expect(screen.getByText(/must be between 5 and 100/)).toBeVisible();
  fireEvent.change(userInput, { target: { value: "26" } });
  fireEvent.submit(form);
  expect(screen.getByText(/must use 2-minute increments/)).toBeVisible();
  fireEvent.change(userInput, { target: { value: "27" } });
  fireEvent.change(
    screen.getByRole("textbox", {
      name: "Administrator inactivity duration (minutes)",
    }),
    { target: { value: "14" } },
  );
  fireEvent.submit(form);
  expect(screen.getByText(/Administrator.*2-minute increments/)).toBeVisible();
  expect(api.updateSessionPolicy).not.toHaveBeenCalled();
});

test("atomic save is duplicate-safe and publishes only the authoritative follow-up GET", async () => {
  let finish!: (value: unknown) => void;
  const changed = {
    ...policy,
    admin_idle_minutes: 15,
    revision: 8,
    user_idle_minutes: 29,
  };
  const api = policyApi({
    sessionPolicy: vi
      .fn()
      .mockResolvedValueOnce({ data: policy, etag: '"idle-session-policy-7"' })
      .mockResolvedValueOnce({
        data: changed,
        etag: '"idle-session-policy-8"',
      }),
    updateSessionPolicy: vi
      .fn()
      .mockImplementation(() => new Promise((resolve) => (finish = resolve))),
  });
  render(
    <SessionPolicyWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  const userInput = await screen.findByDisplayValue("27");
  fireEvent.change(userInput, { target: { value: "29" } });
  fireEvent.change(screen.getByDisplayValue("13"), { target: { value: "15" } });
  const form = screen
    .getByRole("button", { name: "Save session policy" })
    .closest("form")!;
  fireEvent.submit(form);
  fireEvent.submit(form);
  expect(api.updateSessionPolicy).toHaveBeenCalledOnce();
  expect(api.updateSessionPolicy).toHaveBeenCalledWith(
    '"idle-session-policy-7"',
    29,
    15,
    expect.any(AbortSignal),
  );
  await act(async () => {
    finish({ data: { ...policy, revision: 999 }, etag: '"ignored"' });
  });
  expect(await screen.findByText("Session policy updated.")).toBeVisible();
  expect(screen.getByText("8")).toBeVisible();
  expect(api.sessionPolicy).toHaveBeenCalledTimes(2);
});

test("missing ETag prevents mutation", async () => {
  const api = policyApi({
    sessionPolicy: vi.fn().mockResolvedValue({ data: policy }),
  });
  render(
    <SessionPolicyWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  expect(
    await screen.findByText(/did not provide the policy revision/),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Save session policy" }),
  ).toBeNull();
  expect(api.updateSessionPolicy).not.toHaveBeenCalled();
});

test("failed reads discard cached revisions and can be retried safely", async () => {
  const api = policyApi({
    sessionPolicy: vi
      .fn()
      .mockRejectedValueOnce(new TypeError("private database detail"))
      .mockResolvedValueOnce({
        data: policy,
        etag: '"idle-session-policy-7"',
      }),
  });
  render(
    <SessionPolicyWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  expect(
    await screen.findByText(
      "The session policy could not be loaded. Try again.",
    ),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Save session policy" }),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Reload policy" }));
  expect(await screen.findByDisplayValue("27")).toBeVisible();
  expect(api.sessionPolicy).toHaveBeenCalledTimes(2);
});

test("stale writes reload once and are never replayed", async () => {
  const latest = { ...policy, revision: 12, user_idle_minutes: 31 };
  const api = policyApi({
    sessionPolicy: vi
      .fn()
      .mockResolvedValueOnce({ data: policy, etag: '"idle-session-policy-7"' })
      .mockResolvedValueOnce({
        data: latest,
        etag: '"idle-session-policy-12"',
      }),
    updateSessionPolicy: vi
      .fn()
      .mockRejectedValue(new ApiError(412, "PRECONDITION_FAILED", "unsafe")),
  });
  render(
    <SessionPolicyWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  await screen.findByDisplayValue("27");
  fireEvent.submit(
    screen
      .getByRole("button", { name: "Save session policy" })
      .closest("form")!,
  );
  expect(await screen.findByText(/changed on the server/)).toBeVisible();
  expect(screen.getByDisplayValue("31")).toBeVisible();
  expect(api.updateSessionPolicy).toHaveBeenCalledOnce();
  expect(api.sessionPolicy).toHaveBeenCalledTimes(2);
});

test("failed policy mutations expose bounded errors without refreshing or replaying", async () => {
  const api = policyApi({
    updateSessionPolicy: vi
      .fn()
      .mockRejectedValue(
        new ApiError(422, "INVALID_POLICY", "Safe policy rejection"),
      ),
  });
  render(
    <SessionPolicyWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  await screen.findByDisplayValue("27");
  fireEvent.submit(
    screen
      .getByRole("button", { name: "Save session policy" })
      .closest("form")!,
  );
  expect(await screen.findByText("Safe policy rejection")).toBeVisible();
  expect(api.updateSessionPolicy).toHaveBeenCalledOnce();
  expect(api.sessionPolicy).toHaveBeenCalledOnce();
});

test("a stale conflict followed by session loss preserves the authoritative expiry", async () => {
  const expire = vi.fn();
  const api = policyApi({
    sessionPolicy: vi
      .fn()
      .mockResolvedValueOnce({ data: policy, etag: '"idle-session-policy-7"' })
      .mockRejectedValueOnce(
        new ApiError(401, "AUTHENTICATION_REQUIRED", "unsafe"),
      ),
    updateSessionPolicy: vi
      .fn()
      .mockRejectedValue(new ApiError(412, "PRECONDITION_FAILED", "unsafe")),
  });
  render(
    <SessionPolicyWorkspace
      api={api as unknown as AdministrationApi}
      expire={expire}
      user={admin}
    />,
  );
  await screen.findByDisplayValue("27");
  fireEvent.submit(
    screen
      .getByRole("button", { name: "Save session policy" })
      .closest("form")!,
  );
  await waitFor(() => expect(expire).toHaveBeenCalledOnce());
  expect(screen.getByText(/Your session ended/)).toBeVisible();
  expect(screen.queryByText(/changed on the server/)).toBeNull();
  expect(api.updateSessionPolicy).toHaveBeenCalledOnce();
});

test("tightening may expire the caller during authoritative refresh without replay", async () => {
  const expire = vi.fn();
  const api = policyApi({
    sessionPolicy: vi
      .fn()
      .mockResolvedValueOnce({ data: policy, etag: '"idle-session-policy-7"' })
      .mockRejectedValueOnce(
        new ApiError(401, "AUTHENTICATION_REQUIRED", "unsafe"),
      ),
  });
  render(
    <SessionPolicyWorkspace
      api={api as unknown as AdministrationApi}
      expire={expire}
      user={admin}
    />,
  );
  await screen.findByDisplayValue("27");
  fireEvent.submit(
    screen
      .getByRole("button", { name: "Save session policy" })
      .closest("form")!,
  );
  await waitFor(() => expect(expire).toHaveBeenCalledOnce());
  expect(screen.getByText(/Your session ended/)).toBeVisible();
  expect(screen.queryByText("Session policy updated.")).toBeNull();
  expect(api.updateSessionPolicy).toHaveBeenCalledOnce();
  expect(api.sessionPolicy).toHaveBeenCalledTimes(2);
});

test("unmounted policy requests publish no late state", async () => {
  let resolvePolicy!: (value: unknown) => void;
  const api = policyApi({
    sessionPolicy: vi
      .fn()
      .mockImplementation(
        () => new Promise((resolve) => (resolvePolicy = resolve)),
      ),
  });
  const view = render(
    <SessionPolicyWorkspace
      api={api as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  await waitFor(() => expect(api.sessionPolicy).toHaveBeenCalledOnce());
  view.unmount();
  await act(async () => {
    resolvePolicy({ data: policy, etag: '"idle-session-policy-7"' });
  });
  expect(api.sessionPolicy).toHaveBeenCalledOnce();

  let rejectPolicy!: (reason: unknown) => void;
  const rejectedApi = policyApi({
    sessionPolicy: vi
      .fn()
      .mockImplementation(
        () => new Promise((_resolve, reject) => (rejectPolicy = reject)),
      ),
  });
  const rejectedView = render(
    <SessionPolicyWorkspace
      api={rejectedApi as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  await waitFor(() => expect(rejectedApi.sessionPolicy).toHaveBeenCalledOnce());
  rejectedView.unmount();
  await act(async () => {
    rejectPolicy(new TypeError("private late failure"));
  });
  expect(rejectedApi.sessionPolicy).toHaveBeenCalledOnce();
});

test("unmounted policy mutations neither refresh nor publish late failures", async () => {
  let resolveUpdate!: (value: unknown) => void;
  const resolvedApi = policyApi({
    updateSessionPolicy: vi
      .fn()
      .mockImplementation(
        () => new Promise((resolve) => (resolveUpdate = resolve)),
      ),
  });
  const resolvedView = render(
    <SessionPolicyWorkspace
      api={resolvedApi as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  await screen.findByDisplayValue("27");
  fireEvent.submit(
    screen
      .getByRole("button", { name: "Save session policy" })
      .closest("form")!,
  );
  resolvedView.unmount();
  await act(async () => {
    resolveUpdate({ data: policy, etag: '"idle-session-policy-8"' });
  });
  expect(resolvedApi.sessionPolicy).toHaveBeenCalledOnce();

  let rejectUpdate!: (reason: unknown) => void;
  const rejectedApi = policyApi({
    updateSessionPolicy: vi
      .fn()
      .mockImplementation(
        () => new Promise((_resolve, reject) => (rejectUpdate = reject)),
      ),
  });
  const rejectedView = render(
    <SessionPolicyWorkspace
      api={rejectedApi as unknown as AdministrationApi}
      expire={vi.fn()}
      user={admin}
    />,
  );
  await screen.findByDisplayValue("27");
  fireEvent.submit(
    screen
      .getByRole("button", { name: "Save session policy" })
      .closest("form")!,
  );
  rejectedView.unmount();
  await act(async () => {
    rejectUpdate(new TypeError("private late failure"));
  });
  expect(rejectedApi.updateSessionPolicy).toHaveBeenCalledOnce();
});
