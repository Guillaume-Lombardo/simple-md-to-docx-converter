"use client";

import { AppShell } from "../../../components/primitives";
import { Protected, useAuth } from "../../../src/auth/context";
import { ComposerConnectionsWorkspace } from "../../../src/composer/connections";

export default function ComposerConnectionsPage() {
  return (
    <Protected>
      <AuthenticatedConnections />
    </Protected>
  );
}

function AuthenticatedConnections() {
  const { controller, state } = useAuth();
  if (state.phase !== "authenticated") return null;
  return (
    <AppShell
      current="Composer"
      onLogout={() => void controller.logout()}
      pending={state.pending}
      user={state.user}
    >
      <ComposerConnectionsWorkspace
        expire={() => controller.expire()}
        user={state.user}
      />
    </AppShell>
  );
}
