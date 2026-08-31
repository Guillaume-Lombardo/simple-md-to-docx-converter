# T41 HTTP contract fixtures

These pretty-printed fixtures make the application-decomposition regression test
reviewable. `openapi.json` is the normalized dictionary returned by
`FastAPI.openapi()`. `routes.json` records every registered route in application
order, including schema visibility, declared status code, and response class.
FastAPI default placeholders are resolved to their effective response class.

They are test snapshots for T41, not the durable, versioned OpenAPI artifact or
compatibility policy owned by T45. The running application remains authoritative. T45 keeps its
artifact at `openapi/v1.json` and tests normalized equality between the two representations without
making either fixture serve both purposes.
