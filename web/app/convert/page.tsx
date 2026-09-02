"use client";

import {
  Alert,
  AppShell,
  LoadingStatus,
  Progress,
  TextField,
} from "../../components/primitives";
import { Protected, useAuth } from "../../src/auth/context";

export default function FoundationPage() {
  return (
    <Protected>
      <ProtectedConvert />
    </Protected>
  );
}

function ProtectedConvert() {
  const { controller, state } = useAuth();
  if (state.phase !== "authenticated") return null;
  return (
    <AppShell
      current="Convert"
      user={state.user}
      pending={state.pending}
      onLogout={() => void controller.logout()}
    >
      <h1 className="text-3xl font-semibold">Convert</h1>
      <Alert>
        This preview foundation does not replace the production conversion page
        yet.
      </Alert>
      <form className="max-w-xl space-y-5" aria-label="Foundation form">
        <TextField label="Document label" name="document-label" />
        <Progress label="Foundation progress" value={0} />
        <LoadingStatus loading={false}>
          Ready for workflow migration.
        </LoadingStatus>
      </form>
    </AppShell>
  );
}
