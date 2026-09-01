import { render, screen } from "@testing-library/react";
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
  expect(screen.getByRole("main")).toHaveAttribute("id", "main");
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
  render(
    <>
      <Dialog open title="Confirm">
        Body
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
  expect(screen.getByRole("table", { name: "Jobs" })).toBeVisible();
  expect(screen.getByRole("list", { name: "Templates" })).toBeVisible();
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
