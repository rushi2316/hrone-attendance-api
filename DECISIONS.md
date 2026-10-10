# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** On `employees`: unique `emp_code` (uniqueness and lookups); `(department, joined_on)` and `(joined_on)` (department filtering and headcount scoping). On `attendance_logs`: unique `(emp_code, date)` (single daily record guarantee and monthly summary); `(date: -1, emp_code: 1)` (attendance listing and sort); `(date, late_minutes)` (leaderboard); `(emp_code, punch_in: -1)` (punch-out lookup). Rejected an index on `status` alone due to low cardinality and poor query selectivity.

2. **Punch-in race.** Both concurrent requests compute the target record and attempt `db.attendance_logs.insert_one()`. MongoDB WiredTiger enforces the unique `(emp_code, date)` index at storage level. Exactly one insert commits (201); the second raises `DuplicateKeyError`, which our exception handler intercepts to return HTTP 409 without check-then-write races.

3. **Ties.** We use `$setWindowFields` with `$rank: {}` for standard competition ranking (1, 2, 2, 4) and filter `rank <= limit` after ranking. Tied employees share the rank and are both returned (exceeding limit if tied at the cutoff), sorted by `total_late_minutes` descending then `emp_code` ascending.

4. **Headcount.** The aggregation pipeline starts on `employees` (matching `joined_on <= month_end`) and groups by department to establish headcount before joining `attendance_logs` via `$lookup`. Employees with zero logs still count toward department headcount.

5. **One thing you would change for 100x data.** Shard `attendance_logs` by `emp_code` and maintain incremental daily/monthly rollups on write events to avoid real-time cross-collection aggregation over tens of millions of raw documents.
