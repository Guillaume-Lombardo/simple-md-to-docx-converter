import type { PresentationDialect } from "../api/generated/types.gen";
import type { ConversionController, ConversionState } from "./controller";

export function PresentationControls({
  controller,
  state,
}: {
  controller: ConversionController;
  state: ConversionState;
}) {
  return (
    <details className="space-y-3">
      <summary className="cursor-pointer font-semibold">
        Slide structure
      </summary>
      <p>
        Use headings or horizontal rules (---) to separate slides. Marp styling
        is adapted to editable PowerPoint layouts. Review the outline and
        compatibility warnings before generating.
      </p>
      <label className="grid gap-2">
        Markdown format
        <select
          value={state.dialect}
          onChange={(event) =>
            controller.setPresentationOptions(
              event.target.value as PresentationDialect,
              state.slideLevel,
            )
          }
        >
          <option value="auto">Detect Markdown or Marp</option>
          <option value="markdown">Markdown</option>
          <option value="marp">Marp</option>
        </select>
      </label>
      <label className="grid gap-2">
        Slide heading level
        <select
          value={state.slideLevel}
          onChange={(event) =>
            controller.setPresentationOptions(
              state.dialect,
              Number(event.target.value),
            )
          }
        >
          {[1, 2, 3, 4, 5, 6].map((level) => (
            <option key={level} value={level}>
              {"#".repeat(level)}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        disabled={state.planning}
        onClick={() => void controller.preview()}
      >
        {state.planning ? "Preparing outline…" : "Preview slide outline"}
      </button>
      {state.plan && (
        <div aria-live="polite" className="space-y-2">
          <h4 className="font-semibold">Slide outline</h4>
          <ol className="list-inside list-decimal">
            {state.plan.titles.map((title, index) => (
              <li key={index}>{title}</li>
            ))}
          </ol>
          <ul aria-label="Presentation warnings">
            {state.plan.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
      <p>
        No template is required: Pandoc provides its native PowerPoint design.
        To customize it,{" "}
        <a
          className="text-accent underline"
          href="/api/v1/presentation-reference"
        >
          download the default PowerPoint template
        </a>{" "}
        and upload your edited copy in templates.
      </p>
      <p>
        The source ZIP preserves the original input, including images in an
        uploaded ZIP. It does not incorporate edits made later in PowerPoint.
      </p>
    </details>
  );
}
