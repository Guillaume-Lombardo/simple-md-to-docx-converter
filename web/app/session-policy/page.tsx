"use client";

import { AppShell } from "../../components/primitives";
import { SessionPolicyWorkspace } from "../../src/admin/session-policy";
import { Protected, useAuth } from "../../src/auth/context";

export default function SessionPolicyPage() {
  return (
    <Protected>
      <AuthenticatedSessionPolicy />
    </Protected>
  );
}

function AuthenticatedSessionPolicy() {
  const { controller, state } = useAuth();
  if (state.phase !== "authenticated") return null;
  return (
    <AppShell
      current="Session policy"
      onLogout={() => void controller.logout()}
      pending={state.pending}
      user={state.user}
    >
      <SessionPolicyWorkspace
        expire={() => controller.expire()}
        user={state.user}
      />
    </AppShell>
  );
}
