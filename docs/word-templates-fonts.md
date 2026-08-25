# Word template and font validation

The service validates untrusted DOCX reference templates before storing or activating a version. The
validator first checks explicit deployment-owned byte, entry, expansion, XML element, depth, attribute,
and font-declaration bounds. It reads ZIP members in bounded chunks without extraction, rejects
encrypted or non-regular members and normalized path collisions, parses every XML relationship
part with entity expansion disabled, and verifies the internal OPC graph.

Templates are rejected when they contain macro-enabled content types, VBA payloads or relationships,
ActiveX, embedded packages, attached templates, `altChunk`, OLE objects, or any relationship with
`TargetMode="External"`. Every Pandoc 3.10.2 reference style is frozen by identifier, Word name, and
type. Expected font families are version metadata separate from the DOCX bytes; every source font
referenced by the candidate must be declared and resolve through the approved immutable policy.

Only after static validation does the activation probe run Pandoc 3.10.2 with a truly empty private
Markdown input. It validates that generated DOCX with the same security, style, and font contract,
then asks LibreOffice 26.2.5.2 to open and rewrite it using an isolated user profile and output
directory. Both engines run without a shell in a disposable workspace with an allowlisted
environment, deadlines, whole-process-group termination, and bounded regular-file outputs.
LibreOffice adds application-default font names to its rewritten package even when those fonts are
not installed or used by the source. The rewritten package is therefore rechecked for structure,
active content, relationships, and required styles; the original candidate remains authoritative
for expected fonts.

## Pinned fonts

The rootless toolchain installs exactly 32 upstream TTF files and exposes no system or LibreOffice
fonts through its isolated Fontconfig configuration:

- Liberation Sans, Serif, and Mono 2.1.5: twelve faces from the official release attachment,
  SHA-256 `7191c669bf38899f73a2094ed00f7b800553364f90e2637010a69c0e268f25d0`;
- Carlito 1.104: four faces from PGP-verified upstream commit
  `3a810cab78ebd6e2e4eed42af9e8453c4f9b850a`;
- Caladea 1.001: four faces from PGP-verified upstream commit
  `336a529cfad3d103d6527752686f8331d13e820a`;
- DejaVu 2.37: twelve faces from the upstream archive, publisher SHA-256
  `fa9ca4d13871dd122f61258a80d01751d603b4d3ee14095d65453b4e846e17d7`.

The installer verifies every archive, raw font, and notice before use. Liberation, Carlito, and
Caladea retain their complete SIL OFL 1.1 notices. DejaVu retains the complete combined Bitstream
Vera, DejaVu, and Arev license. Liberation and DejaVu provide no detached signature; Carlito and
Caladea use GitHub-verified signed commits. Exact sources and per-file checksums live in
`spikes/toolchain/fonts/manifest.json` and `install-fonts.sh`.

Fontconfig resolves Arial to Liberation Sans, Times New Roman to Liberation Serif, Courier New and
Consolas to Liberation Mono, Calibri/Aptos/Aptos Display to Carlito, Cambria to Caladea, and Cambria
Math to DejaVu Serif. Generic sans, serif, and monospace chains begin with Liberation Sans,
Liberation Serif, and Liberation Mono respectively and retain DejaVu as fallback. The approved
corpus currently requires Latin and Greek only, so no Noto family is installed. Unknown families or
new script requirements fail activation until an administrator-approved pinned font image is built
and reviewed.

DrawingML supplementary font declarations are part of the source font contract. Any non-empty
mapping for a script outside the approved Latin and Greek set fails activation. Administrators must
remove dormant Office theme mappings for unsupported scripts when preparing a candidate template;
they must add a reviewed pinned family before retaining a mapping that the template actually needs.

Template publication calls this validator before atomic activation. Final-image E2E covers valid
activation and macro, external-relationship, missing-style, and unsupported-font rejection.
