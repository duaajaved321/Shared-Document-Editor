"""FastAPI application, REST endpoints, authorization checks, and static frontend hosting."""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.database import db_session, init_db, utc_now_iso
from backend.file_parser import MAX_FILE_SIZE_BYTES, parse_uploaded_file, sanitize_html

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
MAX_TITLE_LENGTH = 200


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Initialize SQLite schema and seed demo users/welcome doc on server startup."""
    init_db()
    yield


app = FastAPI(
    title="Ajaia Docs API",
    description="Lightweight collaborative document editor powered by FastAPI, SQLite, and Quill.js.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class UserOut(BaseModel):
    id: int
    name: str
    email: str


class ShareOut(BaseModel):
    id: int
    user_id: int
    name: str
    email: str
    created_at: str


class DocumentSummaryOut(BaseModel):
    id: int
    title: str
    owner_id: int
    owner_name: str
    owner_email: str
    is_owner: bool
    shared_count: int
    created_at: str
    updated_at: str


class DocumentListOut(BaseModel):
    owned: list[DocumentSummaryOut]
    shared_with_me: list[DocumentSummaryOut]


class DocumentDetailOut(BaseModel):
    id: int
    title: str
    content_html: str
    owner_id: int
    owner_name: str
    owner_email: str
    is_owner: bool
    shares: list[ShareOut]
    created_at: str
    updated_at: str


class DocumentCreateIn(BaseModel):
    title: str = Field(default="Untitled Document")
    content_html: str = Field(default="")


class DocumentUpdateIn(BaseModel):
    title: str | None = None
    content_html: str | None = None


class ShareCreateIn(BaseModel):
    user_id: int


# ---------------------------------------------------------------------------
# Dependencies & Helpers
# ---------------------------------------------------------------------------


def get_db() -> sqlite3.Connection:
    """FastAPI dependency yielding a SQLite connection per request."""
    with db_session() as conn:
        yield conn


def get_current_user(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    conn: sqlite3.Connection = Depends(get_db),
) -> sqlite3.Row:
    """Validate the X-User-Id header and return the matching user record."""
    if x_user_id is None or not x_user_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required X-User-Id header.",
        )
    try:
        parsed_id = int(x_user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-User-Id header must be a valid integer user ID.",
        ) from exc

    user = conn.execute(
        "SELECT id, name, email FROM users WHERE id = ?",
        (parsed_id,),
    ).fetchone()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User with ID {parsed_id} does not exist.",
        )
    return user


def _validate_title(raw_title: str) -> str:
    cleaned = raw_title.strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document title cannot be empty.",
        )
    if len(cleaned) > MAX_TITLE_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Document title cannot exceed {MAX_TITLE_LENGTH} characters.",
        )
    return cleaned


def _fetch_document_or_404(conn: sqlite3.Connection, doc_id: int) -> sqlite3.Row:
    doc = conn.execute(
        """
        SELECT d.id, d.title, d.content_html, d.owner_id, d.created_at, d.updated_at,
               u.name AS owner_name, u.email AS owner_email
        FROM documents d
        JOIN users u ON u.id = d.owner_id
        WHERE d.id = ?
        """,
        (doc_id,),
    ).fetchone()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )
    return doc


def _user_can_access_document(conn: sqlite3.Connection, doc: sqlite3.Row, user_id: int) -> bool:
    if doc["owner_id"] == user_id:
        return True
    share = conn.execute(
        "SELECT 1 FROM document_shares WHERE document_id = ? AND user_id = ?",
        (doc["id"], user_id),
    ).fetchone()
    return share is not None


def _fetch_shares(conn: sqlite3.Connection, doc_id: int) -> list[ShareOut]:
    rows = conn.execute(
        """
        SELECT ds.id, ds.user_id, u.name, u.email, ds.created_at
        FROM document_shares ds
        JOIN users u ON u.id = ds.user_id
        WHERE ds.document_id = ?
        ORDER BY u.name ASC
        """,
        (doc_id,),
    ).fetchall()
    return [ShareOut(**dict(row)) for row in rows]


def _serialize_document_detail(
    conn: sqlite3.Connection,
    doc: sqlite3.Row,
    current_user_id: int,
) -> DocumentDetailOut:
    return DocumentDetailOut(
        id=doc["id"],
        title=doc["title"],
        content_html=doc["content_html"],
        owner_id=doc["owner_id"],
        owner_name=doc["owner_name"],
        owner_email=doc["owner_email"],
        is_owner=(doc["owner_id"] == current_user_id),
        shares=_fetch_shares(conn, doc["id"]),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------


@app.get("/api/users", response_model=list[UserOut])
def list_users(conn: sqlite3.Connection = Depends(get_db)) -> list[UserOut]:
    """Return all demo users for the header switcher and share modal."""
    rows = conn.execute("SELECT id, name, email FROM users ORDER BY id ASC").fetchall()
    return [UserOut(**dict(row)) for row in rows]


@app.get("/api/documents", response_model=DocumentListOut)
def list_documents(
    current_user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> DocumentListOut:
    """List documents owned by the active user and documents shared with them."""
    user_id = current_user["id"]

    owned_rows = conn.execute(
        """
        SELECT d.id, d.title, d.owner_id, u.name AS owner_name, u.email AS owner_email,
               d.created_at, d.updated_at,
               (SELECT COUNT(*) FROM document_shares ds WHERE ds.document_id = d.id) AS shared_count
        FROM documents d
        JOIN users u ON u.id = d.owner_id
        WHERE d.owner_id = ?
        ORDER BY d.updated_at DESC, d.id DESC
        """,
        (user_id,),
    ).fetchall()

    shared_rows = conn.execute(
        """
        SELECT d.id, d.title, d.owner_id, u.name AS owner_name, u.email AS owner_email,
               d.created_at, d.updated_at,
               (SELECT COUNT(*) FROM document_shares ds2 WHERE ds2.document_id = d.id) AS shared_count
        FROM document_shares ds
        JOIN documents d ON d.id = ds.document_id
        JOIN users u ON u.id = d.owner_id
        WHERE ds.user_id = ?
        ORDER BY d.updated_at DESC, d.id DESC
        """,
        (user_id,),
    ).fetchall()

    owned = [
        DocumentSummaryOut(**dict(row), is_owner=True)
        for row in owned_rows
    ]
    shared_with_me = [
        DocumentSummaryOut(**dict(row), is_owner=False)
        for row in shared_rows
    ]
    return DocumentListOut(owned=owned, shared_with_me=shared_with_me)


@app.post("/api/documents", response_model=DocumentDetailOut, status_code=status.HTTP_201_CREATED)
def create_document(
    payload: DocumentCreateIn,
    current_user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> DocumentDetailOut:
    """Create a new rich-text document owned by the active user."""
    title = _validate_title(payload.title)
    cleaned_html = sanitize_html(payload.content_html)
    now = utc_now_iso()

    cursor = conn.execute(
        """
        INSERT INTO documents (title, content_html, owner_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (title, cleaned_html, current_user["id"], now, now),
    )
    doc = _fetch_document_or_404(conn, int(cursor.lastrowid))
    return _serialize_document_detail(conn, doc, current_user["id"])


@app.post(
    "/api/documents/upload",
    response_model=DocumentDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    file: UploadFile = File(...),
    current_user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> DocumentDetailOut:
    """Parse an uploaded .txt, .md, or .docx file and persist it as a new document."""
    raw_bytes = await file.read(MAX_FILE_SIZE_BYTES + 1)
    try:
        title, content_html = parse_uploaded_file(file.filename, raw_bytes)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    title = title[:MAX_TITLE_LENGTH]
    now = utc_now_iso()
    cursor = conn.execute(
        """
        INSERT INTO documents (title, content_html, owner_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (title, content_html, current_user["id"], now, now),
    )
    doc = _fetch_document_or_404(conn, int(cursor.lastrowid))
    return _serialize_document_detail(conn, doc, current_user["id"])


@app.get("/api/documents/{doc_id}", response_model=DocumentDetailOut)
def get_document(
    doc_id: int,
    current_user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> DocumentDetailOut:
    """Retrieve a document if owned by or shared with the active user."""
    doc = _fetch_document_or_404(conn, doc_id)
    if not _user_can_access_document(conn, doc, current_user["id"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this document.",
        )
    return _serialize_document_detail(conn, doc, current_user["id"])


@app.put("/api/documents/{doc_id}", response_model=DocumentDetailOut)
def update_document(
    doc_id: int,
    payload: DocumentUpdateIn,
    current_user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> DocumentDetailOut:
    """Update a document's title and/or HTML content if the user has access."""
    doc = _fetch_document_or_404(conn, doc_id)
    if not _user_can_access_document(conn, doc, current_user["id"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to edit this document.",
        )

    if payload.title is None and payload.content_html is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one field to update (title or content_html).",
        )

    new_title = _validate_title(payload.title) if payload.title is not None else doc["title"]
    new_html = (
        sanitize_html(payload.content_html)
        if payload.content_html is not None
        else doc["content_html"]
    )
    now = utc_now_iso()

    conn.execute(
        """
        UPDATE documents
        SET title = ?, content_html = ?, updated_at = ?
        WHERE id = ?
        """,
        (new_title, new_html, now, doc_id),
    )
    updated_doc = _fetch_document_or_404(conn, doc_id)
    return _serialize_document_detail(conn, updated_doc, current_user["id"])


@app.delete("/api/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    doc_id: int,
    current_user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    """Delete a document (restricted to the document owner)."""
    doc = _fetch_document_or_404(conn, doc_id)
    if doc["owner_id"] != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the document owner can delete this document.",
        )
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


@app.get("/api/documents/{doc_id}/shares", response_model=list[ShareOut])
def list_document_shares(
    doc_id: int,
    current_user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ShareOut]:
    """List collaborators who have access to a document."""
    doc = _fetch_document_or_404(conn, doc_id)
    if not _user_can_access_document(conn, doc, current_user["id"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view shares for this document.",
        )
    return _fetch_shares(conn, doc_id)


@app.post(
    "/api/documents/{doc_id}/shares",
    response_model=list[ShareOut],
    status_code=status.HTTP_201_CREATED,
)
def share_document(
    doc_id: int,
    payload: ShareCreateIn,
    current_user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ShareOut]:
    """Grant another user access to a document (owner only)."""
    doc = _fetch_document_or_404(conn, doc_id)
    if doc["owner_id"] != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the document owner can share this document.",
        )

    if payload.user_id == current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You already own this document.",
        )

    target_user = conn.execute(
        "SELECT id, name, email FROM users WHERE id = ?",
        (payload.user_id,),
    ).fetchone()
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Target user with ID {payload.user_id} does not exist.",
        )

    try:
        conn.execute(
            """
            INSERT INTO document_shares (document_id, user_id, created_at)
            VALUES (?, ?, ?)
            """,
            (doc_id, payload.user_id, utc_now_iso()),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Document is already shared with {target_user['name']}.",
        ) from exc

    return _fetch_shares(conn, doc_id)


@app.delete("/api/documents/{doc_id}/shares/{target_user_id}", response_model=list[ShareOut])
def revoke_document_share(
    doc_id: int,
    target_user_id: int,
    current_user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ShareOut]:
    """Revoke a collaborator's access to a document (owner only)."""
    doc = _fetch_document_or_404(conn, doc_id)
    if doc["owner_id"] != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the document owner can modify sharing permissions.",
        )

    cursor = conn.execute(
        "DELETE FROM document_shares WHERE document_id = ? AND user_id = ?",
        (doc_id, target_user_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share record not found for this user.",
        )

    return _fetch_shares(conn, doc_id)


# ---------------------------------------------------------------------------
# Static Frontend Serving
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def serve_index() -> FileResponse:
    """Serve the single-page frontend entry point."""
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")
