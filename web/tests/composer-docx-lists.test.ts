import { afterEach, describe, expect, it } from "vitest";
import { normalizeDocxLists } from "../src/composer/preview/docx-lists";

const numbering = `<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="4">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="upperRoman"/><w:lvlText w:val="%1."/></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="3">
    <w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val="&#xF0B7;"/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="4"><w:abstractNumId w:val="4"/></w:num>
  <w:num w:numId="5"><w:abstractNumId w:val="4"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="4"/></w:lvlOverride></w:num>
  <w:num w:numId="3"><w:abstractNumId w:val="3"/></w:num>
</w:numbering>`;

afterEach(() => {
  document.body.innerHTML = "";
});

describe("DOCX list labels", () => {
  it("keeps concrete numbering instances separate despite a shared abstract style", () => {
    document.body.innerHTML = `<div id="doc">
      <p class="docx-num-5-0">Roman starting at four</p>
      <p class="docx-num-4-0">Roman starting at one</p>
      <p class="docx-num-4-0">Second item in its own instance</p>
      <p class="docx-num-3-0">Bullet one</p>
      <p class="docx-num-3-0">Bullet two</p>
    </div>`;
    const root = document.getElementById("doc")!;
    expect(normalizeDocxLists(root, numbering)).toBe(5);
    expect(
      [...root.querySelectorAll("p")].map(
        (item) => item.dataset.previewListLabel,
      ),
    ).toEqual(["IV.", "I.", "II.", "•", "•"]);
    expect(root.querySelector("style")?.textContent).toContain(
      "content:attr(data-preview-list-label)",
    );
  });

  it("resets nested levels within each concrete instance and applies startOverride once", () => {
    const multilevel = `<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:abstractNum w:abstractNumId="1">
        <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
        <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%1.%2."/></w:lvl>
      </w:abstractNum>
      <w:num w:numId="7"><w:abstractNumId w:val="1"/></w:num>
      <w:num w:numId="8"><w:abstractNumId w:val="1"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="4"/></w:lvlOverride></w:num>
    </w:numbering>`;
    document.body.innerHTML = `<div id="doc">
      <p class="docx-num-7-0">A parent</p>
      <p class="docx-num-7-1">A child</p>
      <p class="docx-num-7-1">A child</p>
      <p class="docx-num-8-0">B parent</p>
      <p class="docx-num-8-1">B child</p>
      <p class="docx-num-7-0">A next parent</p>
      <p class="docx-num-7-1">A reset child</p>
      <p class="docx-num-8-0">B next parent</p>
      <p class="docx-num-8-1">B reset child</p>
    </div>`;
    const root = document.getElementById("doc")!;
    expect(normalizeDocxLists(root, multilevel)).toBe(9);
    expect(
      [...root.querySelectorAll("p")].map(
        (item) => item.dataset.previewListLabel,
      ),
    ).toEqual(["1.", "1.a.", "1.b.", "4.", "4.a.", "2.", "2.a.", "5.", "5.a."]);
  });

  it("rejects malformed numbering and unsupported formats rather than guessing", () => {
    document.body.innerHTML =
      '<div id="doc"><p class="docx-num-4-0">Item</p></div>';
    const root = document.getElementById("doc")!;
    expect(() => normalizeDocxLists(root, "<w:numbering>")).toThrow(
      "invalid numbering",
    );
    expect(() =>
      normalizeDocxLists(
        root,
        numbering.replace("upperRoman", "chineseCounting"),
      ),
    ).toThrow("unsupported numbering format");
    expect(root.querySelector("style")).toBeNull();
    root.querySelector("p")!.className = "docx-num-3-0";
    expect(() =>
      normalizeDocxLists(root, numbering.replace("&#xF0B7;", "&#xF123;")),
    ).toThrow("unsupported bullet");
  });

  it("treats source label text as data rather than CSS", () => {
    document.body.innerHTML =
      '<div id="doc"><p class="docx-num-3-0">Item</p></div>';
    const root = document.getElementById("doc")!;
    const hostile = numbering.replace(
      "&#xF0B7;",
      "url(https://example.invalid/x)",
    );
    expect(normalizeDocxLists(root, hostile)).toBe(1);
    expect(root.querySelector("p")?.dataset.previewListLabel).toBe(
      "url(https://example.invalid/x)",
    );
    expect(root.querySelector("style")?.textContent).not.toContain(
      "example.invalid",
    );
  });
});
