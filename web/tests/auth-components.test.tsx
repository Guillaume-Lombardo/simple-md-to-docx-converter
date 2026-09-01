import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import type { ReactNode } from "react";
import { LoginPage, PasswordRenewalPage } from "../components/auth";
import { AuthProvider, Protected } from "../src/auth/context";
import { AuthController } from "../src/auth/controller";
import { ApiError, type ApiTransport } from "../src/api/transport";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const regular = {
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};

function setup(json: ReturnType<typeof vi.fn>, child: ReactNode) {
  const controller = new AuthController({ json } as unknown as ApiTransport);
  return {
    controller,
    ...render(<AuthProvider controller={controller}>{child}</AuthProvider>),
  };
}

beforeEach(() => replace.mockReset());

test("login is accessible, non-reflective, and routes an authenticated user", async () => {
  const json = vi
    .fn()
    .mockRejectedValueOnce(
      new ApiError(401, "AUTHENTICATION_REQUIRED", "ignored"),
    )
    .mockResolvedValueOnce({ csrf_token: "secret", user: regular });
  setup(json, <LoginPage />);
  expect(await screen.findByRole("heading", { name: "Sign in" })).toBeVisible();
  const username = screen.getByRole("textbox", { name: "Username" });
  const password = screen.getByLabelText("Password");
  expect(username).toHaveAttribute("autocomplete", "username");
  expect(password).toHaveAttribute("autocomplete", "current-password");
  fireEvent.change(username, { target: { value: "Alice" } });
  fireEvent.change(password, { target: { value: "private-password" } });
  fireEvent.submit(
    screen.getByRole("button", { name: "Sign in" }).closest("form")!,
  );
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/convert"));
  expect(document.body).not.toHaveTextContent("private-password");
});

test("renewal provides labelled fields, logout, duration, and fresh-login navigation", async () => {
  const restricted = {
    ...regular,
    effective_idle_minutes: 17,
    password_change_required: true,
  };
  const json = vi
    .fn()
    .mockResolvedValueOnce(restricted)
    .mockResolvedValueOnce(undefined);
  setup(json, <PasswordRenewalPage />);
  expect(
    await screen.findByRole("heading", { name: "Change your password" }),
  ).toBeVisible();
  expect(screen.getByText(/17 minutes of inactivity/)).toBeVisible();
  expect(screen.getByRole("link", { name: "Templates" })).toBeVisible();
  expect(screen.queryByRole("link", { name: "Users" })).toBeNull();
  const fields = screen.getAllByLabelText(/password/i);
  for (const field of fields)
    expect(field).toHaveAttribute("autocomplete", "new-password");
  fireEvent.change(fields[0]!, { target: { value: "renewed" } });
  fireEvent.change(fields[1]!, { target: { value: "renewed" } });
  fireEvent.submit(
    screen.getByRole("button", { name: "Change password" }).closest("form")!,
  );
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
});

test("protected navigation redirects anonymous and restricted sessions", async () => {
  setup(
    vi
      .fn()
      .mockRejectedValue(
        new ApiError(401, "AUTHENTICATION_REQUIRED", "ignored"),
      ),
    <Protected>Private</Protected>,
  );
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  replace.mockReset();
  setup(
    vi.fn().mockResolvedValue({ ...regular, password_change_required: true }),
    <Protected>Private</Protected>,
  );
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/change-password"));
});

test("protected unavailable state retries current-user loading", async () => {
  const json = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("network"))
    .mockResolvedValueOnce(regular);
  setup(
    json,
    <Protected>
      <span>Private</span>
    </Protected>,
  );
  expect(
    await screen.findByRole("heading", { name: "Markweave is unavailable" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText("Private")).toBeVisible();
});

test("login unavailable state retries without exposing the failure", async () => {
  const json = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("private endpoint"))
    .mockRejectedValueOnce(
      new ApiError(401, "AUTHENTICATION_REQUIRED", "ignored"),
    );
  setup(json, <LoginPage />);
  expect(
    await screen.findByText("Markweave is unavailable. Try again shortly."),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("heading", { name: "Sign in" })).toBeVisible();
  expect(document.body).not.toHaveTextContent("private endpoint");
});

test("restricted login routes directly to renewal", async () => {
  const json = vi
    .fn()
    .mockResolvedValue({ ...regular, password_change_required: true });
  setup(json, <LoginPage />);
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/change-password"));
});

test("renewal displays fixed validation errors and permits sign out", async () => {
  const restricted = { ...regular, password_change_required: true };
  const json = vi
    .fn()
    .mockResolvedValueOnce(restricted)
    .mockRejectedValueOnce(
      new ApiError(422, "PASSWORD_CONFIRMATION_INVALID", "unsafe"),
    )
    .mockResolvedValueOnce(undefined);
  setup(json, <PasswordRenewalPage />);
  expect(
    await screen.findByRole("heading", { name: "Change your password" }),
  ).toBeVisible();
  fireEvent.submit(
    screen.getByRole("button", { name: "Change password" }).closest("form")!,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The passwords do not match.",
  );
  fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
});

test("authenticated protected content is rendered", async () => {
  setup(
    vi.fn().mockResolvedValue(regular),
    <Protected>
      <span>Private work</span>
    </Protected>,
  );
  expect(await screen.findByText("Private work")).toBeVisible();
});
