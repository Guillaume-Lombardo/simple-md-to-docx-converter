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
    location = submit_location(client, template, output, filename, source)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        status, _, content = client.request("GET", location)
        require(status, 200, content)
        job = json.loads(content)
        if job["state"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.25)
    raise RuntimeError("conversion did not reach a terminal state")


def submit_location(
    client: Client,
    template: dict[str, object],
    output: str,
    filename: str,
    source: bytes,
) -> str:
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
    return headers["location"]


def wait_for_running_job(client: Client, location: str) -> dict[str, object]:
    deadline = time.monotonic() + 120
    job: dict[str, object] = {}
    while time.monotonic() < deadline:
        status, _, content = client.request("GET", location)
        require(status, 200, content)
        job = json.loads(content)
        if job["state"] == "running":
            return job
        if job["state"] in {"succeeded", "failed", "cancelled"}:
            raise RuntimeError(
                f"blocking conversion terminated before rendering: {job}"
            )
        time.sleep(0.25)
    raise RuntimeError(f"blocking conversion was not claimed: {job}")


def get_job(client: Client, location: str) -> dict[str, object]:
    status, _, content = client.request("GET", location)
    require(status, 200, content)
    return json.loads(content)


def validate_result(  # noqa: PLR0912 - one assertion workflow covers all outputs
    client: Client, job: dict[str, object], output: str
) -> None:
    path = f"/api/v1/conversions/{job['id']}/result"
    status, headers, content = client.request("GET", path)
    require(status, 200, content)
    if headers.get("cache-control") != "private, no-store":
        raise RuntimeError("result cache contract missing")
    if output == "docx":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if not {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(
                archive.namelist()
            ):
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
            docx = archive.read("document.docx")
            pdf = archive.read("document.pdf")
            embedded_manifest = archive.read("traceability.json")
        with zipfile.ZipFile(io.BytesIO(docx)) as document:
            if not {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(
                document.namelist()
            ):
                raise RuntimeError("invalid combined DOCX result")
        if not pdf.startswith(b"%PDF-"):
            raise RuntimeError("invalid combined PDF result")
    else:
        pdf = content
        embedded_manifest = None
    status, _, manifest = client.request("GET", f"{path}/manifest")
    require(status, 200, manifest)
    decoded = json.loads(manifest)
    if decoded["output_pdf_sha256"] != hashlib.sha256(pdf).hexdigest():
        raise RuntimeError("PDF manifest digest mismatch")
    if decoded["output_pdf_bytes"] != len(pdf):
        raise RuntimeError("PDF manifest size mismatch")
    if decoded["schema_version"] != 1 or decoded["output_format"] != "pdf":
        raise RuntimeError("PDF manifest invariants mismatch")
    if embedded_manifest is not None and embedded_manifest != manifest:
        raise RuntimeError("combined and sidecar manifests differ")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--template", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--submit-blocking-job", type=Path)
    mode.add_argument("--assert-running-job", type=Path)
    mode.add_argument("--recover-job", type=Path)
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
    location_file = arguments.assert_running_job or arguments.recover_job
    if location_file is not None:
        location = location_file.read_text(encoding="utf-8").strip()
        if arguments.assert_running_job is not None:
            job = get_job(client, location)
            if job["state"] != "running" or job.get("result_url") is not None:
                raise RuntimeError(f"interrupted job is not durably leased: {job}")
            return 0
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = get_job(client, location)
            if job["state"] == "succeeded":
                validate_result(client, job, "docx")
                return 0
            if job["state"] in {"failed", "cancelled"}:
                raise RuntimeError(f"interrupted job did not recover safely: {job}")
            time.sleep(0.25)
        raise RuntimeError("interrupted job was not recovered")
    if arguments.template is None:
        parser.error("--template is required for conversion submission")
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
    if arguments.submit_blocking_job is not None:
        location = submit_location(
            client,
            template,
            "docx",
            "blocking.md",
            b"# Blocking\n\n```mermaid\nflowchart LR\nA-->B\n```\n",
        )
        wait_for_running_job(client, location)
        arguments.submit_blocking_job.write_text(location, encoding="utf-8")
        return 0
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
