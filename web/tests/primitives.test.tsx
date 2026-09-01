import { fireEvent, render, screen } from "@testing-library/react";
import {
  Alert,
  AppShell,
  DataTable,
  Dialog,
  ItemList,
  LoadingStatus,
  Progress,
  TextField,
} from "../components/primitives";
import GlobalError from "../app/global-error";

test("application shell exposes navigation and skip target", () => {
  render(
    <AppShell current="Convert">
      <h1>Work</h1>
    </AppShell>,
  );
  expect(screen.getByRole("navigation", { name: "Primary" })).toBeVisible();
  expect(screen.getByRole("link", { name: "Convert" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(screen.getByRole("link", { name: "Templates" })).toBeVisible();
  expect(screen.getByRole("main")).toHaveAttribute("id", "main");
});

test("administrator shell shows identity, inactivity policy, users, and pending sign-out", () => {
  render(
    <AppShell
      current="Templates"
      onLogout={vi.fn()}
      pending
      user={{
        active: true,
        effective_idle_minutes: 15,
        id: "00000000-0000-4000-8000-000000000001",
        password_change_required: false,
        role: "admin",
        username: "Admin",
      }}
    >
      Work
    </AppShell>,
  );
  expect(screen.getByRole("link", { name: "Templates" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(screen.getByRole("link", { name: "Users" })).toHaveAttribute(
    "href",
    "/templates#users",
  );
  expect(screen.getByText("Admin (Administrator)")).toBeVisible();
  expect(screen.getByText(/15 minutes of inactivity/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Sign out" })).toBeDisabled();
});

test("form and status primitives retain accessible names", () => {
  render(
    <>
      <TextField label="Name" name="name" />
      <Alert tone="danger">Failed</Alert>
      <LoadingStatus loading>Ignored</LoadingStatus>
      <Progress label="Upload" value={42} />
    </>,
  );
  expect(screen.getByRole("textbox", { name: "Name" })).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("Failed");
  expect(screen.getByText("Loading…")).toHaveAttribute("aria-busy", "true");
  expect(screen.getByRole("progressbar", { name: "Upload" })).toHaveValue(42);
});

test("dialog, table, and list primitives expose structure", () => {
  const onClose = vi.fn();
  render(
    <>
      <Dialog onClose={onClose} open title="Confirm">
        Body
      </Dialog>
      <Dialog onClose={onClose} open title="Second">
        More
      </Dialog>
      <DataTable caption="Jobs">
        <tbody>
          <tr>
            <td>One</td>
          </tr>
        </tbody>
      </DataTable>
      <ItemList label="Templates">
        <li>Default</li>
      </ItemList>
    </>,
  );
  expect(screen.getByRole("dialog", { name: "Confirm" })).toBeVisible();
  const dialogs = screen.getAllByRole("dialog");
  expect(dialogs[0]!.getAttribute("aria-labelledby")).not.toBe(
    dialogs[1]!.getAttribute("aria-labelledby"),
  );
  expect(screen.getByRole("table", { name: "Jobs" })).toBeVisible();
  expect(screen.getByRole("list", { name: "Templates" })).toBeVisible();
});

test("dialog is modal, handles Escape, closes, and restores focus", () => {
  const onClose = vi.fn();
  const trigger = document.createElement("button");
  document.body.append(trigger);
  trigger.focus();
  const { rerender } = render(
    <Dialog onClose={onClose} open title="Confirm">
      Body
    </Dialog>,
  );
  const dialog = screen.getByRole("dialog");
  expect(dialog).toHaveAttribute("open");
  fireEvent(dialog, new Event("cancel", { bubbles: false, cancelable: true }));
  expect(onClose).toHaveBeenCalledOnce();
  rerender(
    <Dialog onClose={onClose} open={false} title="Confirm">
      Body
    </Dialog>,
  );
  expect(dialog).not.toHaveAttribute("open");
  expect(trigger).toHaveFocus();
  trigger.remove();
});

test("informational and settled loading states render", () => {
  render(
    <>
      <Alert>Notice</Alert>
      <LoadingStatus loading={false}>Ready</LoadingStatus>
    </>,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("Notice");
  expect(screen.getByText("Ready")).toHaveAttribute("aria-busy", "false");
});

test("global error exposes a safe retry without reflecting error details", () => {
  const reset = vi.fn();
  const { container } = render(
    <GlobalError error={new Error("private detail")} reset={reset} />,
  );
  expect(
    screen.getByRole("heading", { name: "Something went wrong" }),
  ).toBeVisible();
  expect(container).not.toHaveTextContent("private detail");
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(reset).toHaveBeenCalledOnce();
  expect(container.querySelector("[style]")).toBeNull();
});
