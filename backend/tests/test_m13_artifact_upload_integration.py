"""M13 Step 2 exact upload and formal-output reuse coverage."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_m12_artifact_integration import _all_formal_documents, _invoice_artifacts, _smtp
from test_m12_credit_integration import _issued_standard
from test_m12_refund_integration import _pay
from test_quote_payment_integration import _setup_company

from jai.db import get_session
from jai.main import app
from jai.services.artifacts import EXTERNAL_UPLOAD_RENDERER_VERSION

pytestmark = pytest.mark.integration


async def _live_pdf(client: AsyncClient, invoice_id: str) -> bytes:
    response = await client.get(f"/api/v1/invoices/{invoice_id}/pdf?preview=true")
    assert response.status_code == 200, response.text
    return response.content


async def _historical_pdf(client: AsyncClient, invoice_id: str, label: str) -> bytes:
    """Make a parseable opaque original that cannot take the SHA fast path."""
    live = await _live_pdf(client, invoice_id)
    # PDF readers accept comments after %%EOF.  This preserves a complete
    # renderer-produced document while making the retained source unambiguously
    # external and byte-distinct.
    original = live + f"\n% jai-m13-historical-original-{label}\n".encode()
    assert original != live
    assert hashlib.sha256(original).hexdigest() != hashlib.sha256(live).hexdigest()
    return original


async def _upload(
    client: AsyncClient, invoice_id: str, content: bytes, filename: str = "legacy.pdf"
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/invoices/{invoice_id}/artifacts",
        files={"file": (filename, content, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_upload_retains_exact_pdf_for_each_formal_kind_and_reuses_it(
    db_client: AsyncClient,
) -> None:
    documents, _ = await _all_formal_documents(db_client)
    for kind in ("STANDARD", "ADVANCE", "FINAL", "CREDIT_NOTE"):
        invoice_id = str(documents[kind]["id"])
        exact = await _historical_pdf(db_client, invoice_id, kind)
        uploaded = await _upload(db_client, invoice_id, exact, "../../历史 原件.pdf")
        assert uploaded["creation_reason"] == "UPLOAD"
        assert uploaded["sha256"] == hashlib.sha256(exact).hexdigest()
        assert uploaded["filename"] == "历史 原件.pdf"
        assert uploaded["renderer_version"] == EXTERNAL_UPLOAD_RENDERER_VERSION
        assert uploaded["locale"] == "en"
        assert len(uploaded["render_fingerprint"]) == 64

        # Preview deliberately bypasses history; ordinary download returns the
        # exact uploaded bytes and the stored filename for this presentation.
        preview = await db_client.get(f"/api/v1/invoices/{invoice_id}/pdf?preview=true")
        downloaded = await db_client.get(f"/api/v1/invoices/{invoice_id}/pdf")
        assert preview.status_code == downloaded.status_code == 200
        assert preview.content != exact
        assert downloaded.content == exact
        assert hashlib.sha256(downloaded.content).hexdigest() == uploaded["sha256"]
        assert downloaded.headers["content-type"] == "application/pdf"
        assert downloaded.headers["content-disposition"] == (
            'attachment; filename=" .pdf"; '
            "filename*=UTF-8''%E5%8E%86%E5%8F%B2%20%E5%8E%9F%E4%BB%B6.pdf"
        )
        assert downloaded.headers["x-content-type-options"] == "nosniff"
        artifacts = await _invoice_artifacts(db_client, invoice_id)
        assert len(artifacts) == 1
        historical = await db_client.get(
            f"/api/v1/invoices/{invoice_id}/artifacts/{uploaded['id']}"
        )
        assert historical.status_code == 200
        assert historical.content == exact
        assert hashlib.sha256(historical.content).hexdigest() == uploaded["sha256"]
        assert historical.headers["content-type"] == "application/pdf"
        assert (
            historical.headers["content-disposition"] == downloaded.headers["content-disposition"]
        )
        assert historical.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_uploaded_bytes_and_filename_are_the_successful_email_artifact(
    db_client: AsyncClient,
) -> None:
    documents, _ = await _all_formal_documents(db_client)
    invoice_id = str(documents["STANDARD"]["id"])
    exact = await _historical_pdf(db_client, invoice_id, "email")
    uploaded = await _upload(db_client, invoice_id, exact, "legacy-original.pdf")
    captured: dict[str, Any] = {}

    async def capture(**kwargs: Any) -> None:
        captured.update(kwargs)

    with (
        patch("jai.services.email._get_smtp_config", return_value=_smtp()),
        patch("jai.services.email._send_mail", side_effect=capture),
    ):
        sent = await db_client.post(
            f"/api/v1/invoices/{invoice_id}/send", json={"to": "customer@example.com"}
        )
    assert sent.status_code == 200, sent.text
    assert sent.json()["artifact_id"] == uploaded["id"]
    assert captured["attachment_bytes"] == exact
    assert captured["attachment_filename"] == "legacy-original.pdf"

    emails = await db_client.get(f"/api/v1/invoices/{invoice_id}/emails")
    assert emails.status_code == 200
    assert emails.json()["items"][0]["artifact_id"] == uploaded["id"]
    assert emails.json()["items"][0]["attachment_filename"] == "legacy-original.pdf"

    # Failed SMTP leaves the immutable upload in place and cannot link it.
    async def fail(**_kwargs: Any) -> None:
        raise OSError("test smtp failure")

    with (
        patch("jai.services.email._get_smtp_config", return_value=_smtp()),
        patch("jai.services.email._send_mail", side_effect=fail),
    ):
        failed = await db_client.post(
            f"/api/v1/invoices/{invoice_id}/send", json={"to": "customer@example.com"}
        )
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "FAILED"
    assert failed.json()["artifact_id"] is None
    artifacts = await _invoice_artifacts(db_client, invoice_id)
    assert len(artifacts) == 1 and artifacts[0]["id"] == uploaded["id"]
    emails = await db_client.get(f"/api/v1/invoices/{invoice_id}/emails")
    assert emails.status_code == 200
    failed_log = emails.json()["items"][0]
    assert failed_log["status"] == "FAILED" and failed_log["artifact_id"] is None


@pytest.mark.asyncio
async def test_upload_reuse_tracks_real_locale_payment_refund_and_pipeline_presentations(
    db_client: AsyncClient,
) -> None:
    documents, _ = await _all_formal_documents(db_client)

    # Locale is an independent formal presentation dimension.  Returning to
    # the default must use the UPLOAD row, not SHA equality with live output.
    locale_id = str(documents["FINAL"]["id"])
    locale_original = await _historical_pdf(db_client, locale_id, "locale")
    locale_upload = await _upload(db_client, locale_id, locale_original, "locale-original.pdf")
    changed_locale = await db_client.get(f"/api/v1/invoices/{locale_id}/pdf?locale=zh")
    assert changed_locale.status_code == 200 and changed_locale.content != locale_original
    assert len(await _invoice_artifacts(db_client, locale_id)) == 2

    # A real payment changes the locked settlement projection/fingerprint.
    seeds = await _setup_company(db_client)
    payment_invoice = await _issued_standard(db_client, seeds["rates"]["NL standard (21%)"]["id"])
    payment_id = str(payment_invoice["id"])
    payment_original = await _historical_pdf(db_client, payment_id, "payment")
    payment_upload = await _upload(db_client, payment_id, payment_original, "payment-original.pdf")
    payment = await _pay(db_client, payment_id, "10")
    changed_payment = await db_client.get(f"/api/v1/invoices/{payment_id}/pdf")
    assert changed_payment.status_code == 200 and changed_payment.content != payment_original
    assert len(await _invoice_artifacts(db_client, payment_id)) == 2
    deleted = await db_client.delete(f"/api/v1/payments/{payment['items'][0]['id']}")
    assert deleted.status_code == 200, deleted.text

    # A real refund belongs in a Credit Note formal presentation.  The fixture
    # leaves enough entitlement for one more refund without modifying its upload.
    refund_id = str(documents["CREDIT_NOTE"]["id"])
    original_refund_payment_id = str(documents["REFUND"]["id"])
    refund_original = await _historical_pdf(db_client, refund_id, "refund")
    refund_upload = await _upload(db_client, refund_id, refund_original, "refund-original.pdf")
    refund = await db_client.post(
        f"/api/v1/credit-notes/{refund_id}/refunds",
        json={
            "payment_date": "2026-02-05",
            "amount": "20",
            "reference": "m13",
            "note": "m13",
        },
    )
    assert refund.status_code == 201, refund.text
    added_refund_payment_id = next(
        item["id"]
        for item in refund.json()["items"]
        if item["id"] != original_refund_payment_id
    )
    changed_refund = await db_client.get(f"/api/v1/invoices/{refund_id}/pdf")
    assert changed_refund.status_code == 200 and changed_refund.content != refund_original
    assert len(await _invoice_artifacts(db_client, refund_id)) == 2

    # Pipeline identity is part of the fingerprint even when the HTML itself
    # is unchanged.  Patch only the version constant, never the rendering path.
    pipeline_id = str(documents["ADVANCE"]["id"])
    pipeline_original = await _historical_pdf(db_client, pipeline_id, "pipeline")
    pipeline_upload = await _upload(
        db_client, pipeline_id, pipeline_original, "pipeline-original.pdf"
    )
    with patch("jai.services.pdf.FORMAL_OUTPUT_PIPELINE_VERSION", "m13-test-pipeline-v3"):
        changed_pipeline = await db_client.get(f"/api/v1/invoices/{pipeline_id}/pdf")
    assert changed_pipeline.status_code == 200 and changed_pipeline.content != pipeline_original
    assert len(await _invoice_artifacts(db_client, pipeline_id)) == 2

    # Each original presentation is reusable by both Download and Send after
    # its transient state is restored.  This is the external-marker canonical
    # branch, as every upload SHA differs from its live renderer output.
    for invoice_id, original, uploaded in (
        (locale_id, locale_original, locale_upload),
        (payment_id, payment_original, payment_upload),
        (refund_id, refund_original, refund_upload),
        (pipeline_id, pipeline_original, pipeline_upload),
    ):
        if invoice_id == refund_id:
            undone = await db_client.delete(f"/api/v1/payments/{added_refund_payment_id}")
            assert undone.status_code == 200, undone.text
        restored = await db_client.get(f"/api/v1/invoices/{invoice_id}/pdf")
        assert restored.status_code == 200 and restored.content == original
        captured: dict[str, Any] = {}

        async def capture(_captured: dict[str, Any] = captured, **kwargs: Any) -> None:
            _captured.update(kwargs)

        with (
            patch("jai.services.email._get_smtp_config", return_value=_smtp()),
            patch("jai.services.email._send_mail", side_effect=capture),
        ):
            sent = await db_client.post(
                f"/api/v1/invoices/{invoice_id}/send", json={"to": "customer@example.com"}
            )
        assert sent.status_code == 200, sent.text
        assert sent.json()["artifact_id"] == uploaded["id"]
        assert captured["attachment_bytes"] == original
        assert captured["attachment_filename"] == uploaded["filename"]


@pytest.mark.asyncio
async def test_first_output_races_use_independent_connections_and_leave_no_partial_rows(
    db_client: AsyncClient,
    db_engine: Any,
) -> None:
    """Synchronize both requests just before their real parent-lock boundary."""
    documents, seeds = await _all_formal_documents(db_client)
    from conftest import _get_runtime_test_db_url

    database_name = db_engine.url.database
    assert database_name is not None
    race_engine = create_async_engine(
        _get_runtime_test_db_url(database_name), pool_size=2, max_overflow=0
    )
    race_sessions = async_sessionmaker(race_engine, expire_on_commit=False, class_=AsyncSession)

    async def race_provider() -> Any:
        async with race_sessions() as session:
            yield session

    old_provider = app.dependency_overrides[get_session]
    app.dependency_overrides[get_session] = race_provider
    cookies = dict(db_client.cookies)

    async def assert_no_partial_email(invoice_id: str) -> None:
        artifacts = await _invoice_artifacts(db_client, invoice_id)
        emails_response = await db_client.get(f"/api/v1/invoices/{invoice_id}/emails")
        assert emails_response.status_code == 200, emails_response.text
        assert len(artifacts) == 1
        assert emails_response.json()["items"] == []

    async def two_uploads(invoice_id: str) -> None:
        original = await _historical_pdf(db_client, invoice_id, "race-upload")
        barrier = asyncio.Barrier(2)
        connection_ids: set[int] = set()
        from jai.services.artifacts import create_uploaded_invoice_artifact as real_upload

        async def gated_upload(session: AsyncSession, **kwargs: Any) -> Any:
            connection_ids.add(int(await session.scalar(text("SELECT pg_backend_pid()"))))
            await barrier.wait()
            return await real_upload(session, **kwargs)

        with patch(
            "jai.services.artifacts.create_uploaded_invoice_artifact", side_effect=gated_upload
        ):
            async with (
                AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test", cookies=cookies
                ) as left_client,
                AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test", cookies=cookies
                ) as right_client,
            ):
                left, right = await asyncio.wait_for(
                    asyncio.gather(
                        left_client.post(
                            f"/api/v1/invoices/{invoice_id}/artifacts",
                            files={"file": ("left.pdf", original, "application/pdf")},
                        ),
                        right_client.post(
                            f"/api/v1/invoices/{invoice_id}/artifacts",
                            files={
                                "file": ("right.pdf", original + b"% second\n", "application/pdf")
                            },
                        ),
                    ),
                    timeout=10,
                )
        assert len(connection_ids) == 2
        assert sorted((left.status_code, right.status_code)) == [201, 409]
        winner = left if left.status_code == 201 else right
        loser = right if winner is left else left
        assert loser.json()["detail"]["code"] == "ARTIFACT_ALREADY_EXISTS"
        expected = original if winner is left else original + b"% second\n"
        assert hashlib.sha256(original).hexdigest() != hashlib.sha256(
            original + b"% second\n"
        ).hexdigest()
        assert winner.json()["sha256"] == hashlib.sha256(expected).hexdigest()
        artifacts = await _invoice_artifacts(db_client, invoice_id)
        assert len(artifacts) == 1
        assert artifacts[0]["id"] == winner.json()["id"]
        assert artifacts[0]["creation_reason"] == "UPLOAD"
        await assert_no_partial_email(invoice_id)

    async def upload_vs_output(invoice_id: str, *, send: bool, upload_first: bool) -> None:
        """Run each first-output winner deterministically at the real lock boundaries.

        Both requests independently reach the wrapper immediately before their
        original parent-lock function.  We release only the selected contender,
        await its complete HTTP response (an observable point proving its lock,
        persistence and commit completed), and only then release the other.
        The original exclusive ``FOR UPDATE`` upload path and shared renderer
        lock are still invoked unmodified.
        """
        original = await _historical_pdf(
            db_client,
            invoice_id,
            f"race-{'send' if send else 'download'}-{'upload' if upload_first else 'output'}",
        )
        upload_ready, output_ready = asyncio.Event(), asyncio.Event()
        allow_upload, allow_output = asyncio.Event(), asyncio.Event()
        first_lock_acquired, second_lock_attempted = asyncio.Event(), asyncio.Event()
        second_lock_returned = asyncio.Event()
        release_first = asyncio.Event()
        connection_ids: set[int] = set()
        from jai.services.artifacts import create_uploaded_invoice_artifact as real_upload
        from jai.services.pdf import render_invoice_pdf_artifact as real_render
        real_scalar = AsyncSession.scalar
        real_execute = AsyncSession.execute

        rendering_upload: contextvars.ContextVar[bool] = contextvars.ContextVar(
            "rendering_upload", default=False
        )
        upload_session: AsyncSession | None = None
        output_session: AsyncSession | None = None

        def lock_mode(statement: Any) -> str | None:
            lock = getattr(statement, "_for_update_arg", None)
            if lock is None:
                return None
            return "FOR SHARE" if lock.read else "FOR UPDATE"

        async def gate_after_parent_lock(session: AsyncSession, statement: Any) -> None:
            """Pause only after the original row-lock SQL has completed."""
            first_session = upload_session if upload_first else output_session
            first_lock = "FOR UPDATE" if upload_first else "FOR SHARE"
            if (
                session is first_session
                and lock_mode(statement) == first_lock
                and not first_lock_acquired.is_set()
            ):
                first_lock_acquired.set()
                await release_first.wait()

        def is_second_parent_lock(session: AsyncSession, statement: Any) -> bool:
            """Identify the opposing real parent-lock query in this scenario."""
            second_session = output_session if upload_first else upload_session
            second_lock = "FOR SHARE" if upload_first else "FOR UPDATE"
            return session is second_session and lock_mode(statement) == second_lock

        async def gated_scalar(
            session: AsyncSession, statement: Any, *args: Any, **kwargs: Any
        ) -> Any:
            second_parent_lock = is_second_parent_lock(session, statement)
            if second_parent_lock:
                second_lock_attempted.set()
            result = await real_scalar(session, statement, *args, **kwargs)
            if second_parent_lock:
                second_lock_returned.set()
            await gate_after_parent_lock(session, statement)
            return result

        async def gated_execute(
            session: AsyncSession, statement: Any, *args: Any, **kwargs: Any
        ) -> Any:
            second_parent_lock = is_second_parent_lock(session, statement)
            if second_parent_lock:
                second_lock_attempted.set()
            result = await real_execute(session, statement, *args, **kwargs)
            if second_parent_lock:
                second_lock_returned.set()
            await gate_after_parent_lock(session, statement)
            return result

        async def gated_upload(session: AsyncSession, **kwargs: Any) -> Any:
            nonlocal upload_session
            upload_session = session
            connection_ids.add(int(await session.scalar(text("SELECT pg_backend_pid()"))))
            upload_ready.set()
            await allow_upload.wait()
            token = rendering_upload.set(True)
            try:
                return await real_upload(session, **kwargs)
            finally:
                rendering_upload.reset(token)

        async def gated_render(
            session: AsyncSession, *args: Any, **kwargs: Any
        ) -> tuple[bytes, str, str, str]:
            # Upload invokes the renderer only after its exclusive parent lock.
            # Do not mistake that nested call for the competing output request.
            if rendering_upload.get():
                return await real_render(session, *args, **kwargs)
            nonlocal output_session
            output_session = session
            connection_ids.add(int(await session.scalar(text("SELECT pg_backend_pid()"))))
            output_ready.set()
            await allow_output.wait()
            return await real_render(session, *args, **kwargs)

        captured: dict[str, Any] = {}

        async def capture(**kwargs: Any) -> None:
            captured.update(kwargs)

        with (
            patch(
                "jai.services.artifacts.create_uploaded_invoice_artifact", side_effect=gated_upload
            ),
            patch("jai.services.pdf.render_invoice_pdf_artifact", side_effect=gated_render),
            patch.object(AsyncSession, "scalar", new=gated_scalar),
            patch.object(AsyncSession, "execute", new=gated_execute),
            patch("jai.services.email._get_smtp_config", return_value=_smtp()),
            patch("jai.services.email._send_mail", side_effect=capture),
        ):
            async with (
                AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test", cookies=cookies
                ) as upload_client,
                AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test", cookies=cookies
                ) as output_client,
            ):
                upload_request = asyncio.create_task(
                    upload_client.post(
                        f"/api/v1/invoices/{invoice_id}/artifacts",
                        files={"file": ("original.pdf", original, "application/pdf")},
                    )
                )
                output_request = asyncio.create_task(
                    output_client.post(
                        f"/api/v1/invoices/{invoice_id}/send",
                        json={"to": "customer@example.com"},
                    )
                    if send
                    else output_client.get(f"/api/v1/invoices/{invoice_id}/pdf")
                )
                await asyncio.wait_for(
                    asyncio.gather(upload_ready.wait(), output_ready.wait()), timeout=10
                )
                if upload_first:
                    allow_upload.set()
                    first_request, second_request = upload_request, output_request
                else:
                    allow_output.set()
                    first_request, second_request = output_request, upload_request
                await asyncio.wait_for(first_lock_acquired.wait(), timeout=10)
                if upload_first:
                    allow_output.set()
                else:
                    allow_upload.set()
                await asyncio.wait_for(second_lock_attempted.wait(), timeout=10)
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(second_lock_returned.wait(), timeout=0.05)
                assert not second_lock_returned.is_set()
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(second_request), timeout=0.05)
                release_first.set()
                first_response = await asyncio.wait_for(first_request, timeout=10)
                second_response = await asyncio.wait_for(second_request, timeout=10)
                assert second_lock_returned.is_set()
                upload, output = (
                    (first_response, second_response)
                    if upload_first
                    else (second_response, first_response)
                )
        assert len(connection_ids) == 2
        assert output.status_code == 200, output.text
        expected_reason = "SEND" if send else "DOWNLOAD"
        expected_status = 201 if upload_first else 409
        assert upload.status_code == expected_status, upload.text
        artifacts = await _invoice_artifacts(db_client, invoice_id)
        assert len(artifacts) == 1
        artifact = artifacts[0]
        historical = await db_client.get(
            f"/api/v1/invoices/{invoice_id}/artifacts/{artifact['id']}"
        )
        assert historical.status_code == 200, historical.text
        if upload_first:
            assert artifact["id"] == upload.json()["id"]
            assert artifact["creation_reason"] == "UPLOAD"
            assert artifact["filename"] == "original.pdf"
            assert historical.content == original
            if send:
                assert captured["attachment_bytes"] == original
                assert captured["attachment_filename"] == artifact["filename"]
            else:
                assert output.content == original
        else:
            assert upload.json()["detail"]["code"] == "ARTIFACT_ALREADY_EXISTS"
            assert artifact["creation_reason"] == expected_reason
            assert historical.content != original
            if send:
                assert captured["attachment_bytes"] == historical.content
                assert captured["attachment_filename"] == artifact["filename"]
            else:
                assert output.content == historical.content
        if send:
            assert output.json()["status"] == "SENT"
            assert output.json()["artifact_id"] == artifact["id"]
            emails_response = await db_client.get(f"/api/v1/invoices/{invoice_id}/emails")
            assert emails_response.status_code == 200, emails_response.text
            emails = emails_response.json()["items"]
            assert len(emails) == 1
            assert emails[0]["status"] == "SENT"
            assert emails[0]["artifact_id"] == artifact["id"]
            assert emails[0]["attachment_filename"] == artifact["filename"]
        else:
            assert (
                output.headers["content-disposition"]
                == historical.headers["content-disposition"]
            )
            await assert_no_partial_email(invoice_id)

    try:
        extra_standard = await _issued_standard(
            db_client, seeds["rates"]["NL standard (21%)"]["id"]
        )
        await two_uploads(str(extra_standard["id"]))
        await upload_vs_output(str(documents["ADVANCE"]["id"]), send=False, upload_first=True)
        await upload_vs_output(str(documents["FINAL"]["id"]), send=False, upload_first=False)
        await upload_vs_output(str(documents["CREDIT_NOTE"]["id"]), send=True, upload_first=True)
        await upload_vs_output(str(documents["STANDARD"]["id"]), send=True, upload_first=False)
    finally:
        app.dependency_overrides[get_session] = old_provider
        await race_engine.dispose()
