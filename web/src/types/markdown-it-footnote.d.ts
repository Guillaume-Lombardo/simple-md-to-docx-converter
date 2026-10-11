declare module "markdown-it-footnote" {
  import type { MarkdownIt } from "markdown-it";

  const footnote: (parser: MarkdownIt) => void;
  export default footnote;
}
