"""Strict model tool replies and human decisions for Office edit proposals."""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from markweave.composer.office_edit_proposals import (
    OfficeEditContext,
    OfficeEditProposalError,
    compose_office_prompt,
    parse_model_reply,
    parse_proposal,
    reviewed_decision,
)

pytestmark = [pytest.mark.unit]

_REVISION = UUID(int=7)
_SHA = "a" * 64


def _context(limit: int = 20) -> OfficeEditContext:
    return OfficeEditContext(
        "docx",
        _REVISION,
        _SHA,
        (("word/document.xml#t0", "Title"), ("word/document.xml#t3", "Body")),
        limit,
    )


def _reply(*operations: object) -> str:
    return json.dumps({"operations": list(operations)})


def _operation(target: str = "word/document.xml#t0", text: str = "New") -> dict:
    return {"op": "replace_text", "target": target, "text": text}


def test_prompt_discloses_only_selected_text_and_is_digested() -> None:
    transmitted, digest = compose_office_prompt("Shorten it.", _context())

    assert transmitted.startswith("Shorten it.\n\nEditable Office text targets")
    assert '[{"target":"word/document.xml#t0","text":"Title"},' in transmitted
    assert len(digest) == 64
    assert compose_office_prompt("Shorten it.", _context())[1] == digest
    with pytest.raises(OfficeEditProposalError):
        compose_office_prompt("  ", _context())


def test_reply_becomes_a_canonical_proposal_with_transmitted_expectations() -> None:
    proposal = parse_model_reply(
        "```json\n" + _reply(_operation(text="Café")) + "\n```", _context()
    )

    assert json.loads(proposal.canonical()) == {
        "kind": "office_edit",
        "format": "docx",
        "engine_version": 1,
        "base_revision_id": str(_REVISION),
        "base_sha256": _SHA,
        "allowed_targets": ["word/document.xml#t0", "word/document.xml#t3"],
        "operations": [
            {
                "op": "replace_text",
                "target": "word/document.xml#t0",
                "expected_text": "Title",
                "text": "Café",
            }
        ],
    }
    assert "Café" in proposal.canonical()
    assert parse_proposal(proposal.canonical(), 20) == proposal


@pytest.mark.parametrize(
    "reply",
    [
        "not json",
        "[]",
        json.dumps({"operations": []}),
        json.dumps({"operations": "all"}),
        json.dumps({"operations": [_operation()], "note": "x"}),
        _reply(_operation(), _operation(), _operation()),
        _reply("replace"),
        _reply({**_operation(), "op": "delete_text"}),
        _reply({**_operation(), "target": "word/document.xml#t9"}),
        _reply({**_operation(), "target": 0}),
        _reply({**_operation(), "extra": True}),
        _reply(_operation(), _operation()),
        _reply(_operation(text="x" * 21)),
        _reply(_operation(text="tab\tinside")),
        _reply(_operation(text=["list"])),  # ty: ignore[invalid-argument-type]
        "[" * 100_000,
    ],
)
def test_replies_outside_the_tool_contract_are_rejected(reply: str) -> None:
    with pytest.raises(OfficeEditProposalError):
        parse_model_reply(reply, _context())


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "markdown"},
        {"format": "xlsx"},
        {"engine_version": 2},
        {"base_sha256": "short"},
        {"base_revision_id": "not-a-uuid"},
        {"allowed_targets": "all"},
        {"allowed_targets": [1]},
        {"allowed_targets": ["word/document.xml#t0", "word/document.xml#t0"]},
        {"operations": []},
        {"extra": 1},
        {
            "operations": [
                {
                    "op": "replace_text",
                    "target": "word/document.xml#t5",
                    "expected_text": "x",
                    "text": "y",
                }
            ]
        },
        {
            "operations": [
                {
                    "op": "replace_text",
                    "target": "word/document.xml#t0",
                    "text": "y",
                }
            ]
        },
    ],
)
def test_stored_proposals_are_reparsed_without_trust(change: dict) -> None:
    stored = json.loads(parse_model_reply(_reply(_operation()), _context()).canonical())
    stored.update(change)

    with pytest.raises(OfficeEditProposalError):
        parse_proposal(json.dumps(stored), 20)


def test_human_decisions_may_only_change_or_drop_texts() -> None:
    proposed = parse_model_reply(
        _reply(_operation(), _operation("word/document.xml#t3", "Body 2")),
        _context(),
    ).canonical()
    data = json.loads(proposed)

    narrowed = {**data, "operations": [{**data["operations"][0], "text": "Mine"}]}
    decided = reviewed_decision(proposed, json.dumps(narrowed), 20)

    assert [item.text for item in decided.operations] == ["Mine"]
    for widened in (
        {**data, "base_revision_id": str(UUID(int=8))},
        {**data, "base_sha256": "b" * 64},
        {**data, "format": "pptx"},
        {**data, "allowed_targets": ["word/document.xml#t0"]},
        {
            **data,
            "operations": [{**data["operations"][0], "expected_text": "Other"}],
        },
    ):
        with pytest.raises(OfficeEditProposalError):
            reviewed_decision(proposed, json.dumps(widened), 20)

    single = parse_model_reply(_reply(_operation()), _context()).canonical()
    single_data = json.loads(single)
    with pytest.raises(OfficeEditProposalError):
        reviewed_decision(
            single,
            json.dumps(
                {
                    **single_data,
                    "operations": [
                        {
                            "op": "replace_text",
                            "target": "word/document.xml#t3",
                            "expected_text": "Body",
                            "text": "Added",
                        }
                    ],
                }
            ),
            20,
        )


@pytest.mark.parametrize(
    "arguments",
    [
        ("xlsx", _SHA, (("t", "x"),), 5),
        ("docx", "bad", (("t", "x"),), 5),
        ("docx", _SHA, (), 5),
        ("docx", _SHA, (("t", "x"), ("t", "y")), 5),
        ("docx", _SHA, (("t", "x"),), 0),
    ],
)
def test_invalid_disclosure_contexts_are_refused(arguments: tuple) -> None:
    office_format, sha, targets, limit = arguments
    with pytest.raises(OfficeEditProposalError):
        OfficeEditContext(office_format, _REVISION, sha, targets, limit)
