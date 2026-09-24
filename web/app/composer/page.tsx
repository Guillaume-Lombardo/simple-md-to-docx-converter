"use client";

import { Protected } from "../../src/auth/context";
import { ComposerWorkspace } from "../../src/composer/workspace";

export default function ComposerPage() {
  return (
    <Protected>
      <ComposerWorkspace />
    </Protected>
  );
}
