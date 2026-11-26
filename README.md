# Ajaia Docs

A lightweight, full-stack collaborative document editor inspired by Google Docs. Built with **Python (FastAPI)**, **SQLite**, and **vanilla HTML/CSS/JavaScript + Quill.js** with zero frontend build step.

**Live Demo:** [https://ajaia-docs-demo.onrender.com](https://ajaia-docs-demo.onrender.com) *(placeholder — replace with deployed Render URL)*

---

## Features

- **Instant Demo User Switcher:** Seeded on startup with **Alice** (`alice@ajaia.dev`), **Bob** (`bob@ajaia.dev`), and **Sara** (`sara@ajaia.dev`) plus an initial welcome document owned by Alice. Switch users directly from the top bar (`X-User-Id` header) to test ownership and collaboration.
- **Rich-Text Editing & Persistence:** Format documents using Quill.js (`Heading 1–3`, `Normal text`, `Bold`, `Italic`, `Underline`, `Bulleted list`, `Numbered list`), rename documents inline in the header, and persist edits to SQLite (`app.db`) with explicit **Save** (`Ctrl+S` / `Cmd+S`) and a live `Saved` / `Unsaved changes` status indicator.
- **File Import to Editable Document:** Upload `.txt`, `.md`, and `.docx` files from the sidebar (via button or drag-and-drop) to convert them into clean, sanitized rich-text HTML documents.
- **Role-Enforced Sharing:** Document owners can grant or revoke edit access for other users from the **Share** modal. The sidebar separates **My Documents** from **Shared With Me** (with an `Owner: <Name>` badge), and all access rules are strictly enforced on the backend (`403 Forbidden` for unauthorized views, edits, or share attempts).

---

## Project Structure

```text
ajaia-docs/
├── backend/
│   ├── database.py      # SQLite connection, schema init, and seeded demo users
│   ├── file_parser.py   # Converts uploaded .txt, .md, and .docx files to clean HTML
│   └── main.py          # FastAPI app, API routes, validation, and static file serving
├── frontend/
│   ├── index.html       # Main UI (header, user switcher, sidebar, Quill editor, share modal)
│   ├── styles.css       # Clean, modern Google-Docs-inspired styling
│   └── app.js           # Frontend logic connected to the FastAPI API
├── tests/
│   └── test_api.py      # Automated pytest suite using isolated temp SQLite DBs
├── requirements.txt     # Pinned Python dependencies
└── README.md            # Setup, testing, deployment, and architecture notes
```

---

## Local Setup & Running

### 1. Create and activate a virtual environment

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Start the application

```bash
uvicorn backend.main:app --reload
```

Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** in your browser. On first startup, `app.db` is automatically created and seeded with the 3 demo users and Alice's welcome document.

---

## Running Automated Tests

Run the `pytest` suite from the project root:

```bash
pytest -v
```

Each test provisions its own isolated temporary SQLite database via `AJAIA_DB_PATH`, ensuring `app.db` is never modified by test runs.

---

## Supported File Upload Types & Limits

| Extension | Input Format | Conversion Behavior |
| :--- | :--- | :--- |
| `.txt` | UTF-8 plain text | Escapes text safely and preserves paragraphs (`<p>`) and line breaks (`<br>`). |
| `.md` | Markdown | Renders headings (`<h1>`–`<h3>`), bold/italic text, and bulleted/numbered lists into sanitized HTML. |
| `.docx` | Microsoft Word (OOXML) | Extracts headings, inline bold/italic/underline runs, bulleted/numbered lists, and table rows into clean HTML. |

**Validation Rules (`400 Bad Request` on violation):**
- Supported extensions only: `.txt`, `.md`, `.docx`
- Maximum file size: **5 MB**
- Empty (`0`-byte or whitespace-only) and corrupted files are rejected with clear UI toast messages.

---

## Deploying to Render

1. Push this repository to GitHub.
2. In the [Render Dashboard](https://dashboard.render.com/), click **New +** -> **Web Service** and connect the repository.
3. Configure the service:
   - **Runtime:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
4. *(Optional for persistence across redeploys)* Attach a **Render Persistent Disk** (e.g., mounted at `/data`) and set the environment variable `AJAIA_DB_PATH=/data/app.db`. On Render's free tier without a disk, SQLite runs on the container's local filesystem and resets cleanly to the seeded demo state on redeploy.

---

## Architecture Note

### Priorities for v1
- **Clarity & Zero-Build Simplicity:** Serving vanilla HTML/CSS/JS and CDN-hosted Quill.js directly from FastAPI eliminates Node/bundler complexity while delivering a responsive, Google-Docs-style editing experience in 9 focused files.
- **Strict Backend Authorization & Sanitization:** All ownership and sharing rules (`403` for unshared users and non-owner share requests, `404` for missing resources, `400` for invalid inputs) are enforced server-side. Uploaded files and saved rich-text markup pass through an allowlist HTML sanitizer in `backend/file_parser.py` to prevent stored XSS across collaborators while keeping Quill semantic HTML clean.
- **Deterministic Testability:** Database resolution supports an `AJAIA_DB_PATH` override so `TestClient` tests execute against isolated per-test SQLite files.

### Key Technical Trade-Offs
- **Sanitized Semantic HTML vs. Delta/CRDT Storage:** Storing Quill's semantic HTML (`content_html`) makes file imports (`.txt`, `.md`, `.docx`) unify naturally into a single storage representation and keeps inspection simple, at the cost of last-write-wins concurrency when two collaborators save simultaneously.
- **Seeded Header Switcher (`X-User-Id`) vs. Full Auth:** Replacing login screens with an instant user-switcher header lets reviewers verify multi-user ownership and sharing flows in seconds, though it is intentionally not cryptographic authentication.
- **SQLite (`app.db`) vs. Client-Server RDBMS:** SQLite provides ACID transactions, foreign-key cascades, and zero infrastructure setup for single-instance deployments, trading off horizontal multi-replica write scaling.

### Logical Next Steps
1. **Real Authentication:** Replace `X-User-Id` with OAuth2 / OIDC or signed HTTP-only session cookies.
2. **Real-Time Collaborative Editing:** Pair Quill with Yjs (CRDT) over FastAPI WebSockets for live multi-cursor editing without last-write-wins overwrites.
3. **Production Database & Storage:** Migrate to PostgreSQL (with Alembic migrations) and add document version history plus read-only (`viewer`) vs. `editor` sharing roles.
