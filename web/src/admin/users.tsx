"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { UserResponse } from "../api/generated/types.gen";
import type { EffectiveUser } from "../auth/controller";
import {
  Alert,
  Dialog,
  ItemList,
  LoadingStatus,
  TextField,
} from "../../components/primitives";
import { AdministrationApi } from "./api";
import { useStableVoidCallback } from "./hooks";
import { administrationError, RequestFence } from "./operations";

const defaultAdministrationApi = new AdministrationApi();

export function UsersWorkspace({
  api = defaultAdministrationApi,
  expire,
  user,
}: {
  api?: AdministrationApi;
  expire: () => void;
  user: EffectiveUser;
}) {
  const fence = useRef(new RequestFence());
  const expireSession = useStableVoidCallback(expire);
  const [users, setUsers] = useState<UserResponse[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(user.role === "admin");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [resetTarget, setResetTarget] = useState<UserResponse>();

  const load = useCallback(async () => {
    if (user.role !== "admin") return;
    const request = fence.current.startRead();
    setLoading(true);
    try {
      const accounts = await api.users(request.controller.signal);
      if (!fence.current.current(request.generation)) return;
      setUsers(accounts);
      setError(undefined);
    } catch (reason) {
      if (!fence.current.current(request.generation)) return;
      setError(
        administrationError(
          reason,
          expireSession,
          "Accounts could not be loaded. Try again.",
        ),
      );
    } finally {
      if (fence.current.current(request.generation)) setLoading(false);
    }
  }, [api, expireSession, user.role]);

  useEffect(() => {
    const activeFence = fence.current;
    let disposed = false;
    void Promise.resolve().then(() => {
      if (!disposed) void load();
    });
    return () => {
      disposed = true;
      activeFence.dispose();
    };
  }, [load]);

  const visible = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return users.filter((account) =>
      account.username.toLocaleLowerCase().includes(normalized),
    );
  }, [query, users]);

  async function mutation(
    action: (signal: AbortSignal) => Promise<unknown>,
    success: string,
  ): Promise<boolean> {
    const request = fence.current.startMutation();
    if (!request) return false;
    setPending(true);
    setError(undefined);
    setNotice(undefined);
    try {
      await action(request.controller.signal);
      if (!fence.current.finishMutation(request.generation)) return false;
      setPending(false);
      setResetTarget(undefined);
      setNotice(success);
      await load();
      return true;
    } catch (reason) {
      if (!fence.current.finishMutation(request.generation)) return false;
      setPending(false);
      setError(
        administrationError(
          reason,
          expireSession,
          "The account change could not be completed. Try again.",
        ),
      );
      return false;
    }
  }

  async function create(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const created = await mutation(
      (signal) =>
        api.createUser(
          String(form.get("username") ?? ""),
          String(form.get("password") ?? ""),
          form.get("renewal") === "on",
          signal,
        ),
      "Account created.",
    );
    if (created) formElement.reset();
  }

  if (user.role !== "admin")
    return (
      <section aria-labelledby="users-title">
        <h1 className="text-3xl font-semibold" id="users-title">
          Users
        </h1>
        <Alert tone="danger">Administrator access is required.</Alert>
      </section>
    );

  return (
    <section className="space-y-6" aria-labelledby="users-title">
      <div>
        <h1 className="text-3xl font-semibold" id="users-title">
          Users
        </h1>
        <p>
          Manage local accounts. FastAPI authorizes and audits every change.
        </p>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      {notice && <Alert>{notice}</Alert>}
      <section className="space-y-4" aria-labelledby="create-user-title">
        <h2 className="text-xl font-semibold" id="create-user-title">
          Create an account
        </h2>
        <form className="grid gap-4" onSubmit={(event) => void create(event)}>
          <TextField
            autoComplete="off"
            label="Username"
            name="username"
            required
          />
          <TextField
            autoComplete="new-password"
            label="Temporary password"
            name="password"
            required
            type="password"
          />
          <label>
            <input name="renewal" type="checkbox" /> Require password change at
            next sign-in
          </label>
          <button disabled={pending} type="submit">
            {pending ? "Saving…" : "Create account"}
          </button>
        </form>
      </section>
      <section className="space-y-4" aria-labelledby="account-list-title">
        <h2 className="text-xl font-semibold" id="account-list-title">
          Local accounts
        </h2>
        <TextField
          label="Search by username"
          name="user-search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <LoadingStatus loading={loading}>
          {visible.length === 0 ? (
            <p>No accounts match this search.</p>
          ) : (
            <ItemList label="Local accounts">
              {visible.map((account) => (
                <li className="space-y-2 py-4" key={account.id}>
                  <h3 className="font-semibold">{account.username}</h3>
                  <p>
                    {account.role === "admin" ? "Administrator" : "User"} ·{" "}
                    {account.active ? "Active" : "Inactive"} · Password renewal{" "}
                    {account.password_change_required
                      ? "required"
                      : "not required"}
                  </p>
                  <div className="flex flex-wrap gap-3">
                    {account.role !== "admin" && (
                      <button
                        disabled={pending}
                        onClick={() =>
                          void mutation(
                            (signal) =>
                              api.setActive(
                                account.id,
                                !account.active,
                                signal,
                              ),
                            account.active
                              ? "Account deactivated and sessions revoked."
                              : "Account reactivated.",
                          )
                        }
                        type="button"
                      >
                        {account.active ? "Deactivate" : "Reactivate"}{" "}
                        {account.username}
                      </button>
                    )}
                    <button
                      disabled={pending}
                      onClick={() => setResetTarget(account)}
                      type="button"
                    >
                      Reset password for {account.username}
                    </button>
                    <button
                      disabled={pending}
                      onClick={() =>
                        void mutation(
                          (signal) =>
                            api.setPasswordChangeRequired(
                              account.id,
                              !account.password_change_required,
                              signal,
                            ),
                          account.password_change_required
                            ? "Password renewal cancelled and sessions revoked."
                            : "Password renewal required and sessions revoked.",
                        )
                      }
                      type="button"
                    >
                      {account.password_change_required
                        ? "Cancel password renewal"
                        : "Require password renewal"}{" "}
                      for {account.username}
                    </button>
                  </div>
                </li>
              ))}
            </ItemList>
          )}
        </LoadingStatus>
      </section>
      <Dialog
        onClose={() => setResetTarget(undefined)}
        open={resetTarget !== undefined}
        title={
          resetTarget
            ? `Reset password for ${resetTarget.username}`
            : "Reset password"
        }
      >
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!resetTarget) return;
            const form = new FormData(event.currentTarget);
            void mutation(
              (signal) =>
                api.resetPassword(
                  resetTarget.id,
                  String(form.get("reset-password") ?? ""),
                  form.get("renewal") === "on",
                  signal,
                ),
              "Password reset and sessions revoked.",
            );
          }}
        >
          <TextField
            autoComplete="new-password"
            label="New temporary password"
            name="reset-password"
            required
            type="password"
          />
          <label>
            <input name="renewal" type="checkbox" /> Require password change at
            next sign-in
          </label>
          <div className="flex gap-3">
            <button disabled={pending} type="submit">
              Reset password
            </button>
            <button
              disabled={pending}
              onClick={() => setResetTarget(undefined)}
              type="button"
            >
              Cancel
            </button>
          </div>
        </form>
      </Dialog>
    </section>
  );
}
