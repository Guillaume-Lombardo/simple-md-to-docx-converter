---
ticket: T130
linear_id: G1L-683
linear_url: https://linear.app/g1lom/issue/G1L-683/t130-fix-intermittent-final-image-e2e-failures-and-harden-ci-network
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T130 - Fix intermittent final-image E2E failures and harden CI network fetches

## Objective

Remove the intermittent `e2e-distributed` failure in the scanner-outage scenario, where probes
after the ClamAV container is stopped time out on the `frontend` network alias and backend
readiness while the router login and the frontend's numeric address answer.

Observed on 2026-10-11 in hosted CI run 38132219791 (main at `7e4f81a`, job
"CI / e2e-distributed": `scanner-outage route frontend_alias status=error:TimeoutError`,
`backend_ready status=error:TimeoutError`, after "StopSignal SIGTERM failed to stop container
md-converter-t21-distributed-clamav in 5 seconds, resorting to SIGKILL"), in the T106 PR #287 run
(passed on rerun), and earlier during T89 qualification (2026-09 progress notes) with the same
symptom.

## Acceptance criteria

- Identify the root cause with evidence (for example container DNS/alias resolution stalling
  after the scanner is killed, a harness probe bound, or a real application readiness regression
  during scanner outage) and record it in the ticket.
- If the cause is in the product (readiness or routing during scanner outage), fix the product;
  if it is in the harness or runtime networking, fix the harness without weakening what the
  scenario proves (scanner outage must still fail closed for uploads while readiness, routing and
  owner reads behave as specified).
- No retry loop or timeout increase may mask a real readiness failure; any bounded retry must be
  justified and documented.
- The distributed and standalone final-image E2E profiles pass repeatedly (state how many
  consecutive hosted or local runs were used as evidence).

## Extended scope (owner decision 2026-10-12, CI speed and reliability lot 1)

The owner approved a CI speed and reliability plan built from a measured investigation
(2026-10-12). Lot 1 is assigned to this ticket:

- **Scanner-outage flake.** The probable root cause is that Podman's Netavark/aardvark-dns
  resolution stalls right after the ClamAV container stops. The two failing routes both depend on
  DNS aliases: `frontend_alias`, and distributed `backend_ready`, which reaches `postgres` and
  `rustfs` by alias. The routes that pass avoid DNS. Fixes:
  - pin static addresses for `postgres`, `rustfs` and the frontend for the app, worker and router
    containers, as is already done for ClamAV and the LLM;
  - turn the one-shot probe into a bounded, documented poll that still requires every route to
    answer;
  - make `scripts/container/fake-clamav.py` honour SIGTERM;
  - record DNS timings in failure artifacts.
- **`browser-next-reversion` "PPTX preview" timeout.** This is a deterministic race in the test.
  Once the preview iframe is ready, the label becomes "PPTX revision <id> preview", so waiting for
  the literal "PPTX preview" can never succeed. Fix the wait in
  `tests/e2e/browser-next-reversion.test.mjs`.
- **Network hardening, without architecture change:**
  - pre-pull every base image (including the frontend Node.js images) with exponential backoff,
    then build without implicit pulls;
  - fix the curl retry settings in the Containerfiles (`--max-time` greater than
    `--retry-max-time` disables retries), and add `--retry-all-errors`;
  - add retries and timeouts to `toolchain/document-engines/fonts/install-fonts.sh`;
  - pre-pull the PostgreSQL and RustFS images with retry in the E2E harness;
  - pre-fetch the Grype database with retries and then scan without auto-update, keeping the same
    freshness;
  - replace the no-op `apt-get install podman` with a version assertion.
- **Quick CI wins:**
  - raise the `heavy` matrix `max-parallel` from 2 to 8;
  - stop selecting both final-image E2E domains for `scripts/e2e/run-compose*.sh`-only changes;
  - use one shared uv cache suffix for heavy domains.

All CI gate semantics, the final rootless image E2E requirement, digest and checksum pinning, and
vulnerability-gate freshness must stay unchanged. The repository's CI validator and its tests are
updated to match.

Out of scope: building images once and sharding E2E (T135), the release workflow restructure
(T134), ClamAV `stop_grace_period` or `init` in `compose.yaml`, a Grype database cache for pull
requests, RPM lockfiles or a hermetic toolchain, changing which domains `web/**` selects, and
skipping Python tests for documentation-only changes.

## Dependencies

- None.

## Blocking points

- None known.

## Progress

- 2026-10-12: Created in Linear on 2026-10-11, extended by the owner on 2026-10-12, mirrored here
  and started on branch `fix/T130-e2e-flakes-ci-network` from `origin/main` `745367f`.
- 2026-10-12: Root cause of the scanner-outage flake. Eight hosted `e2e-distributed` failures
  (runs 38062715491, 38130110398, 38132219791, 38134826813, 38143330262, 38177917148 attempts 1
  and 2, and 38223418603 attempt 1) show the same signature right after the fake ClamAV stop:
  `router_login=200` and `frontend_numeric=200` (no name resolution), while `frontend_alias`
  (`http://frontend:3000`) and `backend_ready` time out. Distributed readiness probes PostgreSQL
  and S3 through the `postgres:5432` and `http://rustfs:9000` aliases; standalone readiness uses no
  peer name and never failed this way. The fake scanner ignored SIGTERM as PID 1, so every stop
  waited 5 seconds and was killed, and tearing down its network endpoint reloads the network's
  aardvark-dns resolver. Passing runs show all four routes answering. The cause is therefore
  runtime name resolution in the harness network, not product readiness or routing.
- 2026-10-12: Harness fix. `scripts/e2e/run.sh` reserves validated static addresses for
  PostgreSQL, RustFS and the frontend at the top of the subnet (dynamic allocation starts at the
  bottom) next to the existing scanner address, starts those peers with `--ip`, verifies they keep
  the address after every start and restart, and gives the application, workers and the
  insecure-mode application `--add-host` entries for them, as the scanner and LLM already had. The
  router shares the application's network namespace. A local check on Podman 5.4 measured
  `getent ahosts` for pinned names at 1 to 3 ms. The scanner-outage probe is now a documented
  bounded poll: attempts 250 ms apart that start within 20 seconds, each request bounded to
  2 seconds, still requiring `router_login`, `frontend_numeric` and `backend_ready` to answer 200.
  `frontend_alias` stays diagnostic. A persistent failure still fails the scenario, prints every
  failed attempt, and records name-resolution timings (`getent ahosts` for each pinned name and
  its `.dns.podman` resolver form). Failure artifacts also record those timings for the running
  application and workers. `scripts/container/fake-clamav.py` now shuts down on SIGTERM.
- 2026-10-12: The `browser-next-reversion` "PPTX preview" timeouts (runs 38142247276 and
  38223418603 attempt 2) are a deterministic race. The label is "PPTX preview" only until the
  captured revision becomes active, then "PPTX revision <id> preview". The test now waits for the
  active label and requires the native preview frame to carry that exact revision identifier.
- 2026-10-12: Network hardening. `scripts/container/pull-base-images.sh` acquires every
  digest-pinned `ARG *_IMAGE` base of a Containerfile through `scripts/ci/pull-immutable-image.sh`,
  which now makes four attempts with 5, 15 and 45 second backoff for transient transport errors
  and reuses a local image only when its digest matches. `build.sh`, `build-reverse-attempt.sh`
  and the new `build-frontend.sh` acquire their bases and then build with `--pull=never`, so the
  frontend Node.js bases are no longer pulled implicitly (run 38217247318 failed on such a pull
  with `unexpected EOF`). Containerfile downloads keep `--max-time 180` but now have a 600-second
  retry budget and `--retry-all-errors` (run 38134149393 lost a Chrome RPM download because the
  120-second budget was below the transfer bound); checksums are still verified. Font downloads
  gain bounded retries and timeouts. The distributed E2E harness acquires the digest-pinned
  PostgreSQL and RustFS images with retries and runs them with `--pull=never`.
  `scripts/container/supply-chain.sh` fetches the current Grype database into a private
  per-invocation directory with bounded retries and scans with automatic updates disabled; the
  database is never reused across invocations and the gate is unchanged. CI asserts the
  preinstalled Podman (4.9.3 on the hosted image, where the former `apt-get install` was a no-op)
  instead of reinstalling it.
- 2026-10-12: CI quick wins. The `heavy` matrix runs up to eight domains in parallel, heavy
  domains share the `py314-heavy` uv cache suffix with writes still limited to trusted pushes to
  `main`, and `scripts/e2e/run-compose*.sh` changes select only the `compose` domain. The CI
  validator locks the new matrix, cache suffix, Podman assertion and canonical digest.
- 2026-10-12: Local verification on Podman 5.4.2 (4 CPUs, 13 GB). The frontend build acquired
  both Node.js bases through the puller and built with `--pull=never` in 74 seconds.
  `scripts/container/supply-chain.sh` on that image fetched the current Grype database (schema
  v6.1.10, built 2026-10-12T06:45:22Z, status valid) into its private directory, scanned with
  automatic updates disabled and passed the unchanged gate. One complete local
  `scripts/e2e/run.sh distributed` run from source-built images passed in 14 minutes 21 seconds:
  39 browser tests passed and none failed. The run included both `browser-next-reversion`
  structured phases and the scanner-outage probe, which reported every route at 200 on the
  first attempt. The standalone profile was not run locally. The repeated-pass criterion still
  requires hosted runs of both profiles at the reviewed head.
- 2026-10-12: Independent review round 1 required changes. Hosted run 38255823107 at `7e534a3`
  then failed in three heavy jobs.
  - **Scanner-outage poll tests.** The poll is now tested by running the extracted JavaScript
    under Node through the real shell wrapper, with scripted fetch outcomes and a virtual clock.
    - On each required route, a persistent non-200 response, rejected fetch or request timeout
      exits nonzero within the window and records name resolution.
    - A transient stall recovers, and an alias-only failure stays diagnostic.
    - Mutation checks: disabling the failure exit code fails 9 tests, widening the window fails
      10, returning success from the shell wrapper fails 10, and making the alias required fails
      4.
  - **`CI / container` font download.** The job lost the Liberation font archive
    (`github.com/liberationfonts/liberation-fonts/files/7261482/...`) to six HTTP 500 responses
    within 31 seconds (13:28:40 to 13:29:11 UTC). The default curl backoff is 1, 2, 4, 8 and
    16 seconds.
    - The URL is the official upstream link: the 2.1.5 release has no assets and links to this
      attachment. It answered 200 from here minutes later, so the failure was a transient
      upstream outage.
    - Font and Containerfile downloads now make eight retries with curl's doubling backoff,
      about four minutes, bounded by a ten-minute retry budget. Containerfile downloads drop the
      fixed two-second retry delay that had collapsed their retry window.
    - Checksums are unchanged. Moving fonts to a host cache or bind mount is a hermetic-toolchain
      change the owner has not approved, so it is left as a recommendation.
  - **Both final-image E2E jobs.** `e2e-standalone` and `e2e-distributed` passed the
    scanner-outage probe on its first attempt, then failed in the frontend admission phase:
    "Frontend hold request 0 returned HTTP 502 before saturation", after about 5 seconds.
    - Cause: that phase replaces the frontend with the admission fixture, which reused the
      pinned frontend address. On Podman 4.9 the application's network namespace, which the
      router shares, still held the previous container's neighbor entry, so the new container
      at the same address was unreachable until that entry expired. Standalone, where only the
      frontend was pinned, failed the same way.
    - Fix: the frontend is no longer pinned. It keeps a dynamic address as on `main`, and the
      router keeps using its current numeric origin. No required route resolves the frontend by
      name, so `frontend_alias` remains a diagnostic resolver route.
    - PostgreSQL and RustFS stay pinned: they keep the same container across restarts, and the
      distributed job passed their outage and restart phases.
