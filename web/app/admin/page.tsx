"use client";

import Link from "next/link";
import { AppShell } from "../../components/primitives";
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
          <h1>Administration</h1>
          <p>Choose the system setting you want to manage.</p>
          <ul className="grid gap-4 sm:grid-cols-2">
            <li className="rounded-control border border-muted p-4">
              <Link
                className="font-semibold text-accent underline"
                href="/admin/llm"
              >
                LLM settings
              </Link>
              <p>Approve destinations and configure Composer connections.</p>
            </li>
            <li className="rounded-control border border-muted p-4">
              <Link
                className="font-semibold text-accent underline"
                href="/templates"
              >
                Templates
              </Link>
              <p>Manage shared Word and presentation templates.</p>
            </li>
            <li className="rounded-control border border-muted p-4">
              <Link
                className="font-semibold text-accent underline"
                href="/users"
              >
                Users and sessions
              </Link>
              <p>Manage accounts and session policy.</p>
            </li>
          </ul>
        </section>
      ) : (
        <p>You are not allowed to view administration settings.</p>
      )}
    </AppShell>
  );
}
