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

### 4. Migrations (only if you already have an `app.db`)

Schema changes are managed with [Alembic](https://alembic.sqlalchemy.org/)
(`backend/alembic/`), not by hand-editing an existing database.

**Brand-new setup, no `app.db` yet:** nothing to do — step 1 above
(`uvicorn app.main:app --reload`) creates `app.db` with the full current
schema on first run, same as always. Optionally tell Alembic your fresh
database is already fully up to date, so a later `alembic upgrade head`
is a no-op instead of erroring on tables that already exist:

```bash
cd backend
alembic stamp head
```

**Pulled a newer version of this repo and already have an `app.db`
from an older commit:** run `alembic upgrade head` to apply whatever
columns changed since. If that errors because your `app.db` predates
Alembic entirely, easiest fix is just deleting `app.db` and letting
step 1 recreate it fresh.

### AI: mock vs. real, and picking a provider

I built lease extraction and photo assessment behind interfaces
(`Protocol`s in `app/ai/base.py`) with three implementations each, rather
than wiring straight to one model call:

- **Mock (default, always available)** — for lease extraction, a
  deterministic, rule-based extractor that looks for common lease
  phrasing (labelled lines, date formats, currency amounts) — genuine,
  if simple, rule-based work. For photo assessment, a deterministic
  hash-based stand-in with a fixed set of scenarios — see
  `app/ai/mock_image_assessor.py`'s own docstring for why that one is
  honestly just a shape-demo, not a rules engine — there's no rule-based
  equivalent for "judge this photo's condition" the way there is for
  "find the rent amount in this text." No dependency on any external
  service, no cost, no latency, runs the same way every time.
- **Real (used automatically if a key is configured)** — a genuine
  model call, prompted to return strict JSON with a source quote and
  confidence per field (lease extraction) or condition/contents/damage
  notes/confidence (photo assessment). Two providers are implemented
  behind the exact same interface, so **whichever key you already
  have works, with zero code changes**:

  ```bash
  # Anthropic (Claude) - used if ANTHROPIC_API_KEY is set
  pip install anthropic
  export ANTHROPIC_API_KEY=sk-ant-...

  # OR OpenAI (GPT-4o) - used if OPENAI_API_KEY is set instead
  pip install openai
  export OPENAI_API_KEY=sk-...
  ```

  You only ever need one of these two — `app/config.py` resolves
  `AI_PROVIDER` from whichever key is present (Anthropic takes
  precedence if both happen to be set), and `app/ai/factory.py` is the
  single place that hands the right concrete implementation to the rest
  of the app based on that. No other code changes needed either way.
  `app/ai/anthropic_lease_extractor.py` / `anthropic_image_assessor.py`
  are the Anthropic implementations; `app/ai/openai_lease_extractor.py` /
  `openai_image_assessor.py` are the OpenAI ones. Both providers are
  handed the *exact same prompts* (the OpenAI files import
  `ASSESSMENT_PROMPT`/`EXTRACTION_PROMPT` from the Anthropic files
  rather than duplicating them) and validate the parsed response against
  the *exact same* `app/ai/schemas.py` Pydantic models — so which
  provider answers only changes the wire format of the API call itself,
  never the question asked or the shape enforced on the answer.

**Why bother with mock + two real providers, instead of just calling one
model directly:** the actual "agent" behavior this product needs —
flagging missing or contradictory data, validating against the ruleset,
matching to a unit, routing everything through human accept/reject/edit —
is logic that has nothing to do with which model (or vendor) produced the
raw extraction. Isolating that step behind an interface meant I could
build and fully exercise that validation/review pipeline (the part with
the actual business value) without depending on an external service
being up, costing money per test run, or being non-deterministic while
iterating. Supporting two real providers behind the same interface, on
top of that, means whoever runs this doesn't need *my* API key or *my*
choice of vendor — they use whichever one they already have. Both real
implementations are genuine model calls, not placeholders — they're just
not the only path, and switching between mock / Anthropic / OpenAI is
one environment variable, never a rewrite.

## Main decisions

- **SQLite + SQLAlchemy** for persistence, with **Alembic** managing
  schema changes (`backend/alembic/`, see "Migrations" above). The ORM
  means moving to Postgres later is a one-line connection-string change
  (`app/config.py`) — no model or query code changes — and Alembic
  means a schema change (like the one that added `reviewed_by` and the
  photo integrity columns) reaches an existing `app.db` through a
  reviewed migration instead of silently not happening: a bare
  `Base.metadata.create_all()` only creates tables that don't exist yet,
  so it does nothing for a new column on a table that's already there.
- **Renewal and termination terms are extracted fields too** —
  `renewal_terms_text` / `termination_terms_text`, same treatment as
  rent or the escalation clause: a source span, reviewable, editable,
  left blank rather than guessed if the lease doesn't spell them out.
- **Provenance and review state live in the schema, not bolted on.**
  `Lease.extracted_fields` stores each field's value, source span, and
  confidence; `Lease.review_status` tracks accepted/rejected/edited per
  field; `Lease.reviewed_by`/`decision_at` and
  `WorkOrder.reviewed_by`/`decision_at` record who made the accept/
  reject call and when; and `RuleCheck` rows are never deleted, only
  superseded (`is_current`/`superseded_at`) — see
  `app/services/lease_extraction.py:refresh_rule_checks` — so a field
  edit's before/after effect on a rule result stays in the record
  instead of being overwritten. This is what makes the output
  "traceable and overridable" rather than a black-box JSON blob.
- **A unit can have at most one accepted lease.** Enforced at the DB
  layer with a partial unique index on `leases.unit_id` scoped to
  `status = 'accepted'` (`app/db/models.py:Lease.__table_args__`), not
  just application logic — a concurrent request can't double-book a
  unit even if two review calls race.
- **An uploaded issue photo is validated, hashed, and checked for
  duplicates before anything else happens to it.**
  `app/api/issues.py:_validate_photo_upload` rejects (400) anything over
  10MB or that Pillow can't actually decode as an image — a client's
  `Content-Type` header is never trusted on its own, since a renamed
  `.txt` file can claim to be `image/jpeg` just as easily as a real
  photo can. Past that gate, `IssuePhoto.sha256`/`size_bytes`/
  `content_type` are recorded for every upload, and the file itself is
  stored under its hash (`app/services/issue_reporting.py:_save_photo`)
  rather than a request-derived name, so an identical photo uploaded
  twice — same issue or a different one — is written to disk exactly
  once. That re-upload is also flagged, not just deduped silently:
  `IssuePhoto.duplicate_of_id` points at the original row (shown in the
  frontend as a "Duplicate of #N" badge), always resolving to the
  earliest upload with that hash even through a chain of re-uploads.
  **Known simplification:** this is fully automatic — a duplicate is
  stored and assessed exactly like any other photo, just flagged, never
  blocked or confirmed. That's deliberate for now, since the same real
  photo can honestly apply to more than one issue (e.g. the same visible
  defect reported against two separate issues) and rejecting it outright
  would be wrong. A reasonable future enhancement would be to surface
  the duplicate at upload time and let the reporter confirm ("this is
  the same photo as issue #4's — attach anyway?") rather than deciding
  silently either way.
- **A real model's JSON response is validated for shape, not just
  parsed.** Both `app/ai/anthropic_lease_extractor.py` and
  `app/ai/anthropic_image_assessor.py` (and their OpenAI counterparts)
  already treated a response that isn't even valid JSON as "no usable
  result" rather than crashing — what they didn't handle was valid JSON
  in the *wrong* shape, e.g. `"contents": "AC unit"` instead of
  `["AC unit"]`. Plain Python's `list("AC unit")` wouldn't raise there —
  it would silently iterate the string into `["A", "C", " ", "u", ...]`.
  All four implementations now validate the
  parsed JSON against a shared Pydantic schema
  (`app/ai/schemas.py:PhotoAssessmentPayload` /
  `ExtractedFieldPayload`) and treat a validation failure exactly like a
  parse failure — same fallback, not a crash and not silently-wrong
  data. (Fixed the same pass: the real lease extractor's prompt never
  asked for `unit_reference_text`, so unlike the mock extractor it could
  never actually match a lease to a unit — see
  `app/services/unit_matching.py`. Its prompt and field allowlist now
  include it.)
- **One photo's AI assessment failing doesn't fail the whole upload.**
  `app/services/issue_reporting.py:_assess_photo` catches any exception
  from the assessor per photo (a provider timeout, or a response the
  schema above rejects) and records a clearly-labelled placeholder
  (`IssuePhoto.processing_status = "failed"`) instead of losing every
  other photo's real assessment — and the whole issue/work order along
  with them — over one bad call. The real exception is logged
  server-side, never surfaced to the caller. `IssuePhoto.original_filename`
  is also now recorded (display only — storage stays content-addressed,
  see above — so a human can tell photos apart by the name they
  uploaded them under).
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
  alembic/            migration environment + versions/ (see "Migrations" above)
  alembic.ini
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
