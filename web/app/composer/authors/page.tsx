"use client";

import { AppShell } from "../../../components/primitives";
import { Protected, useAuth } from "../../../src/auth/context";
import { AuthorDirectory } from "../../../src/composer/author-directory";

export default function AuthorsPage() {
  return (
    <Protected>
      <AuthenticatedAuthors />
    </Protected>
  );
}

function AuthenticatedAuthors() {
  const { controller, state } = useAuth();
  if (state.phase !== "authenticated") return null;
  return (
    <AppShell
      current="Composer"
      onLogout={() => void controller.logout()}
      pending={state.pending}
      user={state.user}
    >
      <AuthorDirectory expire={() => controller.expire()} user={state.user} />
    </AppShell>
  );
}
