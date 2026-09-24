type FlowUnit = {
  article: HTMLElement;
  html: string;
  signature: string;
  placeholder: HTMLDivElement;
  nodes: HTMLElement[] | null;
  initialNodeCount: number;
};

export type DocxWindow = {
  update(): void;
  totalUnits: number;
  mountedUnits(): number;
  totalBlockNodes: number;
  detachedBlockNodes(): number;
  serializedMarkupBytes: number;
  contentPreserved(): boolean;
};

const TARGET_UNIT_HEIGHT = 900;
const MAX_BLOCKS_PER_UNIT = 40;
const MAX_UNIT_NODES = 2_000;
const SAFE_LENGTH = /^-?\d+(?:\.\d+)?(?:px|pt)$/;
const FORBIDDEN_TAGS = new Set([
  "SCRIPT",
  "STYLE",
  "LINK",
  "IFRAME",
  "OBJECT",
  "EMBED",
  "FORM",
  "INPUT",
  "BUTTON",
  "VIDEO",
  "AUDIO",
  "CANVAS",
  "SVG",
  "MATH",
  "META",
  "BASE",
  "TEMPLATE",
]);

function signature(markup: string): string {
  let hash = 2_166_136_261;
  for (let index = 0; index < markup.length; index++) {
    hash ^= markup.charCodeAt(index);
    hash = Math.imul(hash, 16_777_619);
  }
  return `${markup.length}:${hash >>> 0}`;
}

function assertShapeAttributes(element: Element, allowed: Set<string>): void {
  for (const attribute of [...element.attributes]) {
    const name = attribute.name.toLowerCase();
    if (!allowed.has(name))
      throw new Error("The Word preview contains an unsafe shape attribute.");
    if (
      name === "style" &&
      /url\s*\(|@import|expression\s*\(|behavior\s*:|-moz-binding/i.test(
        attribute.value,
      )
    )
      throw new Error("The Word preview contains an unsafe shape style.");
  }
}

/** Replace docx-preview's invalid rect > foreignObject text box with inert HTML. */
export function normalizeDocxTextBoxes(root: HTMLElement): void {
  for (const svg of root.querySelectorAll("svg")) {
    const rect = svg.firstElementChild;
    const foreignObject = rect?.firstElementChild;
    if (
      svg.children.length !== 1 ||
      rect?.localName !== "rect" ||
      rect.children.length !== 1 ||
      foreignObject?.localName !== "foreignObject" ||
      foreignObject.children.length === 0 ||
      [...foreignObject.childNodes].some(
        (node) => node.nodeType !== Node.ELEMENT_NODE,
      ) ||
      svg.style.position !== "absolute" ||
      !SAFE_LENGTH.test(svg.style.width) ||
      !SAFE_LENGTH.test(svg.style.height) ||
      (svg.style.marginTop && !SAFE_LENGTH.test(svg.style.marginTop)) ||
      (svg.style.marginLeft && !SAFE_LENGTH.test(svg.style.marginLeft))
    )
      throw new Error("The Word preview contains an unsupported shape.");
    assertShapeAttributes(svg, new Set(["style", "width", "height"]));
    assertShapeAttributes(rect, new Set(["width", "height"]));
    assertShapeAttributes(foreignObject, new Set(["width", "height"]));
    const bounds = svg.getBoundingClientRect();
    if (
      !Number.isFinite(bounds.width) ||
      !Number.isFinite(bounds.height) ||
      bounds.width < 1 ||
      bounds.height < 1 ||
      bounds.width > 2_000 ||
      bounds.height > 2_000
    )
      throw new Error("The Word preview contains an oversized shape.");
    for (const child of [...foreignObject.children])
      validateRenderedMarkup(child as HTMLElement);
    const box = document.createElement("div");
    box.className = "docx-text-box";
    // An in-flow fallback keeps surrounding text readable when the renderer's
    // floating coordinates cannot be represented safely outside SVG.
    box.style.display = "block";
    box.style.width = `${bounds.width}px`;
    box.style.height = `${bounds.height}px`;
    box.style.marginTop = "4px";
    box.style.marginBottom = "4px";
    box.style.boxSizing = "border-box";
    box.style.border = "1px solid #000";
    box.style.backgroundColor = "#fff";
    box.style.padding = "4pt";
    box.style.overflow = "hidden";
    box.append(...foreignObject.children);
    svg.replaceWith(box);
  }
}

export function validateRenderedMarkup(root: HTMLElement): void {
  for (const element of [root, ...root.querySelectorAll<HTMLElement>("*")]) {
    if (FORBIDDEN_TAGS.has(element.tagName.toUpperCase()))
      throw new Error("The Word preview contains unsupported active markup.");
    for (const attribute of [...element.attributes]) {
      const name = attribute.name.toLowerCase();
      const value = attribute.value.trim();
      if (
        name.startsWith("on") ||
        [
          "srcdoc",
          "srcset",
          "formaction",
          "ping",
          "action",
          "data",
          "xlink:href",
        ].includes(name)
      )
        throw new Error("The Word preview contains an unsafe attribute.");
      if (name === "href") {
        if (element.tagName !== "A")
          throw new Error("The Word preview contains an unsafe link.");
        element.removeAttribute(attribute.name);
      }
      if (
        name === "src" &&
        !/^data:image\/(?:png|jpeg|gif);base64,[a-z0-9+/=]+$/i.test(value)
      )
        throw new Error(
          "The Word preview contains an unsupported image source.",
        );
      if (
        name === "style" &&
        /url\s*\(|@import|expression\s*\(|behavior\s*:|-moz-binding/i.test(
          value,
        )
      )
        throw new Error("The Word preview contains an unsafe style.");
    }
  }
}

function markupOf(nodes: HTMLElement[]): string {
  const serializer = new XMLSerializer();
  return nodes.map((node) => serializer.serializeToString(node)).join("");
}

export function restoreNodes(markup: string): HTMLElement[] {
  if (/<!\s*(?:DOCTYPE|ENTITY)/i.test(markup))
    throw new Error("The Word preview contains unsupported XML markup.");
  const parsed = new DOMParser().parseFromString(
    `<preview xmlns="http://www.w3.org/1999/xhtml">${markup}</preview>`,
    "application/xml",
  );
  if (parsed.querySelector("parsererror"))
    throw new Error("The Word preview could not parse a flow unit.");
  const nodes = Array.from(parsed.documentElement.children).map((element) =>
    document.importNode(element, true),
  );
  if (
    nodes.length === 0 ||
    nodes.some((node) => !(node instanceof HTMLElement))
  )
    throw new Error("The Word preview could not restore a flow unit.");
  for (const node of nodes) validateRenderedMarkup(node as HTMLElement);
  return nodes as HTMLElement[];
}

function currentSpan(unit: FlowUnit): { top: number; bottom: number } {
  if (!unit.nodes) return unit.placeholder.getBoundingClientRect();
  const first = unit.nodes[0]!.getBoundingClientRect();
  const last = unit.nodes.at(-1)!.getBoundingClientRect();
  return { top: first.top, bottom: last.bottom };
}

function mount(unit: FlowUnit): void {
  if (unit.nodes) return;
  const nodes = restoreNodes(unit.html);
  if (markupOf(nodes) !== unit.html)
    throw new Error("The Word preview could not restore a flow unit.");
  if (signature(markupOf(nodes)) !== unit.signature)
    throw new Error("The Word preview flow unit changed during restoration.");
  const nodeCount = nodes.reduce(
    (count, node) => count + 1 + node.querySelectorAll("*").length,
    0,
  );
  if (nodeCount !== unit.initialNodeCount)
    throw new Error("The Word preview flow unit structure changed.");
  unit.placeholder.replaceWith(...nodes);
  unit.nodes = nodes;
}

function unmount(unit: FlowUnit): void {
  if (!unit.nodes) return;
  if (markupOf(unit.nodes) !== unit.html)
    throw new Error("The Word preview flow unit changed during display.");
  unit.nodes[0]!.replaceWith(unit.placeholder);
  for (const node of unit.nodes.slice(1)) node.remove();
  unit.nodes = null;
}

/** Release offscreen DOM after docx-preview's complete initial render. */
export function windowDocx(
  documentRoot: HTMLElement,
  viewport: HTMLElement,
  maximumSerializedBytes: number,
): DocxWindow {
  if (
    !Number.isSafeInteger(maximumSerializedBytes) ||
    maximumSerializedBytes < 1
  )
    throw new Error("Invalid Word preview cache budget.");
  const units: FlowUnit[] = [];
  let serializedMarkupBytes = 0;
  let totalBlockNodes = 0;
  for (const article of documentRoot.querySelectorAll<HTMLElement>(
    "section.docx > article",
  )) {
    const blocks = Array.from(article.children).filter(
      (element): element is HTMLElement => element instanceof HTMLElement,
    );
    let pending: HTMLElement[] = [];
    let firstTop = 0;
    for (const block of blocks) {
      if (pending.length === 0) firstTop = block.getBoundingClientRect().top;
      pending.push(block);
      const height = block.getBoundingClientRect().bottom - firstTop;
      if (
        height < TARGET_UNIT_HEIGHT &&
        pending.length < MAX_BLOCKS_PER_UNIT &&
        block !== blocks.at(-1)
      )
        continue;
      for (const node of pending) validateRenderedMarkup(node);
      const html = markupOf(pending);
      const restored = restoreNodes(html);
      if (
        restored.length !== pending.length ||
        markupOf(restored) !== html ||
        restored.some(
          (node, index) =>
            node.tagName !== pending[index]!.tagName ||
            node.textContent !== pending[index]!.textContent,
        )
      )
        throw new Error(
          "The Word preview contains markup that cannot be restored safely.",
        );
      serializedMarkupBytes += html.length * 2;
      if (serializedMarkupBytes > maximumSerializedBytes)
        throw new Error("The Word preview exceeds its markup budget.");
      const initialNodeCount = pending.reduce(
        (count, node) => count + 1 + node.querySelectorAll("*").length,
        0,
      );
      if (initialNodeCount > MAX_UNIT_NODES)
        throw new Error(
          "The Word preview has an oversized indivisible flow unit.",
        );
      totalBlockNodes += initialNodeCount;
      const first = pending[0]!;
      const last = pending.at(-1)!;
      const placeholder = document.createElement("div");
      placeholder.setAttribute("aria-hidden", "true");
      placeholder.dataset.previewFlowUnit = String(units.length);
      placeholder.style.height = `${Math.max(1, Math.ceil(last.getBoundingClientRect().bottom - firstTop))}px`;
      placeholder.style.marginTop = getComputedStyle(first).marginTop;
      placeholder.style.marginBottom = getComputedStyle(last).marginBottom;
      units.push({
        article,
        html,
        signature: signature(html),
        placeholder,
        nodes: pending,
        initialNodeCount,
      });
      pending = [];
    }
  }
  if (
    documentRoot.querySelectorAll("*").length - totalBlockNodes >
    MAX_UNIT_NODES
  )
    throw new Error("The Word preview contains too much non-flow markup.");

  function update(): void {
    if (units.length <= 3) return;
    const middle =
      viewport.getBoundingClientRect().top + viewport.clientHeight / 2;
    let nearest = 0;
    let distance = Number.POSITIVE_INFINITY;
    for (let index = 0; index < units.length; index++) {
      const rect = currentSpan(units[index]!);
      const delta =
        middle < rect.top
          ? rect.top - middle
          : middle > rect.bottom
            ? middle - rect.bottom
            : 0;
      if (delta < distance) {
        distance = delta;
        nearest = index;
      }
    }
    // Evict first so rehydration never temporarily exceeds three mounted units.
    for (let index = 0; index < units.length; index++)
      if (Math.abs(index - nearest) > 1) unmount(units[index]!);
    for (let index = 0; index < units.length; index++)
      if (Math.abs(index - nearest) <= 1) mount(units[index]!);
  }

  update();
  return {
    update,
    totalUnits: units.length,
    mountedUnits: () => units.filter((unit) => unit.nodes !== null).length,
    totalBlockNodes,
    detachedBlockNodes: () => 0,
    serializedMarkupBytes,
    contentPreserved: () =>
      units.every((unit) => {
        if (unit.nodes && markupOf(unit.nodes) !== unit.html) return false;
        const expected = units
          .filter((item) => item.article === unit.article)
          .flatMap((item) => item.nodes ?? [item.placeholder]);
        const current = Array.from(unit.article.children);
        return (
          current.length === expected.length &&
          current.every((element, index) => element === expected[index])
        );
      }),
  };
}
