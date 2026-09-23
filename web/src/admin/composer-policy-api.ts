import * as v from "valibot";
import { ApiTransport } from "../api/transport";

const policySchema = v.object({
  mode: v.picklist(["delegated", "operator"]),
  enabled: v.boolean(),
  allowed_destinations: v.array(v.string()),
  allowed_networks: v.array(v.string()),
  editable_destinations: v.boolean(),
  etag: v.string(),
});

const resolutionSchema = v.object({
  destination: v.string(),
  addresses: v.array(v.string()),
});

export type ComposerPolicy = v.InferOutput<typeof policySchema>;
export type DestinationResolution = v.InferOutput<typeof resolutionSchema>;

export class AdminComposerPolicyApi {
  constructor(private readonly transport = new ApiTransport()) {}

  get(signal?: AbortSignal): Promise<ComposerPolicy> {
    return this.transport.json("/api/v1/admin/composer-policy", policySchema, {
      signal,
    });
  }

  update(
    policy: ComposerPolicy,
    input: Pick<
      ComposerPolicy,
      "enabled" | "allowed_destinations" | "allowed_networks"
    >,
    signal?: AbortSignal,
  ): Promise<ComposerPolicy> {
    return this.transport.json("/api/v1/admin/composer-policy", policySchema, {
      body: JSON.stringify(input),
      csrf: true,
      etag: policy.etag,
      method: "PUT",
      signal,
    });
  }

  resolve(
    endpoint: string,
    signal?: AbortSignal,
  ): Promise<DestinationResolution> {
    return this.transport.json(
      "/api/v1/admin/composer-policy/resolve",
      resolutionSchema,
      {
        body: JSON.stringify({ endpoint }),
        csrf: true,
        method: "POST",
        signal,
      },
    );
  }
}
