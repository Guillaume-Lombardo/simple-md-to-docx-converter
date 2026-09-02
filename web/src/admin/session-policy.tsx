"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import type { IdleSessionPolicyResponse } from "../api/generated/types.gen";
import { ApiError } from "../api/transport";
import type { EffectiveUser } from "../auth/controller";
import { Alert, LoadingStatus, TextField } from "../../components/primitives";
import { AdministrationApi } from "./api";
import { useStableVoidCallback } from "./hooks";
import {
  administrationError,
  effectiveIdleMaximum,
  idleMinutesError,
  RequestFence,
} from "./operations";

const defaultAdministrationApi = new AdministrationApi();

export function SessionPolicyWorkspace({
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
  const [policy, setPolicy] = useState<IdleSessionPolicyResponse>();
  const [etag, setEtag] = useState<string>();
  const [userMinutes, setUserMinutes] = useState("");
  const [adminMinutes, setAdminMinutes] = useState("");
  const [loading, setLoading] = useState(user.role === "admin");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();

  const accept = useCallback(
    (result: { data: IdleSessionPolicyResponse; etag?: string }): boolean => {
      if (!result.etag) {
        setPolicy(undefined);
        setEtag(undefined);
        setError(
          "The server did not provide the policy revision. Reload and try again.",
        );
        return false;
      }
      setPolicy(result.data);
      setEtag(result.etag);
      setUserMinutes(String(result.data.user_idle_minutes));
      setAdminMinutes(String(result.data.admin_idle_minutes));
      setError(undefined);
      return true;
    },
    [],
  );

  const load = useCallback(async (): Promise<boolean> => {
    if (user.role !== "admin") return false;
    const request = fence.current.startRead();
    setLoading(true);
    try {
      const result = await api.sessionPolicy(request.controller.signal);
      if (!fence.current.current(request.generation)) return false;
      return accept(result);
    } catch (reason) {
      if (!fence.current.current(request.generation)) return false;
      setPolicy(undefined);
      setEtag(undefined);
      setNotice(undefined);
      setError(
        administrationError(
          reason,
          expireSession,
          "The session policy could not be loaded. Try again.",
        ),
      );
      return false;
    } finally {
      if (fence.current.current(request.generation)) setLoading(false);
    }
  }, [accept, api, expireSession, user.role]);

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

  async function save(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!policy || !etag) return;
    const userError = idleMinutesError(
      "User inactivity duration",
      userMinutes,
      policy.user_idle_minutes_bounds,
      policy.idle_minutes_granularity,
      policy.absolute_lifetime_seconds,
    );
    const adminError = idleMinutesError(
      "Administrator inactivity duration",
      adminMinutes,
      policy.admin_idle_minutes_bounds,
      policy.idle_minutes_granularity,
      policy.absolute_lifetime_seconds,
    );
    if (userError || adminError) {
      setNotice(undefined);
      setError(userError ?? adminError);
      return;
    }
    const request = fence.current.startMutation();
    if (!request) return;
    setPending(true);
    setError(undefined);
    setNotice(undefined);
    try {
      await api.updateSessionPolicy(
        etag,
        Number(userMinutes),
        Number(adminMinutes),
        request.controller.signal,
      );
      if (!fence.current.current(request.generation)) return;
      const refreshed = await api.sessionPolicy(request.controller.signal);
      if (!fence.current.finishMutation(request.generation)) return;
      setPending(false);
      if (accept(refreshed)) setNotice("Session policy updated.");
    } catch (reason) {
      if (!fence.current.finishMutation(request.generation)) return;
      setPending(false);
      if (reason instanceof ApiError && reason.status === 412) {
        if (await load()) {
          setNotice(undefined);
          setError(
            "The policy changed on the server. Review the latest values and try again.",
          );
        }
        return;
      }
      setNotice(undefined);
      setError(
        administrationError(
          reason,
          expireSession,
          "The session policy could not be updated. Try again.",
        ),
      );
    }
  }

  if (user.role !== "admin")
    return (
      <section aria-labelledby="session-policy-title">
        <h1 className="text-3xl font-semibold" id="session-policy-title">
          Session policy
        </h1>
        <Alert tone="danger">Administrator access is required.</Alert>
      </section>
    );

  return (
    <section className="space-y-6" aria-labelledby="session-policy-title">
      <div>
        <h1 className="text-3xl font-semibold" id="session-policy-title">
          Session policy
        </h1>
        <p>Set the system-wide inactivity duration for each role.</p>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      {notice && <Alert>{notice}</Alert>}
      <Alert>
        Tightening this policy can require currently signed-in users, including
        you, to sign in again immediately.
      </Alert>
      <LoadingStatus loading={loading}>
        {policy ? (
          <form className="space-y-6" onSubmit={(event) => void save(event)}>
            <dl className="grid gap-2">
              <div>
                <dt className="font-medium">Current revision</dt>
                <dd>{policy.revision}</dd>
              </div>
              <div>
                <dt className="font-medium">Absolute session lifetime</dt>
                <dd>{policy.absolute_lifetime_seconds} seconds</dd>
              </div>
              <div>
                <dt className="font-medium">Allowed increment</dt>
                <dd>{policy.idle_minutes_granularity} minute(s)</dd>
              </div>
            </dl>
            <fieldset className="space-y-4">
              <legend className="text-xl font-semibold">Role durations</legend>
              <div>
                <TextField
                  inputMode="numeric"
                  label="User inactivity duration (minutes)"
                  name="user-idle-minutes"
                  onChange={(event) => setUserMinutes(event.target.value)}
                  required
                  value={userMinutes}
                />
                <p className="text-sm text-muted">
                  Default {policy.user_idle_minutes_bounds.default_minutes};
                  approved range{" "}
                  {policy.user_idle_minutes_bounds.minimum_minutes}–
                  {policy.user_idle_minutes_bounds.maximum_minutes} minutes. The
                  absolute lifetime currently limits this role to at most{" "}
                  {effectiveIdleMaximum(
                    policy.user_idle_minutes_bounds,
                    policy.absolute_lifetime_seconds,
                  )}{" "}
                  minutes.
                </p>
              </div>
              <div>
                <TextField
                  inputMode="numeric"
                  label="Administrator inactivity duration (minutes)"
                  name="admin-idle-minutes"
                  onChange={(event) => setAdminMinutes(event.target.value)}
                  required
                  value={adminMinutes}
                />
                <p className="text-sm text-muted">
                  Default {policy.admin_idle_minutes_bounds.default_minutes};
                  approved range{" "}
                  {policy.admin_idle_minutes_bounds.minimum_minutes}–
                  {policy.admin_idle_minutes_bounds.maximum_minutes} minutes.
                  The absolute lifetime currently limits this role to at most{" "}
                  {effectiveIdleMaximum(
                    policy.admin_idle_minutes_bounds,
                    policy.absolute_lifetime_seconds,
                  )}{" "}
                  minutes.
                </p>
              </div>
            </fieldset>
            <button disabled={pending} type="submit">
              {pending ? "Saving…" : "Save session policy"}
            </button>
          </form>
        ) : (
          <button onClick={() => void load()} type="button">
            Reload policy
          </button>
        )}
      </LoadingStatus>
    </section>
  );
}
