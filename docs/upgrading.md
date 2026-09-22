<a id="upgrading"></a>

# Upgrading

This guide defines the supported operator path between released Markweave
versions. It covers a package installation and the matching container image;
it does not choose production limits, retention values, or a deployment
topology.

## Supported transition

<a id="supported-transition"></a>

Upgrade only from one released final version to a later released final version.
Keep all application components that access one database at the same Markweave
version. For a container deployment, use the image bearing that exact release
version; for a package deployment, install the wheel with that same version.
Do not combine a new wheel with an older image, or an older wheel with a newer
image, against one live database.

Read the target release entry in [the changelog](../CHANGELOG.md#changelog)
and the target release's published evidence before scheduling the change. Test
the exact source, image, and configuration in an isolated environment that
matches the selected storage profile before production use.

Select a complete published release with verified receipts for every required image. Version `0.7.2`
has no qualified published container set; see the [incident record](evidence/release-migration-history.md#incomplete-072-container-release).
The repository quickstarts pin the matched published `0.7.3` backend/frontend images. Reverse-enabled
deployments also require the matching reverse-attempt image and native broker.

## Upgrade procedure

<a id="upgrade-procedure"></a>

1. Select the target released version and confirm its package and container
   provenance. Record the current version, image digest, configuration source,
   and database revision.
2. Verify that every required `MARKWEAVE_*` setting is present for the selected
   profile. Do not substitute unresolved production-limit placeholders with
   values invented during an upgrade.
3. Create and verify a profile-consistent backup before changing binaries or
   schema. For standalone, preserve the SQLite database and the complete object
   directory together. For distributed, preserve the PostgreSQL recovery point
   and matching object-store versions from one coordinated window.
4. Stop or drain every API and worker process that can write the selected
   profile. Never run a mixed-version fleet during a schema transition.
5. Deploy the target package or exact container image, then start one controlled
   Markweave application component for the selected profile. Alembic upgrades
   older metadata when that component is assembled; wait for its migration and
   readiness result before starting any other component at the target version.
   This guide does not depend on a separate migration command.
6. Require `/health/ready` from the controlled component before admitting
   traffic. Start the remaining same-version components only after readiness,
   then verify representative stable object identifiers and one authorized
   workflow before ending the maintenance window.

## Schema changes and rollback

<a id="schema-and-rollback"></a>

Migrations move the database forward. A backup made before the migration is the
rollback boundary; do not assume an automatic schema downgrade exists. If the
target version fails after its migration starts, keep traffic stopped, restore
the database and its matching object data into isolated targets, validate the
restore, then return all components to the previously released version.

Do not restore a database without its corresponding object state, and do not
point a restored database at a newer or unrelated object-store version. A
rollback is complete only after readiness and representative stable-object
checks succeed on the restored previous version.

### Reverse-conversion migration 19

Migration 19 persists reverse-extraction options. When upgrading a database from an earlier revision,
create and verify the profile-consistent database and object backup required above, record the
running broker configuration and native-broker package/virtual environment, and drain reverse work.
Keep the application, frontend, reverse-attempt images, and native-broker executable at the same
target release version and bind the three images to one verified schema-2 `release-images.json`.

The reverse broker remains a host-native, externally isolated service; do not replace it with a
container. Ordinary quickstarts do not configure the external broker. Configure its immutable
attempt-image digest only from the verified release manifest, then require broker `READY` and one
authenticated reverse smoke workflow before reopening reverse admission. Follow the current published
quickstart and release receipts when selecting public image pins.

If migration 19 or the matched reverse set fails, stop admission, restore the pre-upgrade database
and object backup into isolated targets, restore the prior application/frontend/reverse image set,
native-broker executable/virtual environment, and broker configuration together, then verify
readiness before returning traffic. Do not mix a new broker policy or attempt image with a restored
older application release.

### Next.js cutover releases

Current releases use separate backend and frontend images. Upgrade and roll back the matched image
set and routing manifest together; verify frontend routes, FastAPI readiness, login and an authorized
workflow before restoring traffic. Restore the matching database and object backup if persistent
state changed. The one-time legacy-renderer migration and its old rollback procedure are retained
in [cutover history](evidence/release-migration-history.md#nextjs-cutover-and-legacy-rollback).

## Configuration compatibility

### During 0.x

<a id="configuration-compatibility"></a>

`MARKWEAVE_*` is the canonical configuration namespace. Legacy
`MD_CONVERTER_*` aliases remain supported throughout 0.x. Migrate each legacy
setting to its corresponding canonical name before 1.0, verify the effective
configuration, and remove the legacy definition once the canonical definition
is confirmed.

You may temporarily set both names for one setting during 0.x only when they
validate to the same effective value. Conflicting dual definitions fail closed.
The aliases are removed in 1.0, so an upgrade to 1.0 requires a configuration
with no `MD_CONVERTER_*` entries. This policy does not alter which values are
required or select values for unresolved production limits.

## Stable links

- [Upgrade guide top](#upgrading)
- [Supported transition](#supported-transition)
- [Upgrade procedure](#upgrade-procedure)
- [Schema changes and rollback](#schema-changes-and-rollback)
- [Configuration compatibility during 0.x](#configuration-compatibility)

## Related guidance

Read the [changelog](../CHANGELOG.md) for the exact source release, the
[configuration reference](configuration.md) for accepted settings, and the
[recovery guide](recovery.md) before changing persistent state. The
[container deployment guide](container-deployment.md) and [release process](releasing.md) define
immutable image and publication evidence. The [security policy](../SECURITY.md) and
[support policy](../SUPPORT.md) define the supported release boundary.
