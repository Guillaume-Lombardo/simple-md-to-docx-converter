import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthController } from "../src/auth/controller";
import { AuthProvider } from "../src/auth/context";
import type { ApiTransport } from "../src/api/transport";
import { ConversionController } from "../src/conversion/controller";
import { ConversionWorkspace } from "../src/conversion/workspace";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../src/conversion/persistence", () => ({
  ownerStorageEpoch: vi.fn().mockReturnValue(0),
  resumeOwnerStorage: vi.fn(),
  readConversionInputs: vi.fn().mockResolvedValue(undefined),
  writeConversionInputs: vi.fn().mockResolvedValue(undefined),
  clearConversionInputs: vi.fn().mockResolvedValue(undefined),
}));

const user = {
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};

test("presentation workspace keeps its PowerPoint output and slide choices separate from document conversion", async () => {
  const json = vi.fn(async (path: string) => {
    if (path.startsWith("/api/v1/conversion-options"))
      return {
        conversion_upload_max_bytes: 1_000_000,
        resolved_template: null,
        selection_source: "pandoc_default",
        template_version_id: null,
      };
    return { items: [], limit: 20, offset: 0, total: 0 };
  });
  const auth = new AuthController({
    json: vi.fn().mockResolvedValue(user),
  } as unknown as ApiTransport);
  const conversion = new ConversionController(
    { json } as unknown as ApiTransport,
    () => auth.expire(),
    () => "presentation-key",
    undefined,
    undefined,
    true,
  );
  render(
    <AuthProvider controller={auth}>
      <ConversionWorkspace controller={conversion} presentation />
    </AuthProvider>,
  );
  expect(
    await screen.findByRole("heading", {
      name: "Create a PowerPoint presentation",
    }),
  ).toBeVisible();
  expect(
    screen.getByRole("radio", { name: "PowerPoint (PPTX)" }),
  ).toBeChecked();
  expect(screen.queryByRole("radio", { name: "DOCX" })).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("radio", { name: "PowerPoint and original source (ZIP)" }),
  );
  expect(conversion.snapshot().output).toBe("pptx-bundle");
  fireEvent.change(screen.getByLabelText("Markdown format"), {
    target: { value: "marp" },
  });
  fireEvent.change(screen.getByLabelText("Slide heading level"), {
    target: { value: "3" },
  });
  expect(conversion.snapshot()).toMatchObject({
    dialect: "marp",
    slideLevel: 3,
  });
  await waitFor(() =>
    expect(json).toHaveBeenCalledWith(
      "/api/v1/conversion-options?template_kind=pptx",
      expect.anything(),
      expect.anything(),
    ),
  );
  expect(json).toHaveBeenCalledWith(
    expect.stringContaining("output_family=presentation"),
    expect.anything(),
    expect.anything(),
  );
});
