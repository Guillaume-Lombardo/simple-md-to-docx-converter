"""Real PostgreSQL authentication repository contract."""

import os

import pytest
from sqlalchemy import delete

from md_converter.app import create_app
from md_converter.config import Settings
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.schema import SessionRow, UserRow
from md_converter.persistence.sql import (
    DatabaseReadinessProbe,
    SqlSessionRepository,
    SqlUserRepository,
    create_database_engine,
)
from tests.storage_contracts import exercise_auth_repository_contract


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_authentication_repository_contract() -> None:
    database_url = os.environ["MD_CONVERTER_TEST_POSTGRES_URL"]
    engine = create_database_engine(database_url)
    upgrade_database(engine)
    with engine.begin() as connection:
        connection.execute(delete(SessionRow))
        connection.execute(delete(UserRow))
    exercise_auth_repository_contract(
        SqlUserRepository(engine), SqlSessionRepository(engine)
    )
    assert DatabaseReadinessProbe(engine).is_ready()
    engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
@pytest.mark.requires_s3
def test_distributed_profile_wires_postgresql_and_s3_readiness() -> None:
    database_url = os.environ["MD_CONVERTER_TEST_POSTGRES_URL"]
    engine = create_database_engine(database_url)
    upgrade_database(engine)
    with engine.begin() as connection:
        connection.execute(delete(SessionRow))
        connection.execute(delete(UserRow))
    engine.dispose()

    settings = Settings(
        initial_admin_username="admin",
        initial_admin_password="admin-" + "password",
        argon2_memory_cost=8,
        argon2_time_cost=1,
        storage_profile="distributed",
        distributed_database_url=database_url,
        s3_bucket=os.environ["MD_CONVERTER_TEST_S3_BUCKET"],
        s3_endpoint_url=os.environ["MD_CONVERTER_TEST_S3_ENDPOINT_URL"],
        s3_region=os.environ["MD_CONVERTER_TEST_S3_REGION"],
        s3_access_key_id=os.environ["MD_CONVERTER_TEST_S3_ACCESS_KEY_ID"],
        s3_secret_access_key=os.environ["MD_CONVERTER_TEST_S3_SECRET_ACCESS_KEY"],
    )
    app = create_app(settings)
    assert app.state.components.readiness.is_ready()
    assert (
        app.state.components.authentication.login(
            "admin", "admin-" + "password"
        ).user.role.value
        == "admin"
    )
