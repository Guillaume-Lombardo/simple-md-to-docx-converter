"use client";

import { AppShell } from "../../components/primitives";
import { UsersWorkspace } from "../../src/admin/users";
import { Protected, useAuth } from "../../src/auth/context";

export default function UsersPage() {
  return (
    <Protected>
      <AuthenticatedUsers />
    </Protected>
  );
}

function AuthenticatedUsers() {
  const { controller, state } = useAuth();
  if (state.phase !== "authenticated") return null;
  return (
    <AppShell
      current="Users"
      onLogout={() => void controller.logout()}
      pending={state.pending}
      user={state.user}
    >
      <UsersWorkspace expire={() => controller.expire()} user={state.user} />
    </AppShell>
  );
}
