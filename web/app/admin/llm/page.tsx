"use client";

import Link from "next/link";
import { useState } from "react";
import { AppShell } from "../../../components/primitives";
import { AdminComposerPolicyWorkspace } from "../../../src/admin/composer-policy";
import { Protected, useAuth } from "../../../src/auth/context";
import { ComposerConnectionsWorkspace } from "../../../src/composer/connections";

export default function AdminLlmPage() {
  return (
    <Protected>
      <AuthenticatedLlm />
    </Protected>
  );
}

function AuthenticatedLlm() {
  const { controller, state } = useAuth();
  const [policyEnabled, setPolicyEnabled] = useState(false);
  if (state.phase !== "authenticated") return null;
  return (
    <AppShell
      current="Admin"
      onLogout={() => void controller.logout()}
      pending={state.pending}
      user={state.user}
    >
      {state.user.role === "admin" ? (
        <div className="space-y-8">
          <nav aria-label="Administration breadcrumb">
            <Link className="text-accent underline" href="/admin">
              Administration
            </Link>
            {" / LLM settings"}
          </nav>
          <AdminComposerPolicyWorkspace
            expire={() => controller.expire()}
            onPolicyEnabled={setPolicyEnabled}
          />
          {policyEnabled ? (
            <ComposerConnectionsWorkspace
              expire={() => controller.expire()}
              user={state.user}
            />
          ) : (
            <p>
              Enable an approved destination to configure and test a connection.
            </p>
          )}
        </div>
      ) : (
        <p>You are not allowed to view administration settings.</p>
      )}
    </AppShell>
  );
}
