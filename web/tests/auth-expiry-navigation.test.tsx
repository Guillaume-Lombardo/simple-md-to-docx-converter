import { act, render, screen, waitFor } from "@testing-library/react";
import { AuthController } from "../src/auth/controller";
import { AuthProvider, Protected } from "../src/auth/context";
import { clearOwnerDraftInputs } from "../src/auth/local-drafts";
import { redirectProtected } from "../src/auth/navigation";
import type { ApiTransport } from "../src/api/transport";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));
vi.mock("../src/auth/local-drafts", () => ({
  clearOwnerDraftInputs: vi.fn(),
  resumeOwnerDraftInputs: vi.fn(),
}));
vi.mock("../src/auth/navigation", () => ({
  redirectProtected: vi.fn(),
}));

test("Composer expiry hides private content and waits for owner cleanup before navigation", async () => {
  let releaseCleanup!: () => void;
  vi.mocked(clearOwnerDraftInputs).mockImplementationOnce(
    () =>
      new Promise<void>((resolve) => {
        releaseCleanup = resolve;
      }),
  );
  const owner = {
    active: true,
    effective_idle_minutes: 30,
    id: "00000000-0000-4000-8000-000000000001",
    password_change_required: false,
    role: "user",
    username: "Alice",
  };
  const controller = new AuthController({
    json: vi.fn().mockResolvedValue(owner),
  } as unknown as ApiTransport);
  render(
    <AuthProvider controller={controller}>
      <Protected>Private document</Protected>
    </AuthProvider>,
  );
  expect(await screen.findByText("Private document")).toBeVisible();
  act(() => controller.expire());
  expect(screen.queryByText("Private document")).toBeNull();
  expect(screen.getByText("Opening sign in…")).toBeVisible();
  expect(controller.snapshot()).toEqual({ phase: "expiring" });
  await waitFor(() =>
    expect(clearOwnerDraftInputs).toHaveBeenCalledWith(owner.id),
  );
  expect(redirectProtected).not.toHaveBeenCalled();
  await act(async () => releaseCleanup());
  await waitFor(() =>
    expect(redirectProtected).toHaveBeenCalledWith(
      "/login",
      expect.any(Function),
    ),
  );
  expect(redirectProtected).toHaveBeenCalledOnce();
});
