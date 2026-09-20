import { fireEvent, render, screen } from "@testing-library/react";
import { ConversionController } from "../src/conversion/controller";
import { PresentationControls } from "../src/conversion/presentation-controls";

test("presentation controls expose slide settings, warnings and optional reference download", () => {
  const controller = new ConversionController(
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    true,
  );
  const setOptions = vi.spyOn(controller, "setPresentationOptions");
  const preview = vi.spyOn(controller, "preview").mockResolvedValue();
  const { rerender } = render(
    <PresentationControls
      controller={controller}
      state={controller.snapshot()}
    />,
  );
  const disclosure = screen.getByText("Slide structure").closest("details")!;
  expect(disclosure).not.toHaveAttribute("open");
  expect(screen.getByLabelText("Markdown format")).not.toBeVisible();
  fireEvent.click(screen.getByText("Slide structure"));
  expect(disclosure).toHaveAttribute("open");
  fireEvent.change(screen.getByLabelText("Markdown format"), {
    target: { value: "marp" },
  });
  expect(setOptions).toHaveBeenLastCalledWith("marp", 2);
  fireEvent.change(screen.getByLabelText("Slide heading level"), {
    target: { value: "3" },
  });
  expect(setOptions).toHaveBeenLastCalledWith("auto", 3);
  fireEvent.click(
    screen.getByRole("button", { name: "Preview slide outline" }),
  );
  expect(preview).toHaveBeenCalledOnce();
  expect(
    screen.getByRole("link", {
      name: "download the default PowerPoint template",
    }),
  ).toHaveAttribute("href", "/api/v1/presentation-reference");
  rerender(
    <PresentationControls
      controller={controller}
      state={{
        ...controller.snapshot(),
        planning: true,
        plan: {
          dialect: "marp",
          slide_level: 2,
          explicit_breaks: true,
          titles: ["First", "Second"],
          warnings: ["Review layout"],
        },
      }}
    />,
  );
  expect(screen.getByText("First")).toBeVisible();
  expect(screen.getByText("Review layout")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Preparing outline…" }),
  ).toBeDisabled();
});
