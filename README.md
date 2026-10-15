# HROne Employee Attendance & Analytics API

A production-quality REST API built with **FastAPI** and **MongoDB** for employee attendance tracking, shift management, audit-trail regularization, and departmental analytics.

> Submitted as part of the **HROne Software Engineer Trainee** take-home assignment.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ (developed on 3.13) |
| Framework | FastAPI |
| Database | MongoDB 6.0+ |
| Driver | PyMongo (sync, `tz_aware=True`) |
| Testing | Pytest + FastAPI TestClient |
| Server | Uvicorn |

---

## Features

### Employee Management
- Client-supplied `emp_code` as the unique identity (no MongoDB `_id` exposed)
- Shift configuration with overnight shift support (`shift_end < shift_start`)
- Department filtering, `emp_code`-sorted pagination, filtered totals

### Attendance Punch-In / Punch-Out
- **R1** — IST calendar date resolution including overnight shifts (punch-in before shift-end maps to the previous calendar day)
- **R2** — Grace period lateness: >10 minutes late = `late_minutes` recorded
- **R3** — Work hours with `Decimal` + `ROUND_HALF_UP` (never banker's rounding)
- **R4** — Overtime: >9 hours worked AND >30 min over shift end
- **R5** — Half-day: <4.5 hours worked
- Concurrency-safe duplicate prevention via unique compound index `(emp_code, date)` + `DuplicateKeyError` handling

### Manual Regularization (`PATCH`)
- Field-level correction with full audit trail (`history[]`)
- Automatic recomputation of `late_minutes`, `work_hours`, `overtime_minutes`, `half_day`
- Optimistic concurrency control (OCC) — concurrent edits return `409` instead of silently overwriting
- History entries record `at`, `by`, `reason`, and only the fields that actually changed

### Analytics (all via MongoDB aggregation pipelines)
| Endpoint | Description |
|---|---|
| `GET /analytics/employees/{emp_code}/monthly` | Working days (Mon–Fri from `joined_on`), present days (0.5 for half-days), leave count, late count, avg work hours, attendance % |
| `GET /analytics/departments/summary` | Headcount including zero-attendance employees, avg work hours, total present/leave/late |
| `GET /analytics/leaderboard/late` | Standard competition ranking (1,2,2,4) with ties at cutoff preserved |
| `GET /analytics/departments/{dept}/trend` | Day-by-day trend with `$densify` gap filling + 7-day moving average |

### Query Performance
- 6 indexes created idempotently at startup (unique, compound, range-query)
- `/admin/explain/{endpoint}` returns `executionStats` proving `IXSCAN` (no `COLLSCAN`) for all 5 critical queries
- Database-side pagination everywhere — no full collection reads into Python

---

## Project Structure

```
hrone-attendance-api/
├── app/
│   └── main.py              # All application code (single-file per assignment requirement)
├── tests/
│   ├── conftest.py          # Pytest fixtures (isolated test DB, seeding, index setup)
│   ├── test_system.py       # Health endpoint
│   ├── test_employees.py    # Employee CRUD, concurrency, pagination
│   ├── test_punch_in.py     # R1/R2 business rules, overnight shifts
│   ├── test_punch_out.py    # R3–R5 calculations, concurrent punch-outs
│   ├── test_attendance_list.py
│   ├── test_regularize.py   # History audit, OCC conflict detection
│   ├── test_analytics.py    # Monthly summary, dept summary, leaderboard, trend
│   ├── test_explain.py      # IXSCAN verification
│   └── test_edge_cases.py   # Half-up rounding, overnight overtime, zero working days
├── sample_data/
│   ├── employees.json
│   └── attendance_logs.json
├── sample_seed.py           # Seed script for sample data
├── openapi.yaml             # OpenAPI 3.0 specification (authoritative contract)
├── DATA_MODEL.md            # MongoDB collection schemas
├── REVIEW.md                # Part A: 18 verified defects found and fixed
├── DECISIONS.md             # Architectural decision record (5 key decisions)
├── requirements.txt
└── README.md
```

---

## Setup & Installation

### Prerequisites
- Python 3.11 or newer
- MongoDB 6.0 or newer running locally (or accessible via URI)

### 1. Clone the repository
```bash
git clone <repo-url>
cd hrone-attendance-api
```

### 2. Create and activate a virtual environment
```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root (never committed):
```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Actual environment variables always take precedence over `.env`.

### 5. Seed sample data (optional)
```bash
python sample_seed.py
```
Loads 6 employees across Engineering, Sales, and Support departments with 9 attendance records (including overnight shift and manual correction history).

---

## Running the Application

```bash
uvicorn app.main:app --port 8000
```

The application:
- Reads `MONGO_URI` and `MONGO_DB` from environment on startup
- Creates all 6 required MongoDB indexes **idempotently**
- Becomes ready in under 2 seconds

---

## API Documentation

With the server running, open:

| URL | Description |
|---|---|
| http://localhost:8000/docs | **Swagger UI** — interactive API explorer |
| http://localhost:8000/redoc | ReDoc — clean reference documentation |
| http://localhost:8000/health | Health probe (pings MongoDB, returns 503 if down) |

---

## Running Tests

```bash
pytest -v
```

- **55 tests** across 9 test files
- Tests run against an isolated `attendance_test_db` (dropped on teardown)
- Covers: all business rules R1–R10, concurrent race conditions, pagination, audit history, analytics edge cases, and index scan verification

---

## Key Design Decisions

See [`DECISIONS.md`](DECISIONS.md) for full reasoning. Brief summary:

| Decision | Choice | Reason |
|---|---|---|
| Sync vs async driver | PyMongo (sync) | Assignment is single-process; simpler, no event loop complexity |
| Timestamp format | Epoch milliseconds | Timezone-unambiguous; matches OpenAPI schema `EpochMillis` |
| Concurrency for duplicates | Unique index + catch `DuplicateKeyError` | Atomic at DB level; no check-then-insert race |
| Concurrency for regularize | OCC via history array match | Detects mid-flight edits without locks |
| Analytics source | Read stored derived fields | Consistent with point-in-time corrections; avoids recomputing on raw punches |

---

## Defects Fixed (Part A)

See [`REVIEW.md`](REVIEW.md) for the complete list. 18 verified defects were found and corrected in the starter code, including:

- `/health` returned static `ok` without pinging MongoDB
- Pagination skip formula was `page × size` instead of `(page-1) × size`
- `total` count was unfiltered (ignored department filter)
- Banker's rounding used for `work_hours` instead of half-up
- Overnight shift date calculated in local time instead of IST
- MongoDB `_id` exposed as `"id"` in API responses
- Full collection loaded into Python for in-memory pagination
- Grace period boundary used `> 10` instead of `> 600 seconds`

---

## Business Rules Summary

| Rule | Description |
|---|---|
| R1 | Attendance date in IST; punch-in before `shift_end` on overnight shifts → previous calendar day |
| R2 | Late if punch-in > 10 min after shift start; `late_minutes` = rounded difference |
| R3 | `work_hours` = (punch_out − punch_in) in hours, rounded half-up to 2 decimal places |
| R4 | Overtime if `work_hours > 9` AND punch-out > 30 min past shift end |
| R5 | Half-day if `work_hours < 4.5` |
| R6 | Punch-out must be after punch-in and within 24 hours |
| R7 | Working days = Mon–Fri count from max(`joined_on`, period start) to period end |
| R8 | Analytics read stored `work_hours`, `late_minutes`, etc. — not recomputed from raw punches |
| R9 | Department headcount includes employees with zero attendance records |
| R10 | Leaderboard uses standard competition ranking (1,2,2,4); ties at cutoff are preserved |
