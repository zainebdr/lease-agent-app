# Lease & Property Issue Agent

A full-stack service with two linked AI-agent features for a property
owner: one that reads a lease document into a structured, checkable record
(Part A), and one that turns uploaded photos of a property issue into a
draft work order (Part B). Both come together on a single unit view, with
human accept/reject/edit on every AI-produced output.

**Status:** Part A, Part B, the unit view joining them, and a minimal
frontend (upload lease, report issue, unit detail) are all built and
verified working end to end, backed by an automated pytest suite
(rule engine, both AI mocks, and the full review APIs).

## Architecture at a glance

**Modular monolith** — one deployable FastAPI service, internally split into
independent modules with clear boundaries:

```
backend/app/
  ai/         interfaces + mock and real implementations for lease extraction
  services/   business logic (rule engine, unit matching, orchestration)
  db/         SQLAlchemy models, session, seeding
  api/        HTTP routes only — no business logic lives here
  schemas/    Pydantic request/response shapes
```

**Why one deployable service instead of separate services per feature:**
this project will have two AI features sharing the same core resource — a
unit — with the main product value being both shown together on one
screen. Splitting into separate services this early would mean a network
call and two data stores just to render one page, for no actual scaling
benefit yet: no independent load pattern, team split, or deploy cadence
difference exists to justify it. So this stays one service, with internal
module boundaries (`ai/`, `services/`, `db/`, `api/`) drawn where I'd cut
if that ever changed — e.g. Part B's photo analysis, once added, lands in
its own `services/issue_reporting.py` + `ai/*image*` pair, already
isolated enough to extract into its own service later if it needed
independent (e.g. GPU-backed) scaling.

## How to run it

Requires Python 3.11+. No database, API key, or external service needs to
be installed or running — everything (SQLite, the AI mock) is self
contained. These steps are the same on every OS except two commands
(creating/activating the virtual environment), called out below.

### 1. Backend API

**macOS / Linux (bash/zsh):**
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**Windows (PowerShell):**
```powershell
cd backend
py -m venv venv
.\venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

> If PowerShell blocks the activation script (`running scripts is
> disabled on this system`), run
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first, then
> re-run `.\venv\Scripts\Activate.ps1`.
>
> If `uvicorn` isn't recognized, or you see `Fatal error in launcher`
> (this happens if the project folder was moved/renamed after the venv
> was created — the venv's launcher scripts hardcode an absolute path),
> run it as a module instead, which sidesteps the broken launcher:
> `python -m uvicorn app.main:app --reload` (`py -m uvicorn ...` on
> Windows).

Either way, this starts the API at **http://localhost:8000** and, on
first run, creates and seeds `app.db` (SQLite) from `data/units.json`.

Confirm it's up:
- Health check: http://localhost:8000/health → `{"status":"ok"}`
- Interactive API docs: http://localhost:8000/docs

Try it end to end with the sample lease that ships in `data/`:

```bash
curl -X POST http://localhost:8000/leases/upload -F "file=@data/sample_lease.txt"
curl http://localhost:8000/units/MC-B-1204
```

> **PowerShell users:** `curl` there is an alias for `Invoke-WebRequest`,
> which doesn't understand `-F`. Either call the real curl binary
> explicitly (`curl.exe -X POST ... -F "file=@data/sample_lease.txt"`) or
> use `Invoke-RestMethod` instead.

### 2. Frontend

A single dependency-free HTML file — no npm, no build step, no install.
With the backend running (step 1), just open `frontend/index.html`
directly in a browser (double-click it, or File → Open). It talks to the
API at `http://localhost:8000` by default; change the `API_BASE` constant
near the top of `index.html` if your backend is running somewhere else.

If your browser refuses to let a `file://` page call `http://localhost`
(some browsers/extensions are strict about this), serve the folder
instead of opening the file directly:

```bash
cd frontend
python3 -m http.server 5500   # py -m http.server 5500 on Windows
```

then visit http://localhost:5500.

### 3. Tests

```bash
cd backend
pip install -r requirements-dev.txt   # py -m pip install -r requirements-dev.txt on Windows
pytest
```

Runs against an isolated in-memory SQLite database created fresh per
test, and always against the deterministic mock AI implementations —
no API key, no network access, and no effect whatsoever on your real
`app.db`.

### AI: mock vs. real

I built lease extraction behind an interface (`Protocol` in
`app/ai/base.py`) with two implementations, rather than wiring straight to
one model call:

- **Mock (default, always available)** — a deterministic, rule-based
  extractor that looks for common lease phrasing (labelled lines, date
  formats, currency amounts). No dependency on any external service, no
  cost, no latency, runs the same way every time.
- **Real (used automatically if configured)** — a genuine Claude call,
  prompted to return strict JSON with a source quote and confidence per
  field.

  ```bash
  pip install anthropic
  export ANTHROPIC_API_KEY=sk-ant-...
  ```

  No other code changes needed — `app/ai/factory.py` is the single place
  that decides which implementation to hand to the rest of the app, based
  on whether a key is present. Part B's photo assessment
  (`app/ai/mock_image_assessor.py` / `llm_image_assessor.py`) follows the
  exact same pattern, behind its own `ImageAssessor` interface.

**Why bother with both, instead of just calling a real model directly:**
the actual "agent" behavior this product needs — flagging missing or
contradictory data, validating against the ruleset, matching to a unit,
routing everything through human accept/reject/edit — is logic that has
nothing to do with which model produced the raw extraction. Isolating the
raw extraction step behind an interface means I could build and fully
exercise that validation/review pipeline (the part with the actual
business value) without depending on an external service being up,
costing money per test run, or being non-deterministic while I'm
iterating. The real implementation exists and is a genuine model call,
not a placeholder — it's just not the only path, and switching between
them is a one-line env var, not a rewrite.

## Main decisions

- **SQLite + SQLAlchemy** for persistence. Zero setup for a take-home
  reviewer to run, but the ORM means moving to Postgres later is a
  one-line connection-string change (`app/config.py`) plus running
  migrations — no model or query code changes. I've skipped Alembic
  migrations in this build to keep it a zero-setup clone-and-run for a
  reviewer; a real deployment would want them from day one.
- **Provenance and review state live in the schema, not bolted on.**
  `Lease.extracted_fields` stores each field's value, source span, and
  confidence; `Lease.review_status` tracks accepted/rejected/edited per
  field. This is what makes the output "traceable and overridable"
  rather than a black-box JSON blob.
- **The unit is the join key.** `GET /units/{unit_id}` already returns
  the unit with its lease(s) attached — designed so Part B's work orders
  slot into the same response without changing this shape, giving the
  "owner sees a unit's lease alongside the issues raised against it" view
  the brief asks for.
- **Missing data is never guessed.** Every rule check and every extracted
  field either has a value or is explicitly absent — the rule engine
  reports `NOT_DETERMINABLE` rather than defaulting to a PASS or FAIL when
  a needed field wasn't found.
- **Lease documents are accepted as plain text** in this build, not PDF —
  extraction/validation logic is the interesting problem here, and PDF
  parsing is a separable, well-understood addition (e.g. via `pypdf`)
  that would sit in `api/leases.py` without touching anything downstream
  of it.

## Repo layout

```
backend/
  app/                application code (ai/, services/, db/, api/, schemas/ — see above)
  data/               owner_ruleset.json, units.json, sample_lease.txt
  tests/              pytest suite (rule engine, both AI mocks, lease + issue review APIs)
  uploads/            issue photos land here at runtime, served at /uploads/<name>
  requirements.txt    runtime dependencies
  requirements-dev.txt   + pytest/httpx, for `pytest` only
  pytest.ini
  .env.example
frontend/
  index.html          the entire frontend — no build step
```
