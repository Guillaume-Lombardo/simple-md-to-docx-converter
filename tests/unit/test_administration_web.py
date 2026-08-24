"""Unit coverage for safe owner and administrator HTML shells."""

from uuid import uuid4

import pytest

from md_converter.auth.models import Role, User
from md_converter.templates.models import TemplateIdentity, TemplateStatus
from md_converter.web import render_templates_page


@pytest.mark.unit
def test_template_page_escapes_identity_and_distinguishes_regular_users() -> None:
    actor = User(uuid4(), '<script id="actor">', "actor", "hash", Role.USER)
    template = TemplateIdentity(
        uuid4(), actor.id, "Preferred", "Description", TemplateStatus.ACTIVE
    )
    page = render_templates_page(
        actor,
        template,
        "Preferred template",
        maximum_upload_bytes=321,
    )

    assert "<script id=" not in page
    assert "&lt;script" in page
    assert 'data-user-role="user"' in page
    assert f'data-preferred-template-id="{template.id}"' in page
    assert 'data-max-template-bytes="321"' in page
    assert 'id="users"' not in page
    assert "Create a template" in page


@pytest.mark.unit
def test_template_page_exposes_admin_users_tab_without_false_preference() -> None:
    actor = User(uuid4(), "Admin", "admin", "hash", Role.ADMIN)
    fallback = TemplateIdentity(
        uuid4(), actor.id, "Fallback", "Description", TemplateStatus.ACTIVE
    )

    page = render_templates_page(
        actor,
        fallback,
        "System fallback template",
        maximum_upload_bytes=100,
    )

    assert 'data-user-role="admin"' in page
    assert 'data-preferred-template-id=""' in page
    assert 'id="users"' in page
    assert "Create an account" in page
