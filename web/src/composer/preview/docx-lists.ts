const WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";

type Level = { format: string; text: string; start: number };
type Numbering = {
  abstractId: string;
  overrides: Map<number, number>;
};
const SUPPORTED_FORMATS = new Set([
  "decimal",
  "upperRoman",
  "lowerRoman",
  "upperLetter",
  "lowerLetter",
  "bullet",
  "none",
]);

function children(element: Element, name: string): Element[] {
  return [...element.children].filter(
    (child) => child.namespaceURI === WORD_NS && child.localName === name,
  );
}

function value(element: Element | undefined, name: string): string | null {
  return element?.getAttributeNS(WORD_NS, name) ?? null;
}

function integer(raw: string | null, fallback: number): number {
  if (raw === null || !/^[0-9]{1,7}$/.test(raw)) return fallback;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) ? parsed : fallback;
}

function letters(number: number): string {
  let value = number;
  let result = "";
  while (value > 0 && result.length < 8) {
    value -= 1;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }
  return value === 0 ? result : "";
}

function roman(number: number): string {
  if (number < 1 || number > 3_999) return "";
  let remainder = number;
  let result = "";
  for (const [amount, digits] of [
    [1_000, "M"],
    [900, "CM"],
    [500, "D"],
    [400, "CD"],
    [100, "C"],
    [90, "XC"],
    [50, "L"],
    [40, "XL"],
    [10, "X"],
    [9, "IX"],
    [5, "V"],
    [4, "IV"],
    [1, "I"],
  ] as const) {
    while (remainder >= amount) {
      result += digits;
      remainder -= amount;
    }
  }
  return result;
}

function displayNumber(number: number, format: string): string | null {
  if (number < 1 || number > 999_999) return null;
  switch (format) {
    case "decimal":
      return String(number);
    case "upperRoman":
      return roman(number) || null;
    case "lowerRoman":
      return roman(number).toLowerCase() || null;
    case "upperLetter":
      return letters(number) || null;
    case "lowerLetter":
      return letters(number).toLowerCase() || null;
    default:
      return null;
  }
}

/** Materialize safe, static list labels so flow-unit eviction cannot reset CSS counters. */
export function normalizeDocxLists(
  root: HTMLElement,
  numberingXml: string,
): number {
  if (/<!\s*(?:DOCTYPE|ENTITY)/i.test(numberingXml))
    throw new Error("The Word preview has unsupported numbering XML.");
  const parsed = new DOMParser().parseFromString(
    numberingXml,
    "application/xml",
  );
  if (parsed.querySelector("parsererror"))
    throw new Error("The Word preview has invalid numbering.");
  const abstracts = new Map<string, Map<number, Level>>();
  for (const abstract of parsed.getElementsByTagNameNS(
    WORD_NS,
    "abstractNum",
  )) {
    const id = value(abstract, "abstractNumId");
    if (!id) continue;
    const levels = new Map<number, Level>();
    for (const element of children(abstract, "lvl")) {
      const level = integer(value(element, "ilvl"), -1);
      if (level < 0 || level > 8) continue;
      levels.set(level, {
        format: value(children(element, "numFmt")[0], "val") ?? "decimal",
        text: value(children(element, "lvlText")[0], "val") ?? `%${level + 1}.`,
        start: integer(value(children(element, "start")[0], "val"), 1),
      });
    }
    abstracts.set(id, levels);
  }
  const numberings = new Map<string, Numbering>();
  for (const num of parsed.getElementsByTagNameNS(WORD_NS, "num")) {
    const id = value(num, "numId");
    const abstractId = value(children(num, "abstractNumId")[0], "val");
    if (!id || !abstractId) continue;
    const overrides = new Map<number, number>();
    for (const override of children(num, "lvlOverride")) {
      const level = integer(value(override, "ilvl"), -1);
      const start = integer(
        value(children(override, "startOverride")[0], "val"),
        -1,
      );
      if (level >= 0 && level <= 8 && start > 0) overrides.set(level, start);
    }
    numberings.set(id, { abstractId, overrides });
  }

  const counters = new Map<string, number>();
  const seen = new Set<string>();
  let labeled = 0;
  for (const paragraph of root.querySelectorAll<HTMLParagraphElement>("p")) {
    const match = [...paragraph.classList]
      .map((name) => /^docx-num-(\d+)-(\d+)$/.exec(name))
      .find((entry) => entry !== null);
    if (!match) continue;
    const numId = match[1]!;
    const num = numberings.get(numId);
    const levelIndex = Number(match[2]);
    const levels = num && abstracts.get(num.abstractId);
    const level = levels?.get(levelIndex);
    if (!num || !levels || !level)
      throw new Error("The Word preview contains unsupported numbering.");
    if (!SUPPORTED_FORMATS.has(level.format))
      throw new Error(
        "The Word preview contains an unsupported numbering format.",
      );
    if (level.format === "none") continue;
    let label: string | null = null;
    if (level.format === "bullet") {
      label = level.text.replaceAll("\uf0b7", "•").replaceAll("\uf0a7", "▪");
      if (/[\ue000-\uf8ff]/.test(label))
        throw new Error("The Word preview contains an unsupported bullet.");
    } else {
      const key = `${numId}:${levelIndex}`;
      const seenKey = key;
      const override = !seen.has(seenKey)
        ? num.overrides.get(levelIndex)
        : undefined;
      seen.add(seenKey);
      const previous =
        override !== undefined
          ? override - 1
          : (counters.get(key) ?? level.start - 1);
      const current = previous + 1;
      counters.set(key, current);
      for (let nested = levelIndex + 1; nested <= 8; nested++)
        counters.delete(`${numId}:${nested}`);
      let supported = true;
      label = level.text.replace(/%([1-9])/g, (_token, levelNumber: string) => {
        const referencedIndex = Number(levelNumber) - 1;
        const referenced = levels.get(referencedIndex);
        const count = counters.get(`${numId}:${referencedIndex}`);
        const formatted =
          referenced && count !== undefined
            ? displayNumber(count, referenced.format)
            : null;
        if (formatted === null) supported = false;
        return formatted ?? "";
      });
      if (!supported || label.includes("%") || !label.trim())
        throw new Error("The Word preview contains unsupported numbering.");
    }
    if (!label || label.length > 64 || /[\u0000-\u001f\u007f]/.test(label))
      throw new Error("The Word preview contains an unsupported list label.");
    paragraph.dataset.previewListLabel = label;
    labeled++;
  }
  if (labeled > 0) {
    const style = document.createElement("style");
    style.textContent =
      "p[data-preview-list-label]::before{content:attr(data-preview-list-label)!important;counter-increment:none!important;font-family:serif!important}";
    root.append(style);
  }
  return labeled;
}
