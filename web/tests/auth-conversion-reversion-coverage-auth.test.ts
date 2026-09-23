import type { UserResponse } from "../src/api/generated/types.gen";
import { ApiError, type ApiTransport } from "../src/api/transport";
import { AuthController } from "../src/auth/controller";
import { clearConversionInputs } from "../src/conversion/persistence";

vi.mock("../src/conversion/persistence", () => ({
  clearConversionInputs: vi.fn().mockResolvedValue(undefined),
  resumeOwnerStorage: vi.fn(),
}));

const aliceId = "00000000-0000-4000-8000-000000000001";
const bobId = "00000000-0000-4000-8000-000000000002";

function user(id = aliceId, restricted = false): UserResponse {
  return {
    active: true,
    effective_idle_minutes: 30,
    id,
    password_change_required: restricted,
    role: "user",
    username: id === aliceId ? "Alice" : "Bob",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  sessionStorage.clear();
  vi.mocked(clearConversionInputs).mockReset().mockResolvedValue(undefined);
});

test("logout clears only the signed-out owner and cannot retire a newer identity", async () => {
  const cleanup = deferred<void>();
  vi.mocked(clearConversionInputs).mockImplementationOnce(
    () => cleanup.promise,
  );
  const json = vi
    .fn()
    .mockResolvedValueOnce({ user: user(), csrf_token: "unused" })
    .mockResolvedValueOnce(undefined)
    .mockResolvedValueOnce(user(bobId));
  const controller = new AuthController({ json } as unknown as ApiTransport);
  await controller.login("Alice", "password");
  sessionStorage.setItem(`composer:message:${aliceId}:draft`, "Alice's note");
  sessionStorage.setItem(`composer:message:${bobId}:draft`, "Bob's note");

  const logout = controller.logout();
  await vi.waitFor(() =>
    expect(clearConversionInputs).toHaveBeenCalledWith(aliceId),
  );
  await controller.load();
  cleanup.resolve();
  await logout;

  expect(controller.snapshot()).toMatchObject({
    phase: "authenticated",
    user: { id: bobId },
  });
  expect(
    sessionStorage.getItem(`composer:message:${aliceId}:draft`),
  ).toBeNull();
  expect(sessionStorage.getItem(`composer:message:${bobId}:draft`)).toBe(
    "Bob's note",
  );
});

test("expired renewal clears private inputs even if IndexedDB cleanup fails", async () => {
  vi.mocked(clearConversionInputs).mockRejectedValueOnce(
    new DOMException("Quota failure", "QuotaExceededError"),
  );
  const json = vi
    .fn()
    .mockResolvedValueOnce({ user: user(aliceId, true), csrf_token: "unused" })
    .mockRejectedValueOnce(new ApiError(401, "SESSION_EXPIRED", "private"));
  const controller = new AuthController({ json } as unknown as ApiTransport);
  await controller.login("Alice", "temporary");
  sessionStorage.setItem(
    `composer:content:${aliceId}:draft`,
    "private correction",
  );
  await controller.renew("new password", "new password");

  expect(controller.snapshot()).toEqual({
    phase: "anonymous",
    pending: false,
    notice: "Your session ended. Please sign in again.",
  });
  expect(
    sessionStorage.getItem(`composer:content:${aliceId}:draft`),
  ).toBeNull();
  expect(clearConversionInputs).toHaveBeenCalledWith(aliceId);
});

test("a revoked session response never accepts invalid idle policy after a retry", async () => {
  const json = vi
    .fn()
    .mockResolvedValueOnce({ ...user(), effective_idle_minutes: 30.5 })
    .mockResolvedValueOnce(user());
  const controller = new AuthController({ json } as unknown as ApiTransport);
  await controller.load();
  expect(controller.snapshot()).toEqual({ phase: "unavailable" });
  await controller.load();
  expect(controller.snapshot()).toMatchObject({ phase: "authenticated" });
});
