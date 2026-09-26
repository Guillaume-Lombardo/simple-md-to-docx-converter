"use client";

import Link from "next/link";
import { useState } from "react";
import { AppShell } from "../../components/primitives";
import { SessionPolicyWorkspace } from "../../src/admin/session-policy";
import { UsersWorkspace } from "../../src/admin/users";
import { Protected, useAuth } from "../../src/auth/context";

export default function AdminPage() {
  return (
    <Protected>
      <AuthenticatedAdmin />
    </Protected>
  );
}

function AuthenticatedAdmin() {
  const { controller, state } = useAuth();
  const [usersOpen, setUsersOpen] = useState(false);
  const [policyOpen, setPolicyOpen] = useState(false);
  if (state.phase !== "authenticated") return null;
  return (
    <AppShell
      current="Admin"
      onLogout={() => void controller.logout()}
      pending={state.pending}
      user={state.user}
    >
      {state.user.role === "admin" ? (
        <section className="space-y-4">
          <h1 className="text-3xl font-semibold">Administration</h1>
          <p>Choose the system setting you want to manage.</p>
          <div className="grid gap-3">
            <details className="rounded-control border border-muted p-4">
              <summary className="cursor-pointer font-semibold">
                LLM settings
              </summary>
              <p className="mt-3">
                Approve destinations and configure Composer connections.
              </p>
              <Link
                className="font-semibold text-accent underline"
                href="/admin/llm"
              >
                Open LLM settings
              </Link>
            </details>
            <details className="rounded-control border border-muted p-4">
              <summary className="cursor-pointer font-semibold">
                Templates
              </summary>
              <p className="mt-3">
                Manage Pandoc style-reference Word and presentation templates.
              </p>
              <Link
                className="font-semibold text-accent underline"
                href="/templates"
              >
                Open templates
              </Link>
            </details>
            <details className="rounded-control border border-muted p-4">
              <summary className="cursor-pointer font-semibold">
                Typed filling templates
              </summary>
              <p className="mt-3">
                Manage DOCX fields and versions for Composer filling.
              </p>
              <Link
                className="font-semibold text-accent underline"
                href="/composer/fill-templates"
              >
                Open typed filling templates
              </Link>
            </details>
            <details
              className="rounded-control border border-muted p-4"
              onToggle={(event) => {
                setUsersOpen(event.currentTarget.open);
                if (!event.currentTarget.open) setPolicyOpen(false);
              }}
            >
              <summary className="cursor-pointer font-semibold">
                Users and sessions
              </summary>
              {usersOpen && (
                <div className="mt-4 space-y-6">
                  <UsersWorkspace
                    expire={() => controller.expire()}
                    headingLevel="h2"
                    user={state.user}
                  />
                  <details
                    className="rounded-control border border-muted p-4"
                    onToggle={(event) =>
                      setPolicyOpen(event.currentTarget.open)
                    }
                  >
                    <summary className="cursor-pointer font-semibold">
                      Session policy
                    </summary>
                    {policyOpen && (
                      <div className="mt-4">
                        <SessionPolicyWorkspace
                          expire={() => controller.expire()}
                          headingLevel="h3"
                          user={state.user}
                        />
                      </div>
                    )}
                  </details>
                </div>
              )}
            </details>
          </div>
        </section>
      ) : (
        <p>You are not allowed to view administration settings.</p>
      )}
    </AppShell>
  );
}
