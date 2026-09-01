import { AuthController } from "../src/auth/controller";
import { ApiError, type ApiTransport } from "../src/api/transport";
import type { UserResponse } from "../src/api/generated/types.gen";

const user = (overrides: Partial<UserResponse> = {}): UserResponse => ({
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user",
  username: "Alice",
  ...overrides,
});

function controllerWith(json: ReturnType<typeof vi.fn>) {
  return new AuthController({ json } as unknown as ApiTransport);
}

test("session loading distinguishes anonymous, unavailable, and authenticated states", async () => {
  const json = vi
    .fn()
    .mockRejectedValueOnce(
      new ApiError(401, "AUTHENTICATION_REQUIRED", "ignored"),
    )
    .mockRejectedValueOnce(new TypeError("private network detail"))
    .mockResolvedValueOnce(user());
  const controller = controllerWith(json);
  await controller.load();
  expect(controller.snapshot()).toEqual({ phase: "anonymous" });
  await controller.load();
  expect(controller.snapshot()).toEqual({ phase: "unavailable" });
  expect(controller.unavailableMessage()).toBe(
    "Markweave is unavailable. Try again shortly.",
  );
  await controller.load();
  expect(controller.snapshot()).toMatchObject({
    phase: "authenticated",
    user: { effective_idle_minutes: 30 },
  });
});

test("a session response without the effective policy fails closed", async () => {
  for (const effective_idle_minutes of [undefined, null, 0]) {
    const missingPolicy = { ...user(), effective_idle_minutes };
    const controller = controllerWith(vi.fn().mockResolvedValue(missingPolicy));
    await controller.load();
    expect(controller.snapshot()).toEqual({ phase: "unavailable" });
  }
});

test("login maps invalid credentials safely and accepts restricted sessions", async () => {
  const json = vi
    .fn()
    .mockRejectedValueOnce(
      new ApiError(401, "INVALID_CREDENTIALS", "unsafe detail"),
    )
    .mockResolvedValueOnce({
      csrf_token: "not retained",
      user: user({ password_change_required: true }),
    });
  const controller = controllerWith(json);
  await controller.login("submitted-name", "submitted-password");
  expect(controller.snapshot()).toEqual({
    phase: "anonymous",
    notice: "Username or password is incorrect.",
  });
  await controller.login("Alice", "temporary");
  expect(controller.snapshot()).toMatchObject({
    phase: "restricted",
    pending: false,
  });
  expect(JSON.stringify(controller.snapshot())).not.toContain("not retained");
});

test("login uses one fixed failure for non-credential errors", async () => {
  const controller = controllerWith(
    vi.fn().mockRejectedValue(new TypeError("submitted-password")),
  );
  await controller.login("submitted-name", "submitted-password");
  expect(controller.snapshot()).toEqual({
    phase: "anonymous",
    notice: "Sign-in could not be completed. Try again.",
  });
});

test("duplicate login and renewal submits are fenced", async () => {
  let resolveLogin!: (value: unknown) => void;
  const json = vi.fn().mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveLogin = resolve;
      }),
  );
  const controller = controllerWith(json);
  const first = controller.login("Alice", "password");
  const duplicate = controller.login("Alice", "password");
  expect(json).toHaveBeenCalledOnce();
  resolveLogin({
    csrf_token: "csrf",
    user: user({ password_change_required: true }),
  });
  await Promise.all([first, duplicate]);

  let resolveRenew!: (value: unknown) => void;
  json.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveRenew = resolve;
      }),
  );
  const renewal = controller.renew("new", "new");
  const repeated = controller.renew("new", "new");
  expect(json).toHaveBeenCalledTimes(2);
  resolveRenew(undefined);
  await Promise.all([renewal, repeated]);
  expect(controller.snapshot()).toEqual({
    phase: "anonymous",
    notice: "Password changed. Sign in with your new password.",
  });
});

test.each([
  [
    new ApiError(422, "PASSWORD_CONFIRMATION_INVALID", "ignored"),
    "The passwords do not match.",
  ],
  [
    new ApiError(422, "PASSWORD_INVALID", "ignored"),
    "Enter a valid new password.",
  ],
  [new TypeError("private"), "The password could not be changed. Try again."],
])(
  "renewal maps failures without reflecting backend details",
  async (failure, message) => {
    const json = vi
      .fn()
      .mockResolvedValueOnce({
        csrf_token: "csrf",
        user: user({ password_change_required: true }),
      })
      .mockRejectedValueOnce(failure);
    const controller = controllerWith(json);
    await controller.login("Alice", "temporary");
    await controller.renew("secret", "secret");
    expect(controller.snapshot()).toMatchObject({
      phase: "restricted",
      error: message,
      pending: false,
    });
  },
);

test("authoritative renewal and logout session failures converge on one expiry state", async () => {
  const json = vi
    .fn()
    .mockResolvedValueOnce({
      csrf_token: "csrf",
      user: user({ password_change_required: true }),
    })
    .mockRejectedValueOnce(
      new ApiError(401, "AUTHENTICATION_REQUIRED", "ignored"),
    );
  const controller = controllerWith(json);
  await controller.login("Alice", "temporary");
  await controller.renew("new", "new");
  const expired = controller.snapshot();
  controller.expire();
  expect(controller.snapshot()).toBe(expired);

  json
    .mockResolvedValueOnce({ csrf_token: "csrf", user: user() })
    .mockRejectedValueOnce(new ApiError(0, "CSRF_MISSING", "ignored"));
  await controller.login("Alice", "new");
  await controller.logout();
  expect(controller.snapshot()).toEqual({
    phase: "anonymous",
    notice: "Your session ended. Please sign in again.",
  });
});

test("failed logout retains the principal and late responses cannot overwrite newer state", async () => {
  const json = vi
    .fn()
    .mockResolvedValueOnce({ csrf_token: "csrf", user: user() })
    .mockRejectedValueOnce(new TypeError("network"));
  const controller = controllerWith(json);
  await controller.login("Alice", "password");
  await controller.logout();
  expect(controller.snapshot()).toMatchObject({
    phase: "authenticated",
    error: "Sign-out failed. Try again.",
    pending: false,
  });

  let resolveOld!: (value: unknown) => void;
  json
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    )
    .mockResolvedValueOnce(user({ username: "Current" }));
  const old = controller.load();
  await controller.load();
  resolveOld(user({ username: "Stale" }));
  await old;
  expect(controller.snapshot()).toMatchObject({
    user: { username: "Current" },
  });
});

test("successful logout revokes the visible session and expiry clears an active principal", async () => {
  const json = vi
    .fn()
    .mockResolvedValueOnce({ csrf_token: "csrf", user: user() });
  const controller = controllerWith(json);
  await controller.login("Alice", "password");
  controller.expire();
  expect(controller.snapshot()).toEqual({
    phase: "anonymous",
    notice: "Your session ended. Please sign in again.",
  });
  json
    .mockResolvedValueOnce({ csrf_token: "csrf", user: user() })
    .mockResolvedValueOnce(undefined);
  await controller.login("Alice", "password");
  await controller.logout();
  expect(controller.snapshot()).toEqual({ phase: "anonymous" });
});

test("subscribers receive changes and disposal aborts active requests", async () => {
  let capturedSignal: AbortSignal | undefined;
  const json = vi.fn().mockImplementation((_path, _schema, options) => {
    capturedSignal = options.signal;
    return new Promise(() => undefined);
  });
  const controller = controllerWith(json);
  const listener = vi.fn();
  const unsubscribe = controller.subscribe(listener);
  void controller.load();
  expect(listener).toHaveBeenCalledWith({ phase: "loading" });
  unsubscribe();
  controller.dispose();
  expect(capturedSignal?.aborted).toBe(true);
});
