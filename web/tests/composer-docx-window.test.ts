import { afterEach, describe, expect, it, vi } from "vitest";
import {
  normalizeDocxTextBoxes,
  restoreNodes,
  validateRenderedMarkup,
  windowDocx,
} from "../src/composer/preview/docx-window";

function setup(blocks = 200) {
  document.body.innerHTML =
    '<main id="viewport"><div id="document"><section class="docx"><article></article></section></div></main>';
  const viewport = document.getElementById("viewport")!;
  const root = document.getElementById("document")!;
  const article = root.querySelector("article")!;
  Object.defineProperty(viewport, "clientHeight", { value: 700 });
  for (let index = 0; index < blocks; index++) {
    const paragraph = document.createElement("p");
    paragraph.dataset.blockIndex = String(index);
    paragraph.textContent = `Paragraph ${index}`;
    article.appendChild(paragraph);
  }
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    function (this: HTMLElement) {
      const block = this.dataset.blockIndex;
      const unit = this.dataset.previewFlowUnit;
      const top =
        block || unit
          ? (block ? Number(block) * 30 : Number(unit) * 900) -
            viewport.scrollTop
          : 0;
      const height = block ? 30 : unit ? 900 : 700;
      return {
        top,
        bottom: top + height,
        left: 0,
        right: 1000,
        width: 1000,
        height,
        x: 0,
        y: top,
        toJSON: () => ({}),
      };
    },
  );
  return { viewport, root, article };
}

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

describe("DOCX flow window", () => {
  it("makes a constrained generated text box visible as validated HTML", () => {
    const { root, viewport, article } = setup(1);
    article.querySelector("p")!.innerHTML =
      '<svg style="position:absolute;width:141.75pt;height:56.7pt;margin-top:-68.1pt" width="0" height="0"><rect width="100%" height="100%"><foreignObject width="100%" height="100%"><p class="docx_framecontents"><span>Inside the text box.</span></p></foreignObject></rect></svg>';
    vi.spyOn(SVGElement.prototype, "getBoundingClientRect").mockReturnValue({
      width: 189,
      height: 75.6,
    } as DOMRect);
    normalizeDocxTextBoxes(root);
    expect(article.querySelector("svg,foreignObject")).toBeNull();
    const box = article.querySelector<HTMLElement>(".docx-text-box")!;
    expect(box.textContent).toBe("Inside the text box.");
    expect(box.style.width).toBe("189px");
    expect(box.style.position).toBe("");
    expect(box.style.marginTop).toBe("4px");
    expect(windowDocx(root, viewport, 1_000_000).contentPreserved()).toBe(true);
  });

  it("rejects hostile or unsupported generated-shape lookalikes", () => {
    for (const markup of [
      '<svg style="position:absolute;width:20pt;height:20pt"><rect><foreignObject><script>alert(1)</script></foreignObject></rect></svg>',
      '<svg style="position:absolute;width:20pt;height:20pt"><rect><foreignObject><p style="background:url(https://example.invalid/x)">Bad</p></foreignObject></rect></svg>',
      '<svg style="position:absolute;width:20pt;height:20pt"><rect><foreignObject><p>Text</p></foreignObject></rect><image href="https://example.invalid/x"/></svg>',
      '<svg onload="alert(1)" style="position:absolute;width:20pt;height:20pt"><rect><foreignObject><p>Text</p></foreignObject></rect></svg>',
      '<svg style="position:absolute;width:20pt;height:20pt;background:url(https://example.invalid/x)"><rect><foreignObject><p>Text</p></foreignObject></rect></svg>',
      '<svg style="position:absolute;width:expression(1);height:20pt"><rect><foreignObject><p>Text</p></foreignObject></rect></svg>',
    ]) {
      const { root, article } = setup(1);
      article.querySelector("p")!.innerHTML = markup;
      vi.spyOn(SVGElement.prototype, "getBoundingClientRect").mockReturnValue({
        width: 20,
        height: 20,
      } as DOMRect);
      expect(() => normalizeDocxTextBoxes(root)).toThrow();
      vi.restoreAllMocks();
    }
  });

  it("releases offscreen nodes and restores exact content through distant scrolls", () => {
    const { viewport, root, article } = setup();
    const nested = document.createElement("div");
    nested.textContent = "Nested Word text box";
    article.querySelectorAll("p")[150]!.appendChild(nested);
    const window = windowDocx(root, viewport, 1_000_000);
    expect(window.totalUnits).toBeGreaterThan(3);
    expect(window.mountedUnits()).toBeLessThanOrEqual(3);
    expect(window.detachedBlockNodes()).toBe(0);
    expect(window.contentPreserved()).toBe(true);
    expect(article.querySelectorAll("p").length).toBeLessThan(100);

    viewport.scrollTop = 4_500;
    window.update();
    expect(window.mountedUnits()).toBeLessThanOrEqual(3);
    expect(article.textContent).toContain("Paragraph 150");
    expect(article.querySelector("p div")?.textContent).toBe(
      "Nested Word text box",
    );
    expect(window.contentPreserved()).toBe(true);

    viewport.scrollTop = 0;
    window.update();
    expect(article.textContent).toContain("Paragraph 0");
    expect(window.mountedUnits()).toBeLessThanOrEqual(3);
    expect(window.contentPreserved()).toBe(true);
  });

  it("fails a small cache budget and unsafe rendered attributes", () => {
    const first = setup(20);
    expect(() => windowDocx(first.root, first.viewport, 20)).toThrow(
      "markup budget",
    );
    const second = setup(1);
    const image = document.createElement("img");
    image.src = "https://example.invalid/remote.png";
    second.article.querySelector("p")!.appendChild(image);
    expect(() => windowDocx(second.root, second.viewport, 1_000_000)).toThrow(
      "unsupported image source",
    );
  });

  it("rejects active or non-roundtrippable renderer markup before mounting", () => {
    for (const html of [
      '<p onclick="alert(1)">Click</p>',
      '<p style="background:url(https://example.invalid/a)">CSS</p>',
      '<p><svg xmlns="http://www.w3.org/2000/svg"/></p>',
      "<p><script>alert(1)</script></p>",
    ]) {
      const { root, viewport, article } = setup(0);
      article.innerHTML = html;
      expect(() => windowDocx(root, viewport, 1_000_000)).toThrow();
    }
    const link = document.createElement("a");
    link.href = "https://example.invalid";
    validateRenderedMarkup(link);
    expect(link.hasAttribute("href")).toBe(false);
    expect(() => restoreNodes("<!DOCTYPE html><p/>")).toThrow(
      "unsupported XML",
    );
    expect(() => restoreNodes("<p>")).toThrow("could not parse");
    expect(() =>
      restoreNodes('<svg xmlns="http://www.w3.org/2000/svg"/>'),
    ).toThrow("could not restore");
  });

  it("rejects oversized indivisible and non-flow DOM while preserving mutation checks", () => {
    const oversized = setup(1);
    const paragraph = oversized.article.querySelector("p")!;
    for (let index = 0; index < 2_001; index++)
      paragraph.appendChild(document.createElement("span"));
    expect(() =>
      windowDocx(oversized.root, oversized.viewport, 1_000_000),
    ).toThrow("oversized indivisible");

    const nonflow = setup(1);
    const section = nonflow.root.querySelector("section")!;
    for (let index = 0; index < 2_001; index++)
      section.appendChild(document.createElement("span"));
    expect(() => windowDocx(nonflow.root, nonflow.viewport, 1_000_000)).toThrow(
      "non-flow markup",
    );

    const changed = setup();
    const window = windowDocx(changed.root, changed.viewport, 1_000_000);
    changed.article.querySelector("p")!.textContent = "Changed after render";
    expect(window.contentPreserved()).toBe(false);
    changed.viewport.scrollTop = 4_500;
    expect(() => window.update()).toThrow("changed during display");
    expect(() => windowDocx(changed.root, changed.viewport, 0)).toThrow(
      "Invalid Word preview cache budget",
    );
  });
});
