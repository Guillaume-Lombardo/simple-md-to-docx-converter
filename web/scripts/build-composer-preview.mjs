import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { build } from "esbuild";

const webRoot = resolve(import.meta.dirname, "..");
const output = resolve(webRoot, "public/composer-preview");
await mkdir(output, { recursive: true });

const notices = [
  ["docx-preview", "LICENSE"],
  ["jszip", "LICENSE.markdown"],
  ["pdfjs-dist", "LICENSE"],
  ["@aiden0z/pptx-renderer", "LICENSE"],
  ["@aiden0z/pptx-renderer", "licenses/mtx-decompressor-MPL-2.0.txt"],
  ["@aiden0z/pptx-renderer", "licenses/ECMA-text-copyright-notice.txt"],
];
const noticeOutput = resolve(output, "licenses");
await mkdir(noticeOutput, { recursive: true });
const noticeLines = [
  "Third-party notices for the self-hosted Composer document preview",
  "The matching full license and component notices are in this directory.",
  "",
];
for (const [name, licensePath] of notices) {
  const packageRoot = resolve(webRoot, "node_modules", name);
  const metadata = JSON.parse(
    await readFile(resolve(packageRoot, "package.json"), "utf8"),
  );
  const fileName = `${name.replaceAll(/[\\/@]/g, "-")}-${licensePath.replaceAll("/", "-")}`;
  await copyFile(
    resolve(packageRoot, licensePath),
    resolve(noticeOutput, fileName),
  );
  noticeLines.push(
    `${name}@${metadata.version} (${metadata.license}): licenses/${fileName}`,
  );
}
await writeFile(
  resolve(output, "THIRD_PARTY_NOTICES.txt"),
  `${noticeLines.join("\n")}\n`,
);

await Promise.all([
  copyFile(
    resolve(webRoot, "node_modules/jszip/dist/jszip.min.js"),
    resolve(output, "jszip.js"),
  ),
  copyFile(
    resolve(webRoot, "node_modules/docx-preview/dist/docx-preview.min.js"),
    resolve(output, "docx.js"),
  ),
  build({
    entryPoints: [
      resolve(
        webRoot,
        "node_modules/@aiden0z/pptx-renderer/dist/aiden0z-pptx-renderer.browser.es.js",
      ),
    ],
    bundle: true,
    format: "iife",
    globalName: "ComposerPptx",
    minify: true,
    outfile: resolve(output, "pptx.js"),
    platform: "browser",
  }),
  build({
    entryPoints: [resolve(webRoot, "src/composer/preview/frame-client.ts")],
    bundle: true,
    format: "iife",
    minify: true,
    outfile: resolve(output, "frame-client.js"),
    platform: "browser",
  }),
]);
