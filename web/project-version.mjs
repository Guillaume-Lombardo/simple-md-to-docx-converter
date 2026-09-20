import { readFileSync } from "node:fs";

export function projectVersion(metadata) {
  const project = metadata.split(/^\[project\]\s*$/m)[1]?.split(/^\[/m)[0];
  const version = project?.match(/^version\s*=\s*"([^"]+)"\s*$/m)?.[1];
  if (
    !version ||
    !/^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.post\d+|\.dev\d+)?$/.test(version)
  )
    throw new Error("Missing or invalid Markweave project version");
  return version;
}

export function readProjectVersion() {
  return projectVersion(
    readFileSync(new URL("../pyproject.toml", import.meta.url), "utf8"),
  );
}
