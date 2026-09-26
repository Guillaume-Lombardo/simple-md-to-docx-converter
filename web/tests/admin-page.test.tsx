import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import AdminPage from "../app/admin/page";
import { AuthController } from "../src/auth/controller";
import { AuthProvider } from "../src/auth/context";
import type { ApiTransport } from "../src/api/transport";

const replace = vi.fn();
const usersMounted = vi.fn();
const policyMounted = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));
vi.mock("../src/admin/users", () => ({
  UsersWorkspace: ({
    expire,
    headingLevel,
  }: {
    expire: () => void;
    headingLevel: "h2";
  }) => {
    usersMounted();
    const Heading = headingLevel;
    return (
      <>
        <Heading>Users</Heading>
        <button onClick={expire} type="button">
          Expire account session
        </button>
      </>
    );
  },
}));
vi.mock("../src/admin/session-policy", () => ({
  SessionPolicyWorkspace: ({ headingLevel }: { headingLevel: "h3" }) => {
    policyMounted();
    const Heading = headingLevel;
    return (
      <>
        <Heading>Session policy controls</Heading>
        <p>Embedded policy controls</p>
      </>
    );
  },
}));

function show(role: "admin" | "user", passwordChangeRequired = false) {
  const controller = new AuthController({
    json: vi.fn().mockResolvedValue({
      active: true,
      effective_idle_minutes: 30,
      id: "00000000-0000-4000-8000-000000000001",
      password_change_required: passwordChangeRequired,
      role,
      username: "Account",
    }),
  } as unknown as ApiTransport);
  render(
    <AuthProvider controller={controller}>
      <AdminPage />
    </AuthProvider>,
  );
  return controller;
}

beforeEach(() => vi.clearAllMocks());

test("administrator opens inline accounts and policy without hidden requests", async () => {
  const controller = show("admin");
  const expire = vi.spyOn(controller, "expire");
  const usersSummary = await screen.findByText("Users and sessions");
  expect(usersSummary.tagName).toBe("SUMMARY");
  expect(usersMounted).not.toHaveBeenCalled();
  expect(policyMounted).not.toHaveBeenCalled();
  expect(screen.queryByRole("link", { name: "Users" })).toBeNull();

  usersSummary.focus();
  fireEvent.click(usersSummary);
  expect(usersSummary).toHaveFocus();
  expect(
    await screen.findByRole("button", { name: "Expire account session" }),
  ).toBeVisible();
  expect(
    screen.getByRole("heading", { name: "Users", level: 2 }),
  ).toBeVisible();
  expect(policyMounted).not.toHaveBeenCalled();

  const policySummary = screen.getByText("Session policy");
  expect(policySummary.tagName).toBe("SUMMARY");
  fireEvent.click(policySummary);
  expect(await screen.findByText("Embedded policy controls")).toBeVisible();
  expect(
    screen.getByRole("heading", { name: "Session policy controls", level: 3 }),
  ).toBeVisible();

  fireEvent.click(usersSummary);
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Expire account session" }),
    ).toBeNull(),
  );
  expect(screen.queryByText("Embedded policy controls")).toBeNull();
  fireEvent.click(usersSummary);
  expect(
    await screen.findByRole("button", { name: "Expire account session" }),
  ).toBeVisible();
  expect(policyMounted).toHaveBeenCalledTimes(1);
  fireEvent.click(
    screen.getByRole("button", { name: "Expire account session" }),
  );
  expect(expire).toHaveBeenCalledOnce();
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
});

test("setup sections keep their authorized routes behind summaries", async () => {
  show("admin");
  const llmSummary = await screen.findByText("LLM settings");
  expect(
    screen.getByRole("link", { name: "Open LLM settings" }),
  ).not.toBeVisible();
  fireEvent.click(llmSummary);
  expect(
    screen.getByRole("link", { name: "Open LLM settings" }),
  ).toHaveAttribute("href", "/admin/llm");
  fireEvent.click(screen.getByText("Templates"));
  expect(screen.getByRole("link", { name: "Open templates" })).toHaveAttribute(
    "href",
    "/templates",
  );
  fireEvent.click(screen.getByText("Typed filling templates"));
  expect(
    screen.getByRole("link", { name: "Open typed filling templates" }),
  ).toHaveAttribute("href", "/composer/fill-templates");
});

test.each(["user", "renewal"] as const)(
  "%s cannot mount administration controls",
  async (state) => {
    show(state === "user" ? "user" : "admin", state === "renewal");
    if (state === "renewal") {
      await waitFor(() =>
        expect(replace).toHaveBeenCalledWith("/change-password"),
      );
    } else {
      expect(
        await screen.findByText(
          "You are not allowed to view administration settings.",
        ),
      ).toBeVisible();
    }
    expect(screen.queryByText("Users and sessions")).toBeNull();
    expect(usersMounted).not.toHaveBeenCalled();
    expect(policyMounted).not.toHaveBeenCalled();
  },
);
