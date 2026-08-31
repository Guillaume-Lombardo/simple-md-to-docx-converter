"""Real HTTP coverage for the storage-profile-agnostic template CLI contract."""

from __future__ import annotations

import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import ClassVar

import pytest

from markweave.cli.main import main
from markweave.cli.profiles import ProfileStore
from markweave.cli.types import ConnectionProfile

pytestmark = pytest.mark.integration

TEMPLATE_ID = "11111111-1111-4111-8111-111111111111"
VERSION_ID = "22222222-2222-4222-8222-222222222222"
OWNER_ID = "33333333-3333-4333-8333-333333333333"
DOCX = b"real-http-docx"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FONTS = ("Calibri", "Cambria", "Courier New")


class _TemplateHandler(BaseHTTPRequestHandler):
    """Stateful HTTP double that enforces remote auth, CSRF, and ETags."""

    revision: ClassVar[int] = 1
    status: ClassVar[str] = "active"
    guarded: ClassVar[bool] = True
    requests: ClassVar[list[tuple[str, str, str, str | None]]] = []
    multipart: ClassVar[bytes] = b""

    @classmethod
    def reset(cls) -> None:
        cls.revision = 1
        cls.status = "active"
        cls.guarded = True
        cls.requests.clear()
        cls.multipart = b""

    def do_GET(self) -> None:
        self._record()
        if self.path.startswith("/api/v1/templates?"):
            self._json(
                200,
                {"items": [self._identity()], "total": 1, "offset": 0, "limit": 20},
            )
            return
        if self.path == f"/api/v1/templates/{TEMPLATE_ID}":
            if self.status == "archived" and self._actor() == "bob":
                self._error(404, "TEMPLATE_NOT_FOUND", "Template not found.")
                return
            self._json(200, self._identity(), etag=self._etag())
            return
        if self.path == f"/api/v1/templates/{TEMPLATE_ID}/versions":
            self._json(200, [self._version(2), self._version(1)])
            return
        if self.path in {
            f"/api/v1/templates/{TEMPLATE_ID}/content",
            f"/api/v1/templates/{TEMPLATE_ID}/versions/{VERSION_ID}/content",
        }:
            digest = hashlib.sha256(DOCX).hexdigest()
            self.send_response(200)
            self.send_header("Content-Type", DOCX_TYPE)
            self.send_header("Content-Length", str(len(DOCX)))
            self.send_header("ETag", f'"sha256-{digest}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(DOCX)
            return
        self._error(404, "NOT_FOUND", "Not found.")

    def do_POST(self) -> None:
        body = self._body()
        self._record()
        if not self._mutation_headers_valid():
            return
        if self.path == "/api/v1/templates":
            type(self).multipart = body
            self._json(201, self._identity(), etag=self._etag())
            return
        if self.path.endswith(f"/versions/{VERSION_ID}/restore"):
            if not self._conditional():
                return
            type(self).revision += 1
            restored = self._version(3)
            restored["restored_from_version_id"] = VERSION_ID
            self._json(201, restored, etag=self._etag())
            return
        if self.path.endswith("/archive"):
            if not self._conditional():
                return
            type(self).revision += 1
            type(self).status = "archived"
            self._json(200, self._identity(), etag=self._etag())
            return
        self._error(404, "NOT_FOUND", "Not found.")

    def do_PATCH(self) -> None:
        self._body()
        self._record()
        if not self._mutation_headers_valid() or not self._owner_or_admin():
            return
        if not self._conditional():
            return
        type(self).revision += 1
        self._json(200, self._identity(), etag=self._etag())

    def do_PUT(self) -> None:
        self._body()
        self._record()
        if not self._mutation_headers_valid():
            return
        if self.path.endswith("/content"):
            if not self._owner_or_admin() or not self._conditional():
                return
            type(self).revision += 1
            self._json(201, self._version(2), etag=self._etag())
            return
        if self.path.endswith("/system-fallback") and self._actor() != "admin":
            self._error(403, "FORBIDDEN", "Administrator access is required.")
            return
        if self.path.endswith(("/preferred", "/system-fallback")):
            self.send_response(204)
            self.end_headers()
            return
        self._error(404, "NOT_FOUND", "Not found.")

    def do_DELETE(self) -> None:
        self._record()
        if not self._mutation_headers_valid():
            return
        if self.path == "/api/v1/template-preference":
            self.send_response(204)
            self.end_headers()
            return
        if not self._owner_or_admin() or not self._conditional():
            return
        if self.guarded:
            self._error(409, "TEMPLATE_IN_USE", "Template is still referenced.")
            return
        self.send_response(204)
        self.end_headers()

    def _record(self) -> None:
        self.requests.append(
            (
                self.command,
                self.path,
                self._actor(),
                self.headers.get("If-Match"),
            )
        )

    def _actor(self) -> str:
        return self.headers.get("Cookie", "").removeprefix("session=")

    def _mutation_headers_valid(self) -> bool:
        if self._actor() not in {"admin", "alice", "bob"}:
            self._error(401, "SESSION_INVALID", "Sign in again.")
            return False
        if self.headers.get("X-CSRF-Token") != f"csrf-{self._actor()}":
            self._error(403, "CSRF_INVALID", "Invalid CSRF token.")
            return False
        return True

    def _owner_or_admin(self) -> bool:
        if self._actor() in {"admin", "alice"}:
            return True
        self._error(403, "FORBIDDEN", "Template mutation is not permitted.")
        return False

    def _conditional(self) -> bool:
        if self.headers.get("If-Match") == self._etag():
            return True
        self._error(412, "TEMPLATE_CONFLICT", "Template changed.")
        return False

    def _etag(self) -> str:
        return f'"template-{TEMPLATE_ID}-{self.revision}"'

    def _identity(self) -> dict[str, object]:
        return {
            "id": TEMPLATE_ID,
            "owner_id": OWNER_ID,
            "owner_username": "alice",
            "name": "Finance",
            "description": "Quarterly",
            "status": self.status,
            "revision": self.revision,
            "current_version_id": VERSION_ID,
        }

    @staticmethod
    def _version(number: int) -> dict[str, object]:
        return {
            "id": VERSION_ID,
            "template_id": TEMPLATE_ID,
            "number": number,
            "sha256": hashlib.sha256(DOCX).hexdigest(),
            "size": len(DOCX),
            "created_at": "2026-08-30T00:00:00Z",
            "created_by": OWNER_ID,
            "restored_from_version_id": None,
            "declared_fonts": list(FONTS),
            "resolved_fonts": [],
            "validation_trace": ["package_structure"],
        }

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", "0")))

    def _json(self, status: int, payload: object, *, etag: str | None = None) -> None:
        content = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        if etag is not None:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(content)

    def _error(self, status: int, code: str, message: str) -> None:
        self._json(status, {"error": {"code": code, "message": message}})

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@pytest.fixture
def template_server():
    _TemplateHandler.reset()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TemplateHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _save_profile(name: str, actor: str, service_url: str) -> None:
    ProfileStore().save(
        ConnectionProfile(
            name=name,
            service_url=service_url,
            session_state=f"session={actor}",
            csrf_state=f"csrf-{actor}",
        )
    )


@pytest.mark.parametrize("storage_profile", ("standalone", "distributed"))
def test_complete_template_cli_contract_over_real_http_for_each_storage_profile(
    storage_profile: str,
    template_server: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI preserves the same HTTP contract across both deployed profiles."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for actor in ("admin", "alice", "bob"):
        _save_profile(f"{storage_profile}-{actor}", actor, template_server)
    upload = tmp_path / 'hostile-"name.docx'
    upload.write_bytes(DOCX)
    common_fonts = tuple(item for font in FONTS for item in ("--font", font))
    alice = ("--profile", f"{storage_profile}-alice")
    admin = ("--profile", f"{storage_profile}-admin")
    bob = ("--profile", f"{storage_profile}-bob")

    assert main(("templates", "list", *bob)) == 0
    assert main(("templates", "show", TEMPLATE_ID, *alice)) == 0
    assert (
        main(
            (
                "templates",
                "create",
                "--name",
                "Finance",
                "--file",
                str(upload),
                *common_fonts,
                *alice,
            )
        )
        == 0
    )
    assert b'filename="template.docx"' in _TemplateHandler.multipart
    assert upload.name.encode() not in _TemplateHandler.multipart

    forbidden_etag = f'"template-{TEMPLATE_ID}-{_TemplateHandler.revision}"'
    assert (
        main(
            (
                "templates",
                "update",
                TEMPLATE_ID,
                "--name",
                "Forbidden",
                "--description",
                "Forbidden",
                "--etag",
                forbidden_etag,
                *bob,
            )
        )
        == 1
    )
    assert (
        main(
            (
                "templates",
                "update",
                TEMPLATE_ID,
                "--name",
                "Stale",
                "--description",
                "Stale",
                "--etag",
                f'"template-{TEMPLATE_ID}-99"',
                *alice,
            )
        )
        == 1
    )
    assert (
        main(
            (
                "templates",
                "update",
                TEMPLATE_ID,
                "--name",
                "Renamed",
                "--description",
                "Updated",
                *alice,
            )
        )
        == 0
    )
    assert (
        main(
            (
                "templates",
                "replace",
                TEMPLATE_ID,
                "--file",
                str(upload),
                *common_fonts,
                *alice,
            )
        )
        == 0
    )
    assert main(("templates", "versions", TEMPLATE_ID, *bob)) == 0

    current = tmp_path / f"{storage_profile}-current.docx"
    historical = tmp_path / f"{storage_profile}-historical.docx"
    assert (
        main(
            (
                "templates",
                "download",
                TEMPLATE_ID,
                "--output",
                str(current),
                *bob,
            )
        )
        == 0
    )
    assert (
        main(
            (
                "templates",
                "version-download",
                TEMPLATE_ID,
                VERSION_ID,
                "--output",
                str(historical),
                *alice,
            )
        )
        == 0
    )
    assert current.read_bytes() == historical.read_bytes() == DOCX
    assert main(("templates", "restore", TEMPLATE_ID, VERSION_ID, *alice)) == 0
    assert main(("templates", "preferred", "--template-id", TEMPLATE_ID, *bob)) == 0
    assert main(("templates", "fallback", TEMPLATE_ID, *bob)) == 1
    assert main(("templates", "fallback", TEMPLATE_ID, *admin)) == 0
    assert main(("templates", "archive", TEMPLATE_ID, "--force", *alice)) == 0
    assert main(("templates", "show", TEMPLATE_ID, *bob)) == 1
    assert main(("templates", "delete", TEMPLATE_ID, "--force", *alice)) == 1
    _TemplateHandler.guarded = False
    assert main(("templates", "preferred", "--clear", *bob)) == 0
    assert main(("templates", "delete", TEMPLATE_ID, "--force", *admin)) == 0

    captured = capsys.readouterr()
    assert "hostile" not in captured.out + captured.err
    assert "Template mutation is not permitted." in captured.err
    assert "Template changed." in captured.err
    assert "Administrator access is required." in captured.err
    assert "Template is still referenced." in captured.err
