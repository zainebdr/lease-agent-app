# Lease & Property Issue Agent

A small full-stack service with two linked AI-agent features for a property
owner: one that reads a lease document into a structured, checkable record
(Part A — built here), and one that turns uploaded photos of a property
issue into a draft work order (Part B — coming next). Both will come
together on a single unit view.

> Staged build: this commit is Part A only (lease record agent) — verified
> working end to end. Part B (photo-based issue reporting) and the unit
> view that brings both together land in a following commit, once Part A
> is solid. Frontend and the "what's next" sections come after that.

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

**macOS / Linux:**
```bash
cd backend
python3 -m venv venv && source venv/bin/activate
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

The app creates and seeds `app.db` (SQLite) automatically on first startup
from `data/units.json`. No API key or external service is required — see
"AI: mock vs. real" below. A ready-to-use sample lease is provided at
`data/sample_lease.txt`.

Try it immediately:

```bash
curl -X POST http://localhost:8000/leases/upload -F "file=@data/sample_lease.txt"
curl http://localhost:8000/units/MC-B-1204
```

API docs (interactive): `http://localhost:8000/docs`

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
  on whether a key is present. Part B will follow the same pattern with
  its own `ImageAssessor` interface once it's added.

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
  migrations in this build: the schema is still settling while Part B
  lands, so migration files would just be noise right now — worth adding
  once the shape stabilizes.
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

## Coming next

- [ ] Part B — photo-based issue reporting, drafted work orders
- [ ] Unit view bringing lease + work orders together
- [ ] Frontend (upload lease, upload issue, unit detail screens)


## Repo layout

```
backend/
  app/            application code (see above)
  data/           owner_ruleset.json, units.json, sample_lease.txt
  requirements.txt
  .env.example
```
