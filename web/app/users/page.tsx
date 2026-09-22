"use client";

import { AppShell } from "../../components/primitives";
import { SessionPolicyWorkspace } from "../../src/admin/session-policy";
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
      {state.user.role === "admin" && (
        <details className="rounded-control border border-muted p-4">
          <summary className="cursor-pointer font-semibold">
            Session policy
          </summary>
          <div className="mt-4">
            <SessionPolicyWorkspace
              expire={() => controller.expire()}
              user={state.user}
            />
          </div>
        </details>
      )}
    </AppShell>
  );
}
