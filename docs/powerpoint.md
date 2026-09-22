# PowerPoint presentations

Open **2pptx**, upload Markdown or a ZIP containing Markdown and local images, and
choose **PowerPoint (PPTX)** or **PowerPoint and original source (ZIP)**. No template is required:
Markweave uses the reference presentation bundled with its pinned Pandoc version.
Word preferences and the system Word fallback do not override this choice.

For ordinary Markdown, choose the heading level that starts slides (default: 2).
Horizontal rules (`---`, separated from surrounding text by blank lines) explicitly
separate slides. The outline preview shows proposed titles and warnings before submission.
Text is not summarized or truncated. Check the result in PowerPoint for overflow.

Marp files are detected from `marp: true` front matter, or select Marp explicitly.
Slide separators, text, lists, tables, local images and presenter-note comments are
supported. CSS, themes, backgrounds and other Marp styling directives are not rendered;
preview warnings identify ignored styling. The PowerPoint reference controls appearance.
Raw HTML and remote images are rejected. Mermaid uses the existing local renderer.
Pandoc `notes`, `columns` and `column` fenced blocks are supported.

```markdown
---
marp: true
---
# Opening

Editable presentation text.

<!-- Presenter notes for the opening slide. -->

---

# Next steps

- Review the proposal
- Agree on delivery
```

Download the starter reference from **2pptx**, edit its master/layout styles in
PowerPoint, then create a **PowerPoint (PPTX)** template under **templates**.
Keep Pandoc's named layouts and use approved fonts. Select the active template
in **2pptx**; each job records its immutable version. Word and PowerPoint templates
are distinct types, and a replacement must preserve the template's type.

The portable ZIP contains `presentation.pptx`, `generation.json`, a README and the
original upload under `source/`. An original ZIP remains intact with its assets.
This recovers the original source; changes subsequently made in PowerPoint are not
written back into that source. To extract an edited PPTX, use the opt-in
[slide-oriented Markdown or Marp workflow](user-guide.md#extract-an-edited-powerpoint-presentation)
in experimental **2md**. It requires the external broker and does not promise full visual fidelity.

The HTTP conversion endpoint accepts `output=pptx` or `pptx-bundle`,
`presentation_dialect=auto|markdown|marp` and `slide_level=1..6`.
Authenticated preview and starter-reference endpoints are
`POST /api/v1/presentation-plan` and `GET /api/v1/presentation-reference`.
The CLI supports `convert --output pptx --presentation-dialect marp` and template
creation with `templates create --kind pptx`. See command help for required arguments.


## API compatibility in 0.7.0

Conversion responses and history can return `pptx` and `pptx-bundle` in `output`. Clients that
exhaustively validate the former `docx|pdf|both` set must regenerate their bindings or accept these
two values before upgrading from a pre-0.7.0 release. Existing document requests remain supported.
The approval and narrowly scoped OpenAPI-gate exception are retained in the
[compatibility record](evidence/release-migration-history.md#powerpoint-070-api-exception).
