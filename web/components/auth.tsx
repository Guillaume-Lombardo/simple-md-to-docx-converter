"use client";

import { type FormEvent, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { Alert, AppShell, TextField } from "./primitives";
import { useAuth } from "../src/auth/context";

export function LoginPage() {
  const { controller, state } = useAuth();
  const router = useRouter();
  const username = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (state.phase === "authenticated") router.replace("/convert");
    if (state.phase === "restricted") router.replace("/change-password");
    if (state.phase === "anonymous") username.current?.focus();
  }, [router, state.phase]);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    void controller.login(
      String(data.get("username") ?? ""),
      String(data.get("password") ?? ""),
    );
  };
  if (state.phase === "loading")
    return (
      <main className="auth-card">
        <p aria-live="polite">Checking your session…</p>
      </main>
    );
  if (state.phase === "unavailable")
    return (
      <main className="auth-card">
        <h1>Sign in</h1>
        <Alert tone="danger">{controller.unavailableMessage()}</Alert>
        <button type="button" onClick={() => void controller.load()}>
          Try again
        </button>
      </main>
    );
  if (state.phase !== "anonymous")
    return (
      <main className="auth-card">
        <p aria-live="polite">Opening Markweave…</p>
      </main>
    );
  return (
    <main className="auth-card">
      <h1 className="text-3xl font-semibold">Sign in</h1>
      {state.notice && <Alert tone="danger">{state.notice}</Alert>}
      <form className="space-y-5" onSubmit={submit}>
        <TextField
          autoComplete="username"
          label="Username"
          name="username"
          ref={username}
          required
        />
        <TextField
          autoComplete="current-password"
          label="Password"
          name="password"
          required
          type="password"
        />
        <button className="primary-button" type="submit">
          Sign in
        </button>
      </form>
    </main>
  );
}

export function PasswordRenewalPage() {
  const { controller, state } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (state.phase === "authenticated") router.replace("/convert");
    if (state.phase === "anonymous") router.replace("/login");
  }, [router, state.phase]);
  if (state.phase !== "restricted")
    return (
      <main className="auth-card">
        <p aria-live="polite">Checking your session…</p>
      </main>
    );
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    void controller.renew(
      String(data.get("password") ?? ""),
      String(data.get("confirmation") ?? ""),
    );
  };
  return (
    <AppShell
      current="Password"
      user={state.user}
      onLogout={() => void controller.logout()}
      pending={state.pending}
    >
      <h1 className="text-3xl font-semibold">Change your password</h1>
      <p>
        Your current password was accepted. Choose a new password before
        continuing.
      </p>
      {state.error && <Alert tone="danger">{state.error}</Alert>}
      <form className="max-w-xl space-y-5" onSubmit={submit}>
        <TextField
          autoComplete="new-password"
          label="New password"
          name="password"
          required
          type="password"
        />
        <TextField
          autoComplete="new-password"
          label="Confirm new password"
          name="confirmation"
          required
          type="password"
        />
        <button
          className="primary-button"
          disabled={state.pending}
          type="submit"
        >
          {state.pending ? "Changing password…" : "Change password"}
        </button>
      </form>
    </AppShell>
  );
}
