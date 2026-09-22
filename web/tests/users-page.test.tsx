import { render, screen, waitFor } from "@testing-library/react";
import UsersPage from "../app/users/page";
import SessionPolicyPage from "../app/session-policy/page";
import { AuthProvider } from "../src/auth/context";
import { AuthController } from "../src/auth/controller";
import { ApiError, type ApiTransport } from "../src/api/transport";

const replace = vi.fn();
const redirect = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  redirect: (path: string) => redirect(path),
}));
vi.mock("../src/admin/users", () => ({
  UsersWorkspace: () => <h1>Accounts workspace</h1>,
}));
vi.mock("../src/admin/session-policy", () => ({
  SessionPolicyWorkspace: () => <h2>Policy workspace</h2>,
}));

beforeEach(() => vi.clearAllMocks());

test.each(["admin", "user"])(
  "%s receives one Users tab and only administrators receive policy controls",
  async (role) => {
    const json = vi.fn().mockResolvedValue({
      active: true,
      effective_idle_minutes: 60,
      id: "00000000-0000-4000-8000-000000000001",
      password_change_required: false,
      role,
      username: "Account",
    });
    const controller = new AuthController({ json } as unknown as ApiTransport);
    render(
      <AuthProvider controller={controller}>
        <UsersPage />
      </AuthProvider>,
    );
    expect(await screen.findByText("Accounts workspace")).toBeVisible();
    expect(screen.queryByText(/minutes of inactivity/)).toBeNull();
    expect(screen.queryByRole("link", { name: "Session policy" })).toBeNull();
    if (role === "admin") {
      expect(screen.getByRole("link", { name: "Users" })).toHaveAttribute(
        "aria-current",
        "page",
      );
      const summary = screen.getByText("Session policy");
      expect(summary.tagName).toBe("SUMMARY");
      const details = summary.closest("details")!;
      expect(details).not.toHaveAttribute("open");
      expect(screen.getByText("Policy workspace")).not.toBeVisible();
      details.open = true;
      expect(screen.getByText("Policy workspace")).toBeVisible();
      details.open = false;
      expect(screen.getByText("Policy workspace")).not.toBeVisible();
    } else {
      expect(screen.queryByRole("link", { name: "Users" })).toBeNull();
      expect(screen.queryByText("Session policy")).toBeNull();
      expect(screen.queryByText("Policy workspace")).toBeNull();
    }
  },
);

test("anonymous visitors must authenticate before viewing user settings", async () => {
  const json = vi
    .fn()
    .mockRejectedValue(new ApiError(401, "AUTHENTICATION_REQUIRED", "ignored"));
  const controller = new AuthController({ json } as unknown as ApiTransport);
  render(
    <AuthProvider controller={controller}>
      <UsersPage />
    </AuthProvider>,
  );
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  expect(screen.queryByText("Accounts workspace")).toBeNull();
  expect(screen.queryByText("Policy workspace")).toBeNull();
});

test("the former session policy URL redirects to the unified Users page", () => {
  SessionPolicyPage();
  expect(redirect).toHaveBeenCalledWith("/users");
});
