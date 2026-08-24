"""Exercise a real authenticated asynchronous conversion workflow."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import time
import uuid
import zipfile
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree


class Client:
    """Small explicit HTTP client retaining the secure session cookie manually."""

    def __init__(self, base_url: str) -> None:
        parsed = urlsplit(base_url)
        self._host = parsed.hostname or "127.0.0.1"
        self._port = parsed.port or 80
        self._cookie = ""
        self.csrf = ""

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        mutate: bool = False,
    ) -> tuple[int, dict[str, str], bytes]:
        headers: dict[str, str] = {}
        if self._cookie:
            headers["Cookie"] = self._cookie
        if content_type:
            headers["Content-Type"] = content_type
        if mutate:
            headers["X-CSRF-Token"] = self.csrf
        connection = http.client.HTTPConnection(self._host, self._port, timeout=30)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        content = response.read()
        response_headers = {
            name.casefold(): value for name, value in response.getheaders()
        }
        cookies = SimpleCookie()
        for value in response.getheaders():
            if value[0].casefold() == "set-cookie":
                cookies.load(value[1])
        session = cookies.get("md_converter_session")
        if session is not None:
            self._cookie = f"md_converter_session={session.value}"
        connection.close()
        return response.status, response_headers, content


def multipart(
    fields: list[tuple[str, str]], files: list[tuple[str, str, bytes]]
) -> tuple[bytes, str]:
    boundary = f"t20-{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields:
        body.extend(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for name, filename, content in files:
        body.extend(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
        )
        body.extend(content)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def candidate_reference(content: bytes) -> bytes:
    """Remove Pandoc sample links and unsupported dormant script mappings."""

    output = io.BytesIO()
    relationship_namespace = (
        "http://schemas.openxmlformats.org/package/2006/relationships"
    )
    drawing_namespace = "http://schemas.openxmlformats.org/drawingml/2006/main"
    with (
        zipfile.ZipFile(io.BytesIO(content)) as source,
        zipfile.ZipFile(output, "w") as target,
    ):
        payloads = {
            member.filename: source.read(member) for member in source.infolist()
        }
        for name, payload in tuple(payloads.items()):
            if not name.endswith(".rels"):
                continue
            root = ElementTree.fromstring(payload)  # noqa: S314 - trusted Pandoc data
            changed = False
            for node in root.findall(f"{{{relationship_namespace}}}Relationship"):
                if node.attrib.get("TargetMode") == "External":
                    node.attrib["Target"] = "document.xml"
                    node.attrib.pop("TargetMode")
                    changed = True
            if changed:
                payloads[name] = ElementTree.tostring(
                    root, encoding="utf-8", xml_declaration=True
                )
        theme_name = "word/theme/theme1.xml"
        theme = ElementTree.fromstring(  # noqa: S314 - trusted Pandoc data
            payloads[theme_name]
        )
        for parent in theme.iter():
            for child in tuple(parent):
                if child.tag == f"{{{drawing_namespace}}}font":
                    parent.remove(child)
        payloads[theme_name] = ElementTree.tostring(
            theme, encoding="utf-8", xml_declaration=True
        )
        for member in source.infolist():
            target.writestr(member, payloads[member.filename])
    return output.getvalue()


def require(status: int, expected: int, content: bytes) -> None:
    if status != expected:
        raise RuntimeError(f"HTTP {status}, expected {expected}: {content[:500]!r}")


def submit(
    client: Client,
    template: dict[str, object],
    output: str,
    filename: str,
    source: bytes,
) -> dict[str, object]:
    body, content_type = multipart(
        [
            ("template_id", str(template["id"])),
            ("template_version_id", str(template["current_version_id"])),
            ("output", output),
        ],
        [("source", filename, source)],
    )
    status, headers, content = client.request(
        "POST", "/api/v1/conversions", body=body, content_type=content_type, mutate=True
    )
    require(status, 202, content)
    location = headers["location"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        status, _, content = client.request("GET", location)
        require(status, 200, content)
        job = json.loads(content)
        if job["state"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.25)
    raise RuntimeError("conversion did not reach a terminal state")


def validate_result(client: Client, job: dict[str, object], output: str) -> None:
    path = f"/api/v1/conversions/{job['id']}/result"
    status, headers, content = client.request("GET", path)
    require(status, 200, content)
    if headers.get("cache-control") != "private, no-store":
        raise RuntimeError("result cache contract missing")
    if output == "docx":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if "[Content_Types].xml" not in archive.namelist():
                raise RuntimeError("invalid DOCX result")
        status, _, unavailable = client.request("GET", f"{path}/manifest")
        require(status, 409, unavailable)
        return
    if output == "pdf" and not content.startswith(b"%PDF-"):
        raise RuntimeError("invalid PDF result")
    if output == "both":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if archive.namelist() != [
                "document.docx",
                "document.pdf",
                "traceability.json",
            ]:
                raise RuntimeError("invalid combined result")
            embedded_manifest = archive.read("traceability.json")
    else:
        embedded_manifest = None
    status, _, manifest = client.request("GET", f"{path}/manifest")
    require(status, 200, manifest)
    decoded = json.loads(manifest)
    if (
        output == "pdf"
        and decoded["output_pdf_sha256"] != hashlib.sha256(content).hexdigest()
    ):
        raise RuntimeError("PDF manifest digest mismatch")
    if embedded_manifest is not None and embedded_manifest != manifest:
        raise RuntimeError("combined and sidecar manifests differ")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--template", required=True, type=Path)
    arguments = parser.parse_args()
    client = Client(arguments.base_url)
    status, _, content = client.request(
        "POST",
        "/api/v1/login",
        body=json.dumps(
            {"username": "admin", "password": "t20-test-password"}
        ).encode(),
        content_type="application/json",
    )
    require(status, 200, content)
    client.csrf = json.loads(content)["csrf_token"]
    template_body, template_type = multipart(
        [
            ("name", "T20 Smoke"),
            ("description", "Final image workflow"),
            *(
                ("expected_fonts", family)
                for family in (
                    "Aptos",
                    "Aptos Display",
                    "Calibri",
                    "Cambria",
                    "Cambria Math",
                    "Consolas",
                    "Courier New",
                    "Times New Roman",
                )
            ),
        ],
        [
            (
                "content",
                "template.docx",
                candidate_reference(arguments.template.read_bytes()),
            )
        ],
    )
    status, _, content = client.request(
        "POST",
        "/api/v1/templates",
        body=template_body,
        content_type=template_type,
        mutate=True,
    )
    require(status, 201, content)
    template = json.loads(content)
    source = b"# Final image\n\nReal **conversion** workflow.\n"
    for output in ("docx", "pdf", "both"):
        job = submit(client, template, output, "source.md", source)
        if job["state"] != "succeeded":
            raise RuntimeError(f"{output} conversion failed: {job}")
        validate_result(client, job, output)
    failed = submit(client, template, "docx", "mismatch.zip", source)
    if failed["state"] != "failed" or failed["error_code"] != "source_integrity":
        raise RuntimeError(f"source mismatch did not fail safely: {failed}")
    body, content_type = multipart(
        [
            ("template_id", str(template["id"])),
            ("template_version_id", str(template["current_version_id"])),
            ("output", "docx"),
        ],
        [("source", "forbidden.txt", source)],
    )
    status, _, content = client.request(
        "POST", "/api/v1/conversions", body=body, content_type=content_type, mutate=True
    )
    require(status, 422, content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
