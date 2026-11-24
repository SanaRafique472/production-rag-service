"""End-to-end document workflow: Word uploads, extracted content, reload, document Q&A.

Fake providers prove the plumbing (they say nothing about answer quality).
"""

from app.generation import NOT_AVAILABLE
from tests.api.conftest import auth_headers, upload


async def test_word_and_pdf_formats_upload(client, user, samples):
    for key in ("features_docx", "blank_image_docx", "legacy_doc", "handbook_docx", "policy_pdf"):
        r = await upload(client, user, samples[key])
        assert r.status_code == 201, (key, r.text)
        body = r.json()
        assert body["status"] == "ready" and body["chunk_count"] > 0 and body["can_reprocess"] is True
    types = {d["filename"]: d["file_type"] for d in (await client.get("/documents", headers=user)).json()["items"]}
    assert types["leave_policy_legacy.doc"] == "doc" and types["site_inspection.docx"] == "docx"


async def test_renamed_doc_is_stored_as_doc(client, user, samples):
    r = await upload(client, user, samples["legacy_doc"], name="renamed.docx")
    assert r.status_code == 201, r.text
    assert r.json()["file_type"] == "doc"


async def test_upload_limits_endpoint(client):
    r = await client.get("/documents/limits")
    assert r.status_code == 200
    assert {".doc", ".docx", ".pdf"} <= set(r.json()["extensions"]) and r.json()["max_upload_mb"] >= 20


async def test_content_endpoint_returns_structured_text(client, user, samples):
    doc = (await upload(client, user, samples["features_docx"])).json()
    r = await client.get(f"/documents/{doc['id']}/content", headers=user)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_blocks"] == doc["chunk_count"] and body["total_tokens"] > 0
    text = "\n".join(b["text"] for b in body["blocks"])
    assert "Northwind Traders" in text and "sixty days in writing" in text and "1. Send the invoice." in text
    assert any(b["kind"] == "table" and "| North | Licences | 5 | 5 |" in b["text"] for b in body["blocks"])
    other = await auth_headers(client, "other@example.com")
    assert (await client.get(f"/documents/{doc['id']}/content", headers=other)).status_code == 404


async def test_reload_reextracts_without_new_upload(client, user, samples):
    doc = (await upload(client, user, samples["policy_pdf"])).json()
    q = {"question": "How many paid sick days per year?", "document_ids": [doc["id"]]}
    before = (await client.post("/chat/ask", headers=user, json=q)).json()
    assert not before["refused"]

    r = await client.post(f"/documents/{doc['id']}/reload", headers=user)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "reextracted" and body["previous_chunk_count"] == doc["chunk_count"]
    assert body["document"]["status"] == "ready" and body["document"]["chunk_count"] == doc["chunk_count"]

    after = (await client.post("/chat/ask", headers=user, json=q)).json()
    assert not after["refused"] and after["answer"] == before["answer"]
    store = client.app.state.container.store  # type: ignore[attr-defined]
    assert await store.count_document(await _uid(client, user), doc["id"]) == doc["chunk_count"]  # no stale points


async def _uid(client, headers) -> str:
    return (await client.get("/auth/me", headers=headers)).json()["id"]


async def test_reload_without_stored_file_reindexes(app_factory, samples):
    async with app_factory(store_original_files=False) as c:
        h = await auth_headers(c)
        doc = (await upload(c, h, samples["handbook_docx"])).json()
        assert doc["can_reprocess"] is False
        store = c.app.state.container.store  # type: ignore[attr-defined]
        uid = await _uid(c, h)
        await store.delete_document(uid, doc["id"])  # simulate a lost vector index
        r = await c.post(f"/documents/{doc['id']}/reload", headers=h)
        assert r.status_code == 200 and r.json()["mode"] == "reindexed"
        assert await store.count_document(uid, doc["id"]) == doc["chunk_count"]


async def test_reload_unknown_document_404(client, user):
    assert (await client.post("/documents/nope/reload", headers=user)).status_code == 404


async def test_document_qa_uses_full_document_and_exact_refusal(client, user, samples):
    doc = (await upload(client, user, samples["features_docx"])).json()
    scope = {"document_ids": [doc["id"]]}
    r = (
        await client.post(
            "/chat/ask", headers=user, json={"question": "Who is the client registered in Lahore?", **scope}
        )
    ).json()
    assert r["context_mode"] == "full_document" and not r["refused"], r
    assert r["citations"] and all(c["document_id"] == doc["id"] for c in r["citations"])
    assert r["kept_count"] == doc["chunk_count"]  # the whole document was given to the model

    # many questions about the same upload, same session, no re-upload
    session = r["session_id"]
    for question in ("What is the termination notice period in writing?", "Which governing law applies, Pakistan?"):
        nxt = (
            await client.post("/chat/ask", headers=user, json={"question": question, "session_id": session, **scope})
        ).json()
        assert not nxt["refused"] and nxt["session_id"] == session

    missing = (
        await client.post("/chat/ask", headers=user, json={"question": "What is the CEO's salary bonus?", **scope})
    ).json()
    assert missing["refused"] and missing["answer"] == NOT_AVAILABLE
    assert NOT_AVAILABLE == "This information is not available in the uploaded document."


async def test_large_documents_use_retrieval(app_factory, samples):
    async with app_factory(full_context_max_tokens=10) as c:
        h = await auth_headers(c)
        doc = (await upload(c, h, samples["policy_pdf"])).json()
        r = (
            await c.post(
                "/chat/ask", headers=h, json={"question": "How many paid sick days?", "document_ids": [doc["id"]]}
            )
        ).json()
        assert r["context_mode"] == "retrieval" and not r["refused"]


async def test_delete_removes_stored_file(client, user, samples):
    doc = (await upload(client, user, samples["legacy_doc"])).json()
    assert (await client.delete(f"/documents/{doc['id']}", headers=user)).status_code == 204
    assert (await client.post(f"/documents/{doc['id']}/reload", headers=user)).status_code == 404
    # the same file can be uploaded again afterwards
    assert (await upload(client, user, samples["legacy_doc"])).status_code == 201
