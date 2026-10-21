"use client";

import { AppShell } from "../../../components/primitives";
import { Protected, useAuth } from "../../../src/auth/context";
import { LibraryWorkspace } from "../../../src/composer/library";

export default function LibraryPage() {
  return (
    <Protected>
      <AuthenticatedLibrary />
    </Protected>
  );
}

function AuthenticatedLibrary() {
  const { controller, state } = useAuth();
  if (state.phase !== "authenticated") return null;
  return (
    <AppShell
      current="Composer"
      onLogout={() => void controller.logout()}
      pending={state.pending}
      user={state.user}
    >
      <LibraryWorkspace expire={() => controller.expire()} user={state.user} />
    </AppShell>
  );
}
