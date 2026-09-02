"use client";

import { AppShell } from "../../components/primitives";
import { TemplatesWorkspace } from "../../src/admin/templates";
import { Protected, useAuth } from "../../src/auth/context";

export default function TemplatesPage() {
  return (
    <Protected>
      <AuthenticatedTemplates />
    </Protected>
  );
}

function AuthenticatedTemplates() {
  const { controller, state } = useAuth();
  if (state.phase !== "authenticated") return null;
  return (
    <AppShell
      current="Templates"
      onLogout={() => void controller.logout()}
      pending={state.pending}
      user={state.user}
    >
      <TemplatesWorkspace
        expire={() => controller.expire()}
        user={state.user}
      />
    </AppShell>
  );
}
