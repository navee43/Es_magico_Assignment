"""API endpoints, auth, persistence and file serving."""

import json

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.extractor import InvoiceExtractor
from backend.tests.conftest import NON_INVOICE_JSON, FakeLLM, invoice_json, make_digital_pdf


@pytest.fixture
def make_client(settings):
    def factory(*responses):
        llm = FakeLLM(*responses)
        app = create_app(settings, extractor=InvoiceExtractor(settings, llm_client=llm))
        c = TestClient(app)
        token = c.post("/api/v1/auth/login", json={"password": "pw"}).json()["token"]
        c.headers["Authorization"] = f"Bearer {token}"
        return c, llm

    return factory


def upload(c, data=None, name="inv.pdf"):
    return c.post("/api/v1/extract", files={"file": (name, data or make_digital_pdf(), "application/pdf")})


def test_health_is_public_and_leaks_nothing(make_client):
    c, _ = make_client()
    body = c.get("/health").json()
    assert body["status"] == "ok" and "test-key" not in json.dumps(body)


def test_protected_routes_require_auth(settings):
    c = TestClient(create_app(settings, extractor=InvoiceExtractor(settings, llm_client=FakeLLM())))
    for path in ("/api/v1/documents", "/api/v1/stats", "/api/v1/evaluation"):
        assert c.get(path).status_code == 401
    assert c.post("/api/v1/extract", files={"file": ("a.pdf", b"%PDF-")}).status_code == 401
    assert c.post("/api/v1/auth/login", json={"password": "nope"}).status_code == 401
    c.headers["Authorization"] = "Bearer forged.token"
    assert c.get("/api/v1/documents").status_code == 401


def test_extract_persist_and_read_back(make_client):
    c, _ = make_client(invoice_json())
    r = upload(c, name="../../evil name.pdf")
    assert r.status_code == 200
    body = r.json()
    doc_id = body["document_id"]
    assert body["result"]["invoice_number"] == "INV-2041" and body["result"]["needs_review"] is False
    assert body["filename"] == "evil name.pdf"

    listing = c.get("/api/v1/documents").json()
    assert listing["total"] == 1 and "stored_name" not in listing["items"][0]
    detail = c.get(f"/api/v1/documents/{doc_id}").json()
    assert detail["status"] == "success" and detail["cost_usd"] > 0 and detail["total_tokens"] == 1200
    result = c.get(f"/api/v1/documents/{doc_id}/result").json()
    assert result["model_output"]["total"] == 3776.0 and result["corrected"] is None
    f = c.get(f"/api/v1/documents/{doc_id}/file")
    assert f.status_code == 200 and f.headers["content-type"] == "application/pdf" and f.content.startswith(b"%PDF")


def test_unsupported_upload_is_rejected_and_not_stored(make_client):
    c, llm = make_client()
    r = upload(c, data=b"just some text", name="notes.txt")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "UNSUPPORTED_FILE_TYPE"
    doc_id = r.json()["detail"]["document_id"]
    assert c.get(f"/api/v1/documents/{doc_id}/file").status_code == 404
    assert llm.calls == []


def test_llm_failure_returns_502_and_is_recorded_as_failed(make_client):
    from backend.app.services.llm_client import LLMError

    c, _ = make_client(LLMError("LLM_SERVER_ERROR", "HTTP 503"))
    r = upload(c)
    assert r.status_code == 502 and r.json()["result"]["is_invoice"] is None
    stats = c.get("/api/v1/stats").json()
    assert stats["failed"] == 1 and stats["error_codes"] == {"LLM_SERVER_ERROR": 1}


def test_review_flow_preserves_original_output(make_client):
    c, _ = make_client(invoice_json(total=3876.0), invoice_json(total=3876.0))
    doc_id = upload(c).json()["document_id"]
    queue = c.get("/api/v1/documents", params={"review": "pending"}).json()
    assert queue["total"] == 1

    corrected = dict(c.get(f"/api/v1/documents/{doc_id}/result").json()["model_output"])
    corrected.pop("needs_review")
    corrected["total"] = 3776.0
    r = c.patch(f"/api/v1/documents/{doc_id}/review", json={"action": "approve", "corrected": corrected, "note": "fixed total"})
    assert r.status_code == 200
    body = r.json()
    assert body["review_status"] == "approved"
    assert body["model_output"]["total"] == 3876.0  # original kept
    assert body["corrected"]["total"] == 3776.0 and body["effective"]["total"] == 3776.0
    assert body["corrected_validation"]["status"] == "pass"
    assert c.get("/api/v1/documents", params={"review": "pending"}).json()["total"] == 0
    assert c.get("/api/v1/stats").json()["approved"] == 1


def test_invalid_correction_rejected(make_client):
    c, _ = make_client(NON_INVOICE_JSON)
    doc_id = upload(c).json()["document_id"]
    bad = {"is_invoice": True, "invoice_date": "14/03/2026", "currency": "INR"}
    r = c.patch(f"/api/v1/documents/{doc_id}/review", json={"action": "save", "corrected": bad})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_CORRECTION"


def test_stats_and_unknown_document(make_client):
    c, _ = make_client(invoice_json(), NON_INVOICE_JSON)
    upload(c)
    upload(c, data=make_digital_pdf(["A letter that is long enough to count as digital text."] * 3))
    s = c.get("/api/v1/stats").json()
    assert (s["total_documents"], s["invoices"], s["non_invoices"], s["needs_review"]) == (2, 1, 1, 0)
    assert len(s["recent_documents"]) == 2 and s["avg_cost_usd"] > 0
    assert c.get("/api/v1/documents/doesnotexist").status_code == 404
    assert c.get("/api/v1/documents/..%2F..%2Fetc").status_code == 404


def test_evaluation_endpoint(make_client, settings):
    c, _ = make_client()
    assert c.get("/api/v1/evaluation").json()["detail"]["code"] == "NO_EVALUATION"
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    (settings.reports_dir / "evaluation.json").write_text(json.dumps({"meta": {"tag": "main"}, "metrics": {}}))
    assert c.get("/api/v1/evaluation").json()["meta"]["tag"] == "main"


def test_spa_fallback_and_security_headers(settings, tmp_path):
    import dataclasses

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    s = dataclasses.replace(settings, frontend_dist=dist)
    c = TestClient(create_app(s, extractor=InvoiceExtractor(s, llm_client=FakeLLM())))
    r = c.get("/review/abc")
    assert r.status_code == 200 and "app" in r.text
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]
    assert c.get("/assets/app.js").status_code == 200
    assert c.get("/api/v1/nope").status_code in (401, 404)
    assert c.get("/../../etc/passwd").text == "<html>app</html>"  # traversal falls back to the SPA
