"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useState,
  useSyncExternalStore,
} from "react";
import { AuthController, type AuthState } from "./controller";

const AuthContext = createContext<AuthController | null>(null);

export function AuthProvider({
  children,
  controller,
}: {
  children: ReactNode;
  controller?: AuthController;
}) {
  const [active] = useState(() => controller ?? new AuthController());
  useEffect(() => {
    void active.load();
    return () => active.dispose();
  }, [active]);
  return <AuthContext value={active}>{children}</AuthContext>;
}

export function useAuth(): { controller: AuthController; state: AuthState } {
  const controller = useContext(AuthContext);
  if (!controller) throw new Error("AuthProvider is required");
  return {
    controller,
    state: useSyncExternalStore(
      controller.subscribe,
      controller.snapshot,
      controller.snapshot,
    ),
  };
}

export function Protected({ children }: { children: ReactNode }) {
  const { controller, state } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (state.phase === "anonymous") router.replace("/login");
    if (state.phase === "restricted") router.replace("/change-password");
  }, [router, state.phase]);
  if (state.phase === "loading")
    return <p aria-live="polite">Loading your session…</p>;
  if (state.phase === "unavailable")
    return (
      <section aria-labelledby="unavailable-title">
        <h1 id="unavailable-title">Markweave is unavailable</h1>
        <p role="alert">{controller.unavailableMessage()}</p>
        <button type="button" onClick={() => void controller.load()}>
          Try again
        </button>
      </section>
    );
  if (state.phase !== "authenticated")
    return <p aria-live="polite">Opening sign in…</p>;
  return children;
}
