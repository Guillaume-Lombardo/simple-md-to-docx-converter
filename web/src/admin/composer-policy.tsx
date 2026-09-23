"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Alert, LoadingStatus, TextField } from "../../components/primitives";
import { administrationError, RequestFence } from "./operations";
import { useStableVoidCallback } from "./hooks";
import {
  AdminComposerPolicyApi,
  type ComposerPolicy,
  type DestinationResolution,
} from "./composer-policy-api";

const defaultApi = new AdminComposerPolicyApi();

export function AdminComposerPolicyWorkspace({
  api = defaultApi,
  expire,
  onPolicyEnabled,
}: {
  api?: AdminComposerPolicyApi;
  expire: () => void;
  onPolicyEnabled?: (enabled: boolean) => void;
}) {
  const fence = useRef(new RequestFence());
  const expireSession = useStableVoidCallback(expire);
  const [policy, setPolicy] = useState<ComposerPolicy>();
  const [endpoint, setEndpoint] = useState("");
  const endpointRef = useRef("");
  const [preview, setPreview] = useState<DestinationResolution>();
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();

  const loadPolicy = useCallback(async () => {
    const activeFence = fence.current;
    const request = activeFence.startRead();
    setLoading(true);
    setError(undefined);
    setPolicy(undefined);
    try {
      const loaded = await api.get(request.controller.signal);
      if (activeFence.current(request.generation)) {
        setPolicy(loaded);
        onPolicyEnabled?.(loaded.enabled);
      }
    } catch (reason) {
      if (activeFence.current(request.generation))
        setError(
          administrationError(
            reason,
            expireSession,
            "The LLM policy could not be loaded. Retry or ask the operator to check the service.",
          ),
        );
    } finally {
      if (activeFence.current(request.generation)) setLoading(false);
    }
  }, [api, expireSession, onPolicyEnabled]);

  useEffect(() => {
    const activeFence = fence.current;
    let disposed = false;
    void Promise.resolve().then(() => {
      if (!disposed) void loadPolicy();
    });
    return () => {
      disposed = true;
      activeFence.dispose();
    };
  }, [loadPolicy]);

  async function resolve(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!policy || pending) return;
    const request = fence.current.startRead();
    setPending(true);
    setPreview(undefined);
    setError(undefined);
    setNotice(undefined);
    try {
      const requestedEndpoint = endpoint.trim();
      const result = await api.resolve(
        requestedEndpoint,
        request.controller.signal,
      );
      if (
        fence.current.current(request.generation) &&
        endpointRef.current.trim() === requestedEndpoint
      )
        setPreview(result);
    } catch (reason) {
      if (fence.current.current(request.generation))
        setError(
          administrationError(
            reason,
            expireSession,
            "The endpoint could not be resolved. Try again.",
          ),
        );
    } finally {
      if (fence.current.current(request.generation)) setPending(false);
    }
  }

  async function save(enabled: boolean, approval?: DestinationResolution) {
    if (!policy || pending || (approval && !policy.editable_destinations))
      return;
    const request = fence.current.startMutation();
    if (!request) return;
    setPending(true);
    setError(undefined);
    setNotice(undefined);
    try {
      const updated = await api.update(
        policy,
        {
          enabled,
          allowed_destinations: approval
            ? [
                ...new Set([
                  ...policy.allowed_destinations,
                  approval.destination,
                ]),
              ]
            : policy.allowed_destinations,
          allowed_networks: approval
            ? [...new Set([...policy.allowed_networks, ...approval.addresses])]
            : policy.allowed_networks,
        },
        request.controller.signal,
      );
      if (fence.current.current(request.generation)) {
        setPolicy(updated);
        onPolicyEnabled?.(updated.enabled);
        setPreview(undefined);
        setNotice(
          approval
            ? "Destination and resolved addresses approved. Composer egress is enabled."
            : enabled
              ? "Composer egress enabled."
              : "Composer egress disabled.",
        );
      }
    } catch (reason) {
      if (fence.current.current(request.generation)) {
        setPreview(undefined);
        setError(
          administrationError(
            reason,
            expireSession,
            "The LLM policy could not be saved. Reload and try again.",
          ),
        );
        void api
          .get(request.controller.signal)
          .then((latest) => {
            if (fence.current.current(request.generation)) {
              setPolicy(latest);
              onPolicyEnabled?.(latest.enabled);
            }
          })
          .catch(() => undefined);
      }
    } finally {
      if (fence.current.finishMutation(request.generation)) setPending(false);
    }
  }

  return (
    <section aria-labelledby="llm-policy-title" className="space-y-4">
      <div>
        <h1 id="llm-policy-title">LLM settings</h1>
        <p>
          First approve where Composer may connect. Then add credentials, select
          a model, and test the connection below.
        </p>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      {notice && <Alert>{notice}</Alert>}
      {loading && <LoadingStatus loading>Loading LLM policy…</LoadingStatus>}
      {!loading && !policy && (
        <button onClick={() => void loadPolicy()} type="button">
          Retry loading policy
        </button>
      )}
      {policy && (
        <div className="space-y-4 rounded-control border border-muted p-4">
          <h2>1. Destination policy</h2>
          <p>
            Policy mode: <strong>{policy.mode}</strong>. Composer egress:{" "}
            <strong>{policy.enabled ? "enabled" : "disabled"}</strong>.
          </p>
          <p>
            Approved destinations:{" "}
            {policy.allowed_destinations.length
              ? policy.allowed_destinations.join(", ")
              : "None"}
          </p>
          <p>
            Approved address ranges:{" "}
            {policy.allowed_networks.length
              ? policy.allowed_networks.join(", ")
              : "None"}
          </p>
          {policy.editable_destinations ? (
            <>
              <form
                className="grid gap-3"
                onSubmit={(event) => void resolve(event)}
              >
                <TextField
                  label="OpenAI-compatible endpoint URL"
                  name="endpoint"
                  type="url"
                  required
                  value={endpoint}
                  onChange={(event) => {
                    setEndpoint(event.target.value);
                    endpointRef.current = event.target.value;
                    setPreview(undefined);
                  }}
                />
                <button disabled={pending} type="submit">
                  Preview destination and DNS addresses
                </button>
              </form>
              {preview && (
                <div className="space-y-2 rounded-control border border-accent p-4">
                  <h3>Review exact destination</h3>
                  <p>
                    Destination: <strong>{preview.destination}</strong>
                  </p>
                  <p>
                    Resolved addresses:{" "}
                    <strong>{preview.addresses.join(", ") || "None"}</strong>
                  </p>
                  <p>
                    Approval adds this destination and these addresses to the
                    current policy and enables Composer egress.
                  </p>
                  <button
                    disabled={pending || preview.addresses.length === 0}
                    onClick={() => void save(true, preview)}
                    type="button"
                  >
                    Approve destination and addresses
                  </button>
                </div>
              )}
            </>
          ) : (
            <p>
              Destination policy is managed by the operator outside this page.
              Ask the operator to approve an endpoint before configuring a
              connection.
            </p>
          )}
          {policy.enabled ? (
            <button
              disabled={pending}
              onClick={() => void save(false)}
              type="button"
            >
              Disable Composer egress
            </button>
          ) : policy.allowed_destinations.length > 0 ? (
            <button
              disabled={pending}
              onClick={() => void save(true)}
              type="button"
            >
              Enable approved destinations
            </button>
          ) : null}
        </div>
      )}
      {policy?.enabled && (
        <p>
          2. Configure a connection below. Its endpoint must match an approved
          destination. Credentials are write-only.
        </p>
      )}
    </section>
  );
}
