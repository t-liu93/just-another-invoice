"""Unit coverage for the M13 formal-artifact upload contract boundary."""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from starlette import formparsers

from jai.api import pdf as pdf_api
from jai.auth.deps import current_mfa_user
from jai.config import Settings
from jai.main import app
from jai.models._enums import (
    DocumentArtifactReason,
    DocumentArtifactValidationCheckStatus,
    DocumentArtifactValidationConfidence,
    DocumentArtifactValidationField,
    DocumentArtifactValidationStatus,
)
from jai.services import artifacts as artifact_service
from jai.services.artifacts import ArtifactFileValidationError, validate_artifact_pdf


def _multipart_body(parts: list[tuple[str, str, bytes]]) -> tuple[dict[str, str], bytes]:
    boundary = "artifact-test-boundary"
    body = b"".join(
        b"--" + boundary.encode() + b"\r\n"
        + (
            b'Content-Disposition: form-data; name="'
            + name.encode()
            + b'"; filename="'
            + filename.encode()
            + b'"\r\nContent-Type: application/pdf\r\n\r\n'
        )
        + content
        + b"\r\n"
        for name, filename, content in parts
    ) + b"--" + boundary.encode() + b"--\r\n"
    return {"content-type": f"multipart/form-data; boundary={boundary}"}, body


@pytest.fixture
def artifact_upload_owner(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Authenticate and make raw-reader tests database-independent."""
    app.dependency_overrides[current_mfa_user] = lambda: SimpleNamespace(
        role="owner", company_id=uuid.uuid4()
    )

    async def eligible(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(pdf_api, "ensure_invoice_artifact_upload_eligible", eligible)
    try:
        yield
    finally:
        app.dependency_overrides.pop(current_mfa_user, None)


def _artifact_upload_urls() -> tuple[str, str]:
    invoice_id = uuid.uuid4()
    return (
        f"/api/v1/invoices/{invoice_id}/artifacts",
        f"/api/v1/invoices/{invoice_id}/artifacts/validate-upload?language=en",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "status_code", "code"),
    [
        ("missing", 404, "ARTIFACT_UPLOAD_NOT_FOUND"),
        ("cross-company", 404, "ARTIFACT_UPLOAD_NOT_FOUND"),
        ("draft", 404, "ARTIFACT_UPLOAD_NOT_FOUND"),
        ("cancelled", 404, "ARTIFACT_UPLOAD_NOT_FOUND"),
        ("existing-artifact", 409, "ARTIFACT_ALREADY_EXISTS"),
    ],
)
async def test_artifact_upload_eligibility_rejects_before_body_is_consumed(
    client: AsyncClient,
    artifact_upload_owner: None,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    status_code: int,
    code: str,
) -> None:
    """Both stubs perform their shared DB gate before opening request.stream()."""
    async def reject(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise HTTPException(status_code=status_code, detail={"code": code, "message": name})

    monkeypatch.setattr(pdf_api, "ensure_invoice_artifact_upload_eligible", reject)
    headers, body = _multipart_body([("file", "historic.pdf", b"not read")])

    for url in _artifact_upload_urls():
        emitted = 0

        async def stream() -> AsyncIterator[bytes]:
            nonlocal emitted
            emitted += 1
            yield body

        response = await client.post(url, headers=headers, content=stream())
        assert response.status_code == status_code
        assert response.json()["detail"]["code"] == code
        assert emitted == 0


@pytest.mark.asyncio
async def test_artifact_upload_parameter_validation_has_stable_non_file_envelope(
    client: AsyncClient, artifact_upload_owner: None
) -> None:
    cases = (
        "/api/v1/invoices/not-a-uuid/artifacts",
        f"/api/v1/invoices/{uuid.uuid4()}/artifacts/validate-upload",
        f"/api/v1/invoices/{uuid.uuid4()}/artifacts/validate-upload?language=nl",
    )
    for url in cases:
        response = await client.post(url)
        assert response.status_code == 422
        assert response.json() == {
            "detail": {
                "code": "INVALID_ARTIFACT_UPLOAD_REQUEST",
                "message": "The artifact upload request is invalid.",
            }
        }


@pytest.mark.asyncio
async def test_artifact_upload_multipart_exact_limit_never_uses_starlette_spool(
    client: AsyncClient,
    valid_pdf: bytes,
    artifact_upload_owner: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both stubs read the raw ASGI stream before default multipart parsing."""
    content = valid_pdf + b" " * 128
    monkeypatch.setattr(
        pdf_api, "get_settings", lambda: SimpleNamespace(max_artifact_bytes=len(content))
    )

    def fail_if_spooled(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("Starlette multipart spool must not be used")

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", fail_if_spooled)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", fail_if_spooled)
    headers, body = _multipart_body([("file", "historic.pdf", content)])

    for url in _artifact_upload_urls():
        response = await client.post(url, headers=headers, content=body)
        # Step 1 exposes a contract stub after accepting the bounded valid body.
        assert response.status_code == 501


@pytest.mark.asyncio
async def test_artifact_upload_multipart_rejects_one_byte_over_limit(
    client: AsyncClient,
    valid_pdf: bytes,
    artifact_upload_owner: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pdf_api, "get_settings", lambda: SimpleNamespace(max_artifact_bytes=len(valid_pdf))
    )

    def fail_if_spooled(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("rejected multipart input must not create a temp file")

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", fail_if_spooled)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", fail_if_spooled)
    headers, body = _multipart_body([("file", "historic.pdf", valid_pdf + b"x")])

    for url in _artifact_upload_urls():
        response = await client.post(url, headers=headers, content=body)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_ARTIFACT_FILE"


@pytest.mark.asyncio
async def test_artifact_upload_multipart_stops_far_over_chunked_body(
    client: AsyncClient,
    valid_pdf: bytes,
    artifact_upload_owner: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = len(valid_pdf)
    monkeypatch.setattr(pdf_api, "get_settings", lambda: SimpleNamespace(max_artifact_bytes=limit))
    headers, full_body = _multipart_body([("file", "historic.pdf", valid_pdf + b"x" * 200)])
    # Split the multipart envelope before streaming its file data in many ASGI
    # chunks.  The endpoint must stop well before the intentionally distant end.
    first_content = full_body.index(valid_pdf) + len(valid_pdf)
    chunks = [full_body[:first_content]] + [b"x" * 16 for _ in range(100)]
    emitted = 0

    async def stream() -> AsyncIterator[bytes]:
        nonlocal emitted
        for chunk in chunks:
            emitted += 1
            yield chunk

    for url in _artifact_upload_urls():
        emitted = 0
        response = await client.post(url, headers=headers, content=stream())
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_ARTIFACT_FILE"
        assert emitted < len(chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parts",
    [
        [("file", "one.pdf", b"%PDF"), ("file", "two.pdf", b"%PDF")],
        [("file", "one.pdf", b"%PDF"), ("other", "two.pdf", b"%PDF")],
    ],
)
async def test_artifact_upload_multipart_rejects_duplicate_or_extra_file_parts(
    client: AsyncClient,
    artifact_upload_owner: None,
    monkeypatch: pytest.MonkeyPatch,
    parts: list[tuple[str, str, bytes]],
) -> None:
    monkeypatch.setattr(pdf_api, "get_settings", lambda: SimpleNamespace(max_artifact_bytes=1024))
    headers, body = _multipart_body(parts)

    for url in _artifact_upload_urls():
        response = await client.post(url, headers=headers, content=body)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_ARTIFACT_FILE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "body"),
    [
        (
            {"content-type": f"multipart/form-data; boundary={'x' * 257}"},
            b"--" + b"x" * 257 + b"--\r\n",
        ),
        (
            {"content-type": "multipart/form-data; boundary=bad"},
            b"--bad\r\nBroken header\r\n\r\n%PDF\r\n--bad--\r\n",
        ),
        (
            {"content-type": "multipart/form-data; boundary=bad"},
            b"--bad\r\nContent-Disposition: form-data; name=\"file\"; filename=\"x.pdf\""
            b"\r\nContent-Type: application/pdf\r\n"
            b"Content-Type: application/pdf\r\n\r\n%PDF\r\n--bad--\r\n",
        ),
        (
            {"content-type": "multipart/form-data; boundary=bad"},
            b"--bad\r\n"
            + b"".join(f"X-{index}: value\r\n".encode() for index in range(9))
            + b"\r\n%PDF\r\n--bad--\r\n",
        ),
        (
            {"content-type": "multipart/form-data; boundary=bad"},
            b"--bad\r\nContent-Disposition: form-data; name=\"file\"",
        ),
        (
            {"content-type": "multipart/form-data; boundary=bad"},
            b"--bad\r\nContent-Disposition: form-data; name=\"file\"; filename=\"x.pdf\""
            b"\r\nContent-Type: application/pdf\r\n\r\n%PDF",
        ),
    ],
)
async def test_artifact_upload_multipart_normalizes_adversarial_framing(
    client: AsyncClient,
    artifact_upload_owner: None,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    body: bytes,
) -> None:
    monkeypatch.setattr(pdf_api, "get_settings", lambda: SimpleNamespace(max_artifact_bytes=1024))
    for url in _artifact_upload_urls():
        response = await client.post(url, headers=headers, content=body)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_ARTIFACT_FILE"


@pytest.mark.asyncio
async def test_artifact_upload_multipart_handles_one_byte_chunks_and_stops_after_closing_boundary(
    client: AsyncClient,
    valid_pdf: bytes,
    artifact_upload_owner: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pdf_api, "get_settings", lambda: SimpleNamespace(max_artifact_bytes=len(valid_pdf))
    )
    headers, body = _multipart_body([("file", "historic.pdf", valid_pdf)])

    for url in _artifact_upload_urls():
        one_byte_emitted = 0

        async def one_byte_stream() -> AsyncIterator[bytes]:
            nonlocal one_byte_emitted
            for byte in body:
                one_byte_emitted += 1
                yield bytes([byte])

        response = await client.post(url, headers=headers, content=one_byte_stream())
        assert response.status_code == 501
        # The parser recognizes the final delimiter before its conventional
        # trailing CRLF, so even a byte-at-a-time legal request stops early.
        assert one_byte_emitted < len(body)

        epilogue_emitted = 0

        async def epilogue_stream() -> AsyncIterator[bytes]:
            nonlocal epilogue_emitted
            yield body
            epilogue_emitted += 1
            for _ in range(100):
                epilogue_emitted += 1
                yield b"x" * 1024

        response = await client.post(url, headers=headers, content=epilogue_stream())
        assert response.status_code == 501
        assert epilogue_emitted == 0


@pytest.mark.asyncio
async def test_artifact_upload_multipart_accepts_same_bytes_regardless_of_epilogue_chunking(
    client: AsyncClient,
    valid_pdf: bytes,
    artifact_upload_owner: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legal closing delimiter wins over epilogue in the same ASGI chunk."""
    monkeypatch.setattr(
        pdf_api, "get_settings", lambda: SimpleNamespace(max_artifact_bytes=len(valid_pdf))
    )

    def fail_if_spooled(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("raw multipart parsing must not create a temp file")

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", fail_if_spooled)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", fail_if_spooled)
    headers, multipart = _multipart_body([("file", "historic.pdf", valid_pdf)])
    epilogue = b"e" * 20_000
    body = multipart + epilogue

    for url in _artifact_upload_urls():
        split_emitted = 0

        async def split_stream() -> AsyncIterator[bytes]:
            nonlocal split_emitted
            split_emitted += 1
            yield multipart
            split_emitted += 1
            yield epilogue

        split = await client.post(url, headers=headers, content=split_stream())
        assert split.status_code == 501
        # The endpoint must not ask ASGI for the already-irrelevant epilogue.
        assert split_emitted == 1

        coalesced_emitted = 0

        async def coalesced_stream() -> AsyncIterator[bytes]:
            nonlocal coalesced_emitted
            coalesced_emitted += 1
            yield body

        coalesced = await client.post(url, headers=headers, content=coalesced_stream())
        assert coalesced.status_code == split.status_code
        assert coalesced_emitted == 1


@pytest.mark.asyncio
async def test_artifact_upload_multipart_bounds_large_preamble_without_content_length(
    client: AsyncClient,
    artifact_upload_owner: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf_api, "get_settings", lambda: SimpleNamespace(max_artifact_bytes=64))
    headers = {"content-type": "multipart/form-data; boundary=bounded"}
    chunks = [b"\r\n" * 128 for _ in range(100)]

    for url in _artifact_upload_urls():
        emitted = 0

        async def preamble_stream() -> AsyncIterator[bytes]:
            nonlocal emitted
            for chunk in chunks:
                emitted += 1
                yield chunk

        response = await client.post(url, headers=headers, content=preamble_stream())
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_ARTIFACT_FILE"
        assert emitted < len(chunks)


@pytest.fixture(scope="module")
def valid_pdf() -> bytes:
    """Return a minimal valid one-page PDF without mutating renderer globals."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
        b"/Resources << >> /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    content = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(content))
        content += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(content)
    xref_rows = b"".join(
        f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]
    )
    return (
        content
        + f"xref\n0 {len(objects) + 1}\n".encode()
        + b"0000000000 65535 f \n"
        + xref_rows
        + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
        + f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )


class TestArtifactPdfValidation:
    def test_valid_pdf_preserves_exact_bytes_and_accepts_exact_limit(
        self, valid_pdf: bytes
    ) -> None:
        original = bytes(valid_pdf)
        assert validate_artifact_pdf(valid_pdf, "application/pdf", len(valid_pdf)) == 1
        assert valid_pdf == original

    def test_mime_parameter_is_normalized(self, valid_pdf: bytes) -> None:
        assert (
            validate_artifact_pdf(valid_pdf, "application/pdf; charset=binary", len(valid_pdf))
            == 1
        )

    @pytest.mark.parametrize(
        ("content", "mime_type", "max_bytes"),
        [
            (b"not a PDF", "application/pdf", 100),
            (b"%PDF-not-a-real-document", "application/pdf", 100),
            (b"%PDF-not-a-real-document", "text/plain", 100),
            (b"%PDF-not-a-real-document", "application/pdf", 1),
            (b"\xef\xbb\xbf%PDF-1.7", "application/pdf", 100),
        ],
    )
    def test_invalid_mime_size_magic_and_malformed_pdf_have_one_stable_error(
        self, content: bytes, mime_type: str, max_bytes: int
    ) -> None:
        with pytest.raises(ArtifactFileValidationError) as exc_info:
            validate_artifact_pdf(content, mime_type, max_bytes)
        assert str(exc_info.value) == (
            "The file must be a valid PDF within the configured size limit."
        )

    def test_over_limit_is_rejected_without_parsing(self, valid_pdf: bytes) -> None:
        with pytest.raises(ArtifactFileValidationError):
            validate_artifact_pdf(valid_pdf, "application/pdf", len(valid_pdf) - 1)

    def test_zero_page_pdf_is_rejected(
        self, valid_pdf: bytes, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class ZeroPageDocument:
            def __init__(self, content: bytes) -> None:
                assert content == valid_pdf

            def __len__(self) -> int:
                return 0

            def close(self) -> None:
                pass

        monkeypatch.setattr(artifact_service.pdfium, "PdfDocument", ZeroPageDocument)
        with pytest.raises(ArtifactFileValidationError) as exc_info:
            validate_artifact_pdf(valid_pdf, "application/pdf", len(valid_pdf))
        assert str(exc_info.value) == (
            "The file must be a valid PDF within the configured size limit."
        )


def test_artifact_upload_contract_is_locked_in_openapi() -> None:
    schema = app.openapi()
    paths = schema["paths"]
    components = schema["components"]["schemas"]
    upload = paths["/api/v1/invoices/{invoice_id}/artifacts"]["post"]
    validate = paths["/api/v1/invoices/{invoice_id}/artifacts/validate-upload"]["post"]

    assert upload["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DocumentArtifactRead"
    }
    assert upload["requestBody"]["content"]["multipart/form-data"]
    assert validate["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DocumentArtifactValidationRead"
    }
    assert validate["parameters"][1]["name"] == "language"
    assert validate["parameters"][1]["required"] is True

    assert upload["responses"]["404"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DocumentArtifactNotFoundErrorResponse"
    }
    assert upload["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DocumentArtifactUploadConflictErrorResponse"
    }
    expected_422 = {"$ref": "#/components/schemas/DocumentArtifactUnprocessableErrorResponse"}
    assert upload["responses"]["422"]["content"]["application/json"]["schema"] == expected_422
    assert validate["responses"]["404"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DocumentArtifactNotFoundErrorResponse"
    }
    assert validate["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DocumentArtifactValidationConflictErrorResponse"
    }
    assert validate["responses"]["422"]["content"]["application/json"]["schema"] == expected_422
    assert validate["responses"]["502"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DocumentArtifactValidationFailedErrorResponse"
    }

    unprocessable = components["DocumentArtifactUnprocessableErrorResponse"]
    assert unprocessable["properties"]["detail"]["anyOf"] == [
        {"$ref": "#/components/schemas/DocumentArtifactInvalidFileErrorDetail"},
        {"$ref": "#/components/schemas/DocumentArtifactInvalidRequestErrorDetail"},
    ]
    generated_types = (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "schema.d.ts"
    ).read_text()
    assert (
        'detail: components["schemas"]["DocumentArtifactInvalidFileErrorDetail"] '
        '| components["schemas"]["DocumentArtifactInvalidRequestErrorDetail"];'
    ) in generated_types

    assert components["DocumentArtifactReason"]["enum"] == ["DOWNLOAD", "SEND", "UPLOAD"]
    assert components["DocumentArtifactValidationStatus"]["enum"] == [
        "MATCH",
        "WARNING",
        "INCONCLUSIVE",
    ]
    assert components["DocumentArtifactValidationCheckStatus"]["enum"] == [
        "MATCH",
        "MISMATCH",
        "NOT_FOUND",
    ]
    assert components["DocumentArtifactValidationField"]["enum"] == [
        "DOCUMENT_NUMBER",
        "DOCUMENT_KIND",
        "DOCUMENT_DATE",
        "SUPPLY_OR_ADVANCE_DATE",
        "SELLER",
        "BUYER",
        "CURRENCY",
        "TOTAL_EXCL_VAT",
        "VAT_TOTAL",
        "TOTAL_INCL_VAT",
    ]
    assert components["DocumentArtifactValidationConfidence"]["enum"] == ["HIGH", "MEDIUM", "LOW"]
    assert components["DocumentArtifactUploadConflictErrorDetail"]["properties"]["code"] == {
        "type": "string",
        "const": "ARTIFACT_ALREADY_EXISTS",
        "title": "Code",
    }
    assert components["DocumentArtifactValidationConflictErrorDetail"]["properties"]["code"] == {
        "type": "string",
        "enum": ["ARTIFACT_ALREADY_EXISTS", "AI_NOT_CONFIGURED"],
        "title": "Code",
    }


def test_artifact_upload_enums_and_setting_are_independent() -> None:
    assert DocumentArtifactReason.UPLOAD == "UPLOAD"
    assert DocumentArtifactValidationStatus.MATCH == "MATCH"
    assert DocumentArtifactValidationCheckStatus.MISMATCH == "MISMATCH"
    assert DocumentArtifactValidationField.VAT_TOTAL == "VAT_TOTAL"
    assert DocumentArtifactValidationConfidence.LOW == "LOW"
    assert Settings.model_fields["max_artifact_bytes"].default == 10 * 1024 * 1024
    assert Settings.model_fields["max_receipt_bytes"].default == 10 * 1024 * 1024
