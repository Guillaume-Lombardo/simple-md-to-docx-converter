---
ticket: T99
linear_id: G1L-618
linear_url: https://linear.app/g1lom/issue/G1L-618/t99-consolidate-compose-variants-with-profiles
status: Done
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T99 - Consolidate Compose variants with profiles

## Objective

Reduce the ten root Compose files to a smaller set that uses Compose profiles and documented overrides, without changing any deployed behavior.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to change, item 6, and what to remove, compose files).

## Acceptance criteria

- Inventory every Compose file, its consumers (quickstart scripts, E2E harness, CI, docs) and its security posture (trusted upstream, Podman, Next.js, Composer, test).
- Replace mutually exclusive variants with profiles or a minimal set of overrides; keep the loopback-only, ClamAV-by-default and trusted-upstream restrictions intact.
- Update `scripts/quickstart*.sh`, the E2E harness, CI and every guide.
- Prove equivalence by comparing `docker compose config` / `podman compose config` output for every supported mode before and after.
- Both final-image E2E profiles and the quickstart tests pass.

## Dependencies

- None.

## Blocking points

- Must not land while a published release and the public Compose pin differ (AGENTS.md release rule); schedule outside a release-adoption window. Owner preference (2026-10-09): land before the T93 0.8.0 release starts; PyPI and the public Compose pin were both 0.7.3 on that date.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.
- 2026-10-11: Implementation on `chore/T99-compose-profiles`. Inventory: `compose.yaml`
  (secure base; both helpers), `compose.simple.yaml` (unbounded `/work`; simple helper),
  `compose.simple-composer.yaml` (opt-in Composer; simple helper), `compose.podman.yaml`
  (rootless Podman), `compose.trusted-upstream.yaml` (trusted upstream and insecure; ClamAV in the
  inactive `local-antivirus` profile), `compose.podman-trusted-upstream.yaml` (CNI-free
  `slirp4netns`), `compose.nextjs.yaml`, `compose.nextjs-podman.yaml` and
  `compose.nextjs-podman-trusted-upstream.yaml` (Next.js browser tier, applied in every supported
  helper mode), and `compose.test.yaml` (local PostgreSQL/S3 test services; unrelated to the
  quickstarts and unchanged). Consumers: both quickstart helpers, the four Compose E2E runners, CI
  domain selection, release public alignment, the quickstart and Compose tests, and
  `docs/quickstart.md`. Result: the always-applied browser tier moved into `compose.yaml` and the
  two Podman browser overlays merged into `compose.podman.yaml` and
  `compose.podman-trusted-upstream.yaml`; ten root Compose files became seven. Compose profiles
  gate only whole services, so the attribute-level runtime and scanner variants remain ordered,
  documented overrides; ClamAV's existing `local-antivirus` profile is the only service-presence
  variant. Release alignment now reads both published defaults from `compose.yaml`. Equivalence:
  `docker compose config --format json` and `podman compose config --format json` rendered
  13 supported modes (secure; simple Docker/Podman x ClamAV/trusted-upstream/insecure x
  Composer off/on), each with published and candidate images and two ports (104 renders before
  and after); all normalized renders are byte-identical. New tests derive each helper's file stack
  from its real `compose()` function and check every rendered mode's loopback-only, scanner and
  image posture. CI selection now also covers the two trusted-upstream overrides. Limitations:
  local `podman compose` delegates to the same Docker Compose v5.5.1 provider; hosted CI runs the
  real Compose E2E and final-image E2E profiles.
- 2026-10-11: Completed and verified on `main`. PR #288 was squash-merged as
  `a087eb5a862e5acfed7fd787e01fd5b5e3d7e8f7` from head `8c77884`, a `main`-update merge of
  reviewed head `ac7989d`. Independent review round 1 approved it with no actionable findings and
  independently reproduced the 104 byte-identical before/after renders. PR CI run 38150654069 and
  push CI run 38153540913 on `main` at `a087eb5` passed all 19 jobs, including `compose`,
  `container`, both final-image E2E profiles and `CI / gate`. Linear G1L-618 is Done.
  Per-criterion evidence:
  - Inventory: the 2026-10-11 progress entry above lists every Compose file, its consumers and its
    security posture.
  - Profiles and overrides: ten root Compose files became seven; the loopback-only,
    ClamAV-by-default and trusted-upstream restrictions are unchanged, as the new posture tests
    and the review confirm.
  - Consumers: both quickstart helpers, the Compose E2E runners, CI domain selection, release
    public alignment and `docs/quickstart.md` were updated in PR #288.
  - Equivalence: `docker compose config` and `podman compose config` renders for 13 supported
    modes are byte-identical before and after.
  - Final-image E2E and quickstart tests: both profiles and the `compose` job passed in main CI
    run 38153540913.
  Operator note: hand-built `-f` lists must drop the removed `compose.nextjs*.yaml` files; the
  0.8.0 upgrade guide records this. Limitation unchanged: local `podman compose` delegates to the
  Docker Compose provider.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-618.
