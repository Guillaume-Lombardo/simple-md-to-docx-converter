"use client";

import { AppShell } from "../../../components/primitives";
import { Protected, useAuth } from "../../../src/auth/context";
import { FillTemplatesWorkspace } from "../../../src/composer/fill-templates";

export default function FillTemplatesPage() {
  return (
    <Protected>
      <AuthenticatedFillTemplates />
    </Protected>
  );
}

function AuthenticatedFillTemplates() {
  const { controller, state } = useAuth();
  if (state.phase !== "authenticated") return null;
  return (
    <AppShell
      current="Composer"
      onLogout={() => void controller.logout()}
      pending={state.pending}
      user={state.user}
    >
      <FillTemplatesWorkspace
        expire={() => controller.expire()}
        user={state.user}
      />
    </AppShell>
  );
}
