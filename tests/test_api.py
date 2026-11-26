"""Automated API tests for Ajaia Docs using isolated temporary SQLite databases."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Iterator

import docx
import pytest
from fastapi.testclient import TestClient

# Ensure the repository root is importable without extra config files.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.main import app  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Provide a FastAPI TestClient backed by a fresh temporary SQLite file per test."""
    isolated_db = tmp_path / "isolated_test.db"
    monkeypatch.setenv("AJAIA_DB_PATH", str(isolated_db))
    with TestClient(app) as test_client:
        yield test_client


def _users_by_name(client: TestClient) -> dict[str, dict]:
    response = client.get("/api/users")
    assert response.status_code == 200
    users = response.json()
    assert len(users) == 3
    return {user["name"]: user for user in users}


def _auth_header(user_id: int) -> dict[str, str]:
    return {"X-User-Id": str(user_id)}


def test_seeded_users_and_welcome_document(client: TestClient) -> None:
    """Startup seeds Alice, Bob, and Sara plus 1 welcome document owned by Alice."""
    users = _users_by_name(client)
    assert set(users.keys()) == {"Alice", "Bob", "Sara"}
    assert users["Alice"]["email"] == "alice@ajaia.dev"

    alice_docs = client.get(
        "/api/documents",
        headers=_auth_header(users["Alice"]["id"]),
    )
    assert alice_docs.status_code == 200
    payload = alice_docs.json()
    assert len(payload["owned"]) == 1
    assert payload["owned"][0]["title"] == "Welcome to Ajaia Docs"
    assert payload["shared_with_me"] == []

    # Frontend index route loads cleanly
    index_res = client.get("/")
    assert index_res.status_code == 200
    assert "Ajaia Docs" in index_res.text


def test_create_rename_save_and_reopen_rich_text_document(client: TestClient) -> None:
    """Users can create, rename, save rich-text HTML, and reopen with formatting preserved."""
    users = _users_by_name(client)
    alice_headers = _auth_header(users["Alice"]["id"])

    # 1. Create a new blank document
    create_res = client.post(
        "/api/documents",
        json={"title": "Untitled Document", "content_html": ""},
        headers=alice_headers,
    )
    assert create_res.status_code == 201
    doc_id = create_res.json()["id"]

    # 2. Rename inline without overwriting content
    rename_res = client.put(
        f"/api/documents/{doc_id}",
        json={"title": "Q4 Engineering Roadmap"},
        headers=alice_headers,
    )
    assert rename_res.status_code == 200
    assert rename_res.json()["title"] == "Q4 Engineering Roadmap"

    # Reject empty titles with 400
    empty_title_res = client.put(
        f"/api/documents/{doc_id}",
        json={"title": "   "},
        headers=alice_headers,
    )
    assert empty_title_res.status_code == 400

    # 3. Save rich-text HTML (headings, bold, italic, underline, bullet & numbered lists)
    rich_html = (
        "<h1>Engineering Goals</h1>"
        "<h2>Core Priorities</h2>"
        "<p>Ship <strong>reliable</strong>, <em>clean</em>, and <u>tested</u> features.</p>"
        "<ul><li>SQLite persistence</li><li>Role-based sharing</li></ul>"
        "<ol><li>Design</li><li>Implement</li></ol>"
    )
    save_res = client.put(
        f"/api/documents/{doc_id}",
        json={"title": "Q4 Engineering Roadmap", "content_html": rich_html},
        headers=alice_headers,
    )
    assert save_res.status_code == 200

    # 4. Reopen document and verify title and HTML formatting are intact
    reopen_res = client.get(f"/api/documents/{doc_id}", headers=alice_headers)
    assert reopen_res.status_code == 200
    reopened = reopen_res.json()
    assert reopened["title"] == "Q4 Engineering Roadmap"
    assert reopened["content_html"] == rich_html


def test_sharing_permissions_and_access_control(client: TestClient) -> None:
    """Shared users can view/edit, unshared users get 403, and non-owners cannot share."""
    users = _users_by_name(client)
    alice = users["Alice"]
    bob = users["Bob"]
    sara = users["Sara"]

    # Alice creates a private document
    create_res = client.post(
        "/api/documents",
        json={"title": "Architecture RFC", "content_html": "<p>Initial draft</p>"},
        headers=_auth_header(alice["id"]),
    )
    assert create_res.status_code == 201
    doc_id = create_res.json()["id"]

    # Before sharing, Bob cannot view or edit (403)
    assert client.get(f"/api/documents/{doc_id}", headers=_auth_header(bob["id"])).status_code == 403
    assert (
        client.put(
            f"/api/documents/{doc_id}",
            json={"content_html": "<p>Unauthorized edit</p>"},
            headers=_auth_header(bob["id"]),
        ).status_code
        == 403
    )

    # Alice shares the document with Bob
    share_res = client.post(
        f"/api/documents/{doc_id}/shares",
        json={"user_id": bob["id"]},
        headers=_auth_header(alice["id"]),
    )
    assert share_res.status_code == 201
    shares = share_res.json()
    assert len(shares) == 1
    assert shares[0]["user_id"] == bob["id"]

    # Bob sees the document under "shared_with_me" with Alice as owner
    bob_list = client.get("/api/documents", headers=_auth_header(bob["id"])).json()
    assert len(bob_list["shared_with_me"]) == 1
    assert bob_list["shared_with_me"][0]["id"] == doc_id
    assert bob_list["shared_with_me"][0]["owner_name"] == "Alice"

    # Bob can view and edit the shared document
    bob_get = client.get(f"/api/documents/{doc_id}", headers=_auth_header(bob["id"]))
    assert bob_get.status_code == 200
    bob_edit = client.put(
        f"/api/documents/{doc_id}",
        json={"content_html": "<p>Reviewed by Bob</p>"},
        headers=_auth_header(bob["id"]),
    )
    assert bob_edit.status_code == 200
    assert bob_edit.json()["content_html"] == "<p>Reviewed by Bob</p>"

    # Unshared third user (Sara) still gets 403 Forbidden
    sara_get = client.get(f"/api/documents/{doc_id}", headers=_auth_header(sara["id"]))
    assert sara_get.status_code == 403

    # Non-owner (Bob) cannot share the document with Sara (403 Forbidden)
    bob_share_attempt = client.post(
        f"/api/documents/{doc_id}/shares",
        json={"user_id": sara["id"]},
        headers=_auth_header(bob["id"]),
    )
    assert bob_share_attempt.status_code == 403

    # Owner (Alice) can revoke Bob's access, after which Bob gets 403 again
    revoke_res = client.delete(
        f"/api/documents/{doc_id}/shares/{bob['id']}",
        headers=_auth_header(alice["id"]),
    )
    assert revoke_res.status_code == 200
    assert revoke_res.json() == []
    assert client.get(f"/api/documents/{doc_id}", headers=_auth_header(bob["id"])).status_code == 403


def test_file_upload_valid_txt_md_docx_and_reject_invalid(client: TestClient) -> None:
    """Uploading .txt, .md, and .docx creates editable docs; invalid uploads return 400."""
    users = _users_by_name(client)
    bob_headers = _auth_header(users["Bob"]["id"])

    # 1. Valid .txt upload
    txt_bytes = b"First paragraph line 1\nline 2\n\nSecond paragraph"
    txt_res = client.post(
        "/api/documents/upload",
        files={"file": ("meeting-notes.txt", txt_bytes, "text/plain")},
        headers=bob_headers,
    )
    assert txt_res.status_code == 201
    txt_doc = txt_res.json()
    assert txt_doc["title"] == "meeting-notes"
    assert "<p>First paragraph line 1<br>line 2</p><p>Second paragraph</p>" == txt_doc["content_html"]

    # 2. Valid .md upload
    md_bytes = b"# Release Plan\n\nShip **v1.0** with:\n\n- FastAPI\n- SQLite\n"
    md_res = client.post(
        "/api/documents/upload",
        files={"file": ("release_plan.md", md_bytes, "text/markdown")},
        headers=bob_headers,
    )
    assert md_res.status_code == 201
    md_doc = md_res.json()
    assert md_doc["title"] == "release_plan"
    assert "<h1>Release Plan</h1>" in md_doc["content_html"]
    assert "<strong>v1.0</strong>" in md_doc["content_html"]
    assert "<ul>" in md_doc["content_html"]
    assert "<li>FastAPI</li>" in md_doc["content_html"]

    # 3. Valid .docx upload built in memory
    word_doc = docx.Document()
    word_doc.add_heading("Product Spec", level=1)
    para = word_doc.add_paragraph("Key ")
    bold_run = para.add_run("deliverables")
    bold_run.bold = True
    word_doc.add_paragraph("User switcher", style="List Bullet")
    docx_buffer = io.BytesIO()
    word_doc.save(docx_buffer)

    docx_res = client.post(
        "/api/documents/upload",
        files={
            "file": (
                "Product Spec.docx",
                docx_buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers=bob_headers,
    )
    assert docx_res.status_code == 201
    docx_payload = docx_res.json()
    assert docx_payload["title"] == "Product Spec"
    assert "<h1>Product Spec</h1>" in docx_payload["content_html"]
    assert "<strong>deliverables</strong>" in docx_payload["content_html"]
    assert "<li>User switcher</li>" in docx_payload["content_html"]

    # 4. Reject unsupported file extensions (.exe and .png) with 400
    for bad_name, mime in [("installer.exe", "application/octet-stream"), ("diagram.png", "image/png")]:
        bad_res = client.post(
            "/api/documents/upload",
            files={"file": (bad_name, b"\x89PNG\r\n\x1a\n", mime)},
            headers=bob_headers,
        )
        assert bad_res.status_code == 400
        assert "Unsupported file type" in bad_res.json()["detail"]

    # 5. Reject empty file with 400
    empty_res = client.post(
        "/api/documents/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
        headers=bob_headers,
    )
    assert empty_res.status_code == 400
    assert "empty" in empty_res.json()["detail"].lower()


def test_validation_for_missing_users_and_documents(client: TestClient) -> None:
    """Invalid user headers return 400 and unknown document IDs return 404."""
    users = _users_by_name(client)

    assert client.get("/api/documents").status_code == 400
    assert client.get("/api/documents", headers={"X-User-Id": "9999"}).status_code == 400
    assert (
        client.get("/api/documents/99999", headers=_auth_header(users["Alice"]["id"])).status_code
        == 404
    )
