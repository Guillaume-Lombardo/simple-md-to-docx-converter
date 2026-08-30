# OpenAPI contract maintenance

The running FastAPI application is the source of truth for the Markweave HTTP API. The committed
[`openapi/v1.json`](../openapi/v1.json) file is its deterministic, reviewable v1 artifact; it is not
input to the application and no generated client code is part of the server implementation.

Generate and validate it from the repository root:

```bash
uv run python -m scripts.openapi_contract generate
uv run python -m scripts.openapi_contract check
```

Generation assembles the same routers as the runtime with deterministic, infrastructure-free
configuration. The placeholder credential used during assembly is not a runtime secret and the
generator fails if it appears in the artifact. JSON object keys, UTF-8 encoding, indentation, and
the final newline are canonical. Tests also fetch runtime `/openapi.json`, normalize it with the
same function, and require byte equality with the artifact for both storage profiles.

The document declares the existing opaque session cookie as the global security requirement.
Login, liveness, readiness, and metrics explicitly opt out with an empty operation-level security
requirement; every other documented operation inherits authenticated-session security. The cookie
name in the durable artifact is the canonical configuration default, while a runtime configured
with another supported cookie name truthfully exposes that effective name in its own schema.

## Compatibility review

CI regenerates the contract and fails if the working tree artifact is stale. For pull requests and
merge groups it compares `openapi/v1.json` with the target revision. The comparison reports
compatible additions and rejects incompatible route, method, schema, response-status, response-
header, security-requirement, parameter, request-body, and required-field changes. Schema
comparison is directional: restricting accepted request values or broadening emitted response
values is incompatible, while broadening accepted inputs or narrowing emitted outputs is
compatible.

Review the generated diff together with the route implementation. In particular, confirm optional
template selection, restricted authentication sessions, pagination, `ETag`/`If-Match`, stable
errors, download media types, health endpoints, and administrator-only operations. CLI contract
tests read this artifact to verify the endpoints they call; they do not maintain a second route
list.

An intentional breaking change must not bypass or weaken the comparison. Plan a new API major
version and artifact path in its own approved ticket, preserve the v1 artifact and routes for the
supported compatibility period, update clients and documentation, and then update the CI policy in
the same independently reviewed change. A same-major incompatible change remains a CI failure.

The readable snapshots under `tests/fixtures/t41_http_contract/` remain T41 decomposition
regression evidence. They deliberately stay separate from the durable artifact, and a test requires
their normalized OpenAPI content to agree so that neither can drift silently.
