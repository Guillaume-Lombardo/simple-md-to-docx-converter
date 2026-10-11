"""Representative records for databases migrated to an earlier recovery-set revision.

The single implementation lives in the final-image recovery setup script so the
development tests and the rootless-image smoke share the same fixture.
"""

from tests.e2e.recovery_cli_setup import (
    PRE_FILL_PLAN_REVISION,
    OlderSchemaRecords,
    populate_older_schema,
)

__all__ = ["PRE_FILL_PLAN_REVISION", "OlderSchemaRecords", "populate_older_schema"]
