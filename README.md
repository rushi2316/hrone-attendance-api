# Employee Attendance & Analytics API

A FastAPI and MongoDB backend service for employee attendance tracking, regularization, audit logging, and departmental analytics.

## Features

- **Employees Management**: Unique client-supplied employee identity (`emp_code`), shift configurations, department filtering, and pagination.
- **Attendance Punch-In & Punch-Out**: IST calendar date resolution (including overnight shifts across midnight, R1), grace period lateness calculation (R2), overtime computation (R3), decimal half-up work hours (R4), half-day calculation (R5), and concurrency-safe duplicate prevention.
- **Manual Regularization (PATCH)**: Atomic corrections with field-level change audit trails (`history`), optimistic concurrency conflict detection, and recalculation of derived metrics.
- **Analytics Aggregations**:
  - Employee monthly summary with mid-month joiner working days (R7).
  - Department summaries counting employees with zero attendance records (R9) and record-level average work hours.
  - Late-comers leaderboard with standard competition ranking (1, 2, 2, 4) and ties.
  - Department daily trend with MongoDB gap filling (`$densify`) and 7-day moving averages.
- **Query Performance & Explainability**: Idempotent startup index creation and `/admin/explain/{endpoint}` executionStats proving index scans (`IXSCAN`) and absence of collection scans (`COLLSCAN`).

## Prerequisites

- **Python**: 3.11 or newer (developed and verified on Python 3.13)
- **MongoDB**: 6.0 or newer (uses `$densify` and `$setWindowFields`)

## Installation

1. Create and activate a Python virtual environment:
   ```bash
   python -m venv .venv
   # On Windows:
   .venv\Scripts\activate
   # On Linux / macOS:
   source .venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

Set environment variables or create a local `.env` file (actual environment variables take precedence):

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

## Seeding Sample Data

Load the sample documents provided in `sample_data/`:

```bash
python sample_seed.py
```

## Running the Application

Start the Uvicorn server from the repository root:

```bash
uvicorn app.main:app --port 8000
```

The service initializes indexes idempotently on startup and becomes ready within seconds.

## Running Tests

Execute the automated test suite with PyTest:

```bash
pytest -v
```

## Interactive API Documentation

When the service is running, explore and test the OpenAPI schema and endpoints via Swagger UI at:
- **Swagger UI**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc**: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

## Known Limitations

- Real-time aggregation pipelines on raw collections are optimized for datasets up to ~100k records. At 100x scale (10M+ records), pre-computed daily rollups and horizontal sharding on `emp_code` are recommended.
