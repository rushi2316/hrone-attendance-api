"""
Employee Attendance & Analytics API

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for development; env vars take precedence)
"""
import calendar
import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional

from bson import json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, status as http_status
from pydantic import BaseModel, Field, field_validator, model_validator
import pymongo
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv()

# Connect to MongoDB with timezone awareness
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

client = MongoClient(MONGO_URI, tz_aware=True, serverSelectionTimeoutMS=2000)
db = client[MONGO_DB]

IST = timezone(timedelta(hours=5, minutes=30))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure all required indexes exist at startup (idempotent)
    db.employees.create_index("emp_code", unique=True)
    db.employees.create_index([("department", 1), ("joined_on", 1)])
    db.employees.create_index([("joined_on", 1)])

    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("punch_in", -1)])
    db.attendance_logs.create_index([("date", 1), ("late_minutes", 1)])
    yield


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def to_epoch_ms(val: datetime | None) -> int | None:
    if val is None:
        return None
    if val.tzinfo is None:
        val = val.replace(tzinfo=timezone.utc)
    return int(val.timestamp() * 1000)


def from_epoch_ms(ms: int) -> datetime:
    # R1: Truncate instants to whole seconds
    trunc_ms = (ms // 1000) * 1000
    return datetime.fromtimestamp(trunc_ms / 1000, tz=timezone.utc)


def current_truncated_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=0)


def get_shift_times(shift_start: str, shift_end: str):
    h_s, m_s = map(int, shift_start.split(":"))
    h_e, m_e = map(int, shift_end.split(":"))
    is_overnight = (h_e, m_e) <= (h_s, m_s)
    return (h_s, m_s), (h_e, m_e), is_overnight


def get_attendance_date(punch_in_dt_utc: datetime, shift_start: str, shift_end: str) -> str:
    """R1: IST calendar date of punch_in. Overnight shifts before shift_end belong to previous day."""
    dt_ist = punch_in_dt_utc.astimezone(IST)
    _, (h_e, m_e), is_overnight = get_shift_times(shift_start, shift_end)
    if is_overnight:
        if (dt_ist.hour, dt_ist.minute, dt_ist.second) < (h_e, m_e, 0):
            return (dt_ist.date() - timedelta(days=1)).isoformat()
    return dt_ist.date().isoformat()


def compute_late_minutes(punch_in_dt_utc: datetime, shift_start: str, record_date: str) -> int:
    """R2: Grace period 10 minutes. Late only if strictly > 10:00 after shift_start."""
    h_s, m_s = map(int, shift_start.split(":"))
    d = date.fromisoformat(record_date)
    start_ist = datetime(d.year, d.month, d.day, h_s, m_s, 0, tzinfo=IST)
    punch_in_ist = punch_in_dt_utc.astimezone(IST)
    diff_sec = (punch_in_ist - start_ist).total_seconds()
    if diff_sec > 600:
        return int(diff_sec // 60)
    return 0


def compute_overtime(punch_out_dt_utc: datetime, shift_start: str, shift_end: str, record_date: str) -> int:
    """R3: Whole minutes from shift_end to punch_out, counted only if >= 30; otherwise 0."""
    (h_s, m_s), (h_e, m_e), is_overnight = get_shift_times(shift_start, shift_end)
    d = date.fromisoformat(record_date)
    if is_overnight:
        end_d = d + timedelta(days=1)
    else:
        end_d = d
    end_ist = datetime(end_d.year, end_d.month, end_d.day, h_e, m_e, 0, tzinfo=IST)
    punch_out_ist = punch_out_dt_utc.astimezone(IST)
    diff_sec = (punch_out_ist - end_ist).total_seconds()
    if diff_sec >= 1800:
        return int(diff_sec // 60)
    return 0


def compute_work_hours(punch_in_dt_utc: datetime, punch_out_dt_utc: datetime) -> float:
    """R4: (punch_out - punch_in) in seconds / 3600, rounded to 2 decimals half-up."""
    diff_sec = (punch_out_dt_utc - punch_in_dt_utc).total_seconds()
    val = Decimal(str(int(diff_sec))) / Decimal("3600")
    return float(val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def is_half_day(work_hours: float | None) -> bool:
    """R5: half_day is true when rounded work_hours < 4.50."""
    if work_hours is None:
        return False
    return work_hours < 4.50


def round_half_up(val: float | Decimal | None, decimals: int) -> float | None:
    """R8: Decimal half-up rounding."""
    if val is None:
        return None
    d = Decimal(str(val))
    fmt = Decimal("10") ** -decimals
    return float(d.quantize(fmt, rounding=ROUND_HALF_UP))


def format_employee_doc(doc: dict) -> dict:
    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end": doc["shift_end"],
        "joined_on": doc["joined_on"],
        "created_at": to_epoch_ms(doc.get("created_at")),
    }


def format_attendance_doc(doc: dict) -> dict:
    history_api = []
    for h in doc.get("history", []):
        changes_api = {}
        for field, ch in h.get("changes", {}).items():
            from_val = ch.get("from")
            to_val = ch.get("to")
            if field in ("punch_in", "punch_out"):
                if isinstance(from_val, datetime):
                    from_val = to_epoch_ms(from_val)
                if isinstance(to_val, datetime):
                    to_val = to_epoch_ms(to_val)
            changes_api[field] = {"from": from_val, "to": to_val}
        history_api.append({
            "at": to_epoch_ms(h.get("at")),
            "by": h.get("by"),
            "reason": h.get("reason"),
            "changes": changes_api,
        })

    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": to_epoch_ms(doc.get("punch_in")),
        "punch_out": to_epoch_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": history_api,
    }


# --------------------------------------------------------------------------- #
# Request and Enum Models
# --------------------------------------------------------------------------- #
class Status(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LEAVE = "LEAVE"
    WFH = "WFH"
    ON_DUTY = "ON_DUTY"


class PresenceStatus(str, Enum):
    PRESENT = "PRESENT"
    WFH = "WFH"
    ON_DUTY = "ON_DUTY"


class EmployeeCreate(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @model_validator(mode="after")
    def validate_fields(self):
        try:
            date.fromisoformat(self.joined_on)
        except Exception:
            raise ValueError("joined_on must be a valid YYYY-MM-DD date")
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


def validate_epoch_millis(v, field_name: str, allow_null: bool = False):
    if v is None and allow_null:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"{field_name} must be an integer count of epoch milliseconds")
    if v < 100000000000 or v > 4102444800000:
        raise ValueError(f"{field_name} out of range [100000000000, 4102444800000]")
    return v


class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: PresenceStatus = PresenceStatus.PRESENT

    @field_validator("punched_at", mode="before")
    @classmethod
    def check_punched_at(cls, v):
        return validate_epoch_millis(v, "punched_at", allow_null=True)


class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

    @field_validator("punched_at", mode="before")
    @classmethod
    def check_punched_at(cls, v):
        return validate_epoch_millis(v, "punched_at", allow_null=True)


class RegularizeRequest(BaseModel):
    status: Optional[Status] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("punch_in", mode="before")
    @classmethod
    def check_punch_in(cls, v):
        return validate_epoch_millis(v, "punch_in", allow_null=True)

    @field_validator("punch_out", mode="before")
    @classmethod
    def check_punch_out(cls, v):
        return validate_epoch_millis(v, "punch_out", allow_null=True)


class ExplainEndpoint(str, Enum):
    attendance_list = "attendance_list"
    employee_monthly = "employee_monthly"
    department_summary = "department_summary"
    late_leaderboard = "late_leaderboard"
    department_trend = "department_trend"


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health", tags=["system"])
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")


@app.post("/employees", status_code=http_status.HTTP_201_CREATED, tags=["employees"])
def create_employee(body: EmployeeCreate):
    created_at = current_truncated_utc()
    doc = {
        "emp_code": body.emp_code,
        "name": body.name,
        "email": body.email,
        "department": body.department,
        "shift_start": body.shift_start,
        "shift_end": body.shift_end,
        "joined_on": body.joined_on,
        "created_at": created_at,
    }
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="Employee with this emp_code already exists")

    return format_employee_doc(doc)


@app.get("/employees", tags=["employees"])
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    q = {}
    if department is not None:
        q["department"] = department

    total = db.employees.count_documents(q)
    skip = (page - 1) * page_size
    cursor = db.employees.find(q, {"_id": 0}).sort("emp_code", 1).skip(skip).limit(page_size)
    items = [format_employee_doc(doc) for doc in cursor]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=http_status.HTTP_201_CREATED, tags=["attendance"])
def punch_in(body: PunchInRequest):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if body.punched_at is not None:
        punch_in_dt = from_epoch_ms(body.punched_at)
    else:
        punch_in_dt = current_truncated_utc()

    att_date = get_attendance_date(punch_in_dt, emp["shift_start"], emp["shift_end"])
    late_mins = compute_late_minutes(punch_in_dt, emp["shift_start"], att_date)

    doc = {
        "emp_code": body.emp_code,
        "date": att_date,
        "status": body.status.value,
        "punch_in": punch_in_dt,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_mins,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="Already punched in for this date")

    return format_attendance_doc(doc)


@app.post("/attendance/punch-out", status_code=http_status.HTTP_200_OK, tags=["attendance"])
def punch_out(body: PunchOutRequest):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Employee not found")

    if body.punched_at is not None:
        punch_out_dt = from_epoch_ms(body.punched_at)
    else:
        punch_out_dt = current_truncated_utc()

    # Find the employee's most recent record whose punch_in is at or before punched_at
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": punch_out_dt}},
        sort=[("punch_in", -1)],
    )
    if not record:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="No eligible punch-in record found")

    punch_in_dt = record["punch_in"]
    if punch_out_dt <= punch_in_dt:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Punch-out must be strictly after punch-in")
    if (punch_out_dt - punch_in_dt).total_seconds() > 24 * 3600:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Punch-out cannot be more than 24 hours after punch-in")

    if record.get("punch_out") is not None:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="Record is already punched out")

    work_hours = compute_work_hours(punch_in_dt, punch_out_dt)
    half_day = is_half_day(work_hours)
    overtime_mins = compute_overtime(punch_out_dt, emp["shift_start"], emp["shift_end"], record["date"])

    # Atomic update to prevent race conditions on concurrent punch-out
    res = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": punch_out_dt,
                "work_hours": work_hours,
                "half_day": half_day,
                "overtime_minutes": overtime_mins,
            }
        },
        return_document=pymongo.ReturnDocument.AFTER,
    )
    if res is None:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="Record is already punched out")

    return format_attendance_doc(res)


@app.get("/attendance", tags=["attendance"])
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[Status] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail="date_from cannot be after date_to")

    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if status:
        q["status"] = status.value
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to

    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    cursor = (
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )
    items = [format_attendance_doc(doc) for doc in cursor]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{date}", tags=["attendance"])
def regularize_attendance(emp_code: str, date: str, body: RegularizeRequest):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Employee not found")

    rec = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not rec:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Attendance record not found")

    cur_status = rec["status"]
    cur_punch_in = rec.get("punch_in")
    cur_punch_out = rec.get("punch_out")
    cur_work_hours = rec.get("work_hours")
    cur_late_minutes = rec.get("late_minutes", 0)
    cur_overtime_minutes = rec.get("overtime_minutes", 0)
    cur_half_day = rec.get("half_day", False)

    target_status = body.status.value if body.status is not None else cur_status

    if target_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Punch times cannot be supplied when status is ABSENT or LEAVE",
            )
        target_punch_in = None
        target_punch_out = None
        target_work_hours = None
        target_late_minutes = 0
        target_overtime_minutes = 0
        target_half_day = False
    else:
        # Presence status
        if body.punch_in is not None:
            target_punch_in = from_epoch_ms(body.punch_in)
        else:
            target_punch_in = cur_punch_in

        if target_punch_in is None:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="punch_in is required for presence status",
            )

        calc_date = get_attendance_date(target_punch_in, emp["shift_start"], emp["shift_end"])
        if calc_date != date:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="punch_in does not belong to the record's attendance date",
            )

        if body.punch_out is not None:
            target_punch_out = from_epoch_ms(body.punch_out)
        else:
            target_punch_out = cur_punch_out

        if target_punch_out is not None:
            if target_punch_out <= target_punch_in:
                raise HTTPException(
                    status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="punch_out must be after punch_in",
                )
            if (target_punch_out - target_punch_in).total_seconds() > 24 * 3600:
                raise HTTPException(
                    status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="punch_out cannot be more than 24 hours after punch_in",
                )
            target_work_hours = compute_work_hours(target_punch_in, target_punch_out)
            target_half_day = is_half_day(target_work_hours)
            target_overtime_minutes = compute_overtime(
                target_punch_out, emp["shift_start"], emp["shift_end"], date
            )
        else:
            target_work_hours = None
            target_half_day = False
            target_overtime_minutes = 0

        target_late_minutes = compute_late_minutes(target_punch_in, emp["shift_start"], date)

    # Detect fields that actually changed
    changes_db = {}
    if target_status != cur_status:
        changes_db["status"] = {"from": cur_status, "to": target_status}
    if target_punch_in != cur_punch_in:
        changes_db["punch_in"] = {"from": cur_punch_in, "to": target_punch_in}
    if target_punch_out != cur_punch_out:
        changes_db["punch_out"] = {"from": cur_punch_out, "to": target_punch_out}
    if target_work_hours != cur_work_hours:
        changes_db["work_hours"] = {"from": cur_work_hours, "to": target_work_hours}
    if target_late_minutes != cur_late_minutes:
        changes_db["late_minutes"] = {"from": cur_late_minutes, "to": target_late_minutes}
    if target_overtime_minutes != cur_overtime_minutes:
        changes_db["overtime_minutes"] = {"from": cur_overtime_minutes, "to": target_overtime_minutes}
    if target_half_day != cur_half_day:
        changes_db["half_day"] = {"from": cur_half_day, "to": target_half_day}

    if len(changes_db) == 0:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Correction made no actual change",
        )

    now_utc = current_truncated_utc()
    history_entry = {
        "at": now_utc,
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes_db,
    }

    # Optimistic concurrency check: match existing history list
    res = db.attendance_logs.find_one_and_update(
        {"_id": rec["_id"], "history": rec.get("history", [])},
        {
            "$set": {
                "status": target_status,
                "punch_in": target_punch_in,
                "punch_out": target_punch_out,
                "work_hours": target_work_hours,
                "late_minutes": target_late_minutes,
                "overtime_minutes": target_overtime_minutes,
                "half_day": target_half_day,
            },
            "$push": {"history": history_entry},
        },
        return_document=pymongo.ReturnDocument.AFTER,
    )
    if res is None:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="Concurrent edit conflict")

    return format_attendance_doc(res)


# --------------------------------------------------------------------------- #
# Analytics Endpoints
# --------------------------------------------------------------------------- #
def get_employee_monthly_pipeline(emp_code: str, month: str):
    import calendar
    y, m = map(int, month.split("-"))
    last_day = calendar.monthrange(y, m)[1]
    month_start = f"{month}-01"
    month_end = f"{month}-{last_day:02d}"

    return [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": month_start, "$lte": month_end},
            }
        },
        {
            "$project": {
                "dayOfWeek": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                "status": 1,
                "half_day": {"$ifNull": ["$half_day", False]},
                "late_minutes": {"$ifNull": ["$late_minutes", 0]},
                "overtime_minutes": {"$ifNull": ["$overtime_minutes", 0]},
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$dayOfWeek", [2, 3, 4, 5, 6]]},
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                ]
                            },
                            {"$cond": ["$half_day", 0.5, 1.0]},
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, "$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]


@app.get("/analytics/employees/{emp_code}/monthly", tags=["analytics"])
def employee_monthly(emp_code: str, month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Employee not found")

    y, m = map(int, month.split("-"))
    last_day_num = calendar.monthrange(y, m)[1]
    first_day = date(y, m, 1)
    last_day = date(y, m, last_day_num)
    joined_date = date.fromisoformat(emp["joined_on"])
    eff_start = max(first_day, joined_date)

    if eff_start > last_day:
        working_days = 0
    else:
        working_days = sum(
            1 for d in range((last_day - eff_start).days + 1)
            if (eff_start + timedelta(days=d)).weekday() < 5
        )

    pipeline = get_employee_monthly_pipeline(emp_code, month)
    agg_res = list(db.attendance_logs.aggregate(pipeline))
    stat_doc = agg_res[0] if agg_res else {}

    present_days = float(stat_doc.get("present_days", 0.0))
    leave_days = int(stat_doc.get("leave_days", 0))
    late_count = int(stat_doc.get("late_count", 0))
    total_late_minutes = int(stat_doc.get("total_late_minutes", 0))
    total_overtime_minutes = int(stat_doc.get("total_overtime_minutes", 0))

    if working_days == 0:
        attendance_pct = None
    else:
        val = Decimal(str(present_days)) / Decimal(str(working_days)) * Decimal("100")
        attendance_pct = float(val.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }


def get_department_summary_pipeline(month: str, department: Optional[str] = None):
    y, m = map(int, month.split("-"))
    last_day_num = calendar.monthrange(y, m)[1]
    month_start = f"{month}-01"
    month_end = f"{month}-{last_day_num:02d}"

    match_emp = {"joined_on": {"$lte": month_end}}
    if department:
        match_emp["department"] = department

    return [
        {"$match": match_emp},
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "emp_codes": {"$push": "$emp_code"},
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"emps": "$emp_codes"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$in": ["$emp_code", "$$emps"]},
                                    {"$gte": ["$date", month_start]},
                                    {"$lte": ["$date", month_end]},
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "dayOfWeek": {
                                "$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}
                            },
                            "status": 1,
                            "half_day": {"$ifNull": ["$half_day", False]},
                            "work_hours": 1,
                            "late_minutes": {"$ifNull": ["$late_minutes", 0]},
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "present_days": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$in": ["$dayOfWeek", [2, 3, 4, 5, 6]]},
                                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                            ]
                                        },
                                        {"$cond": ["$half_day", 0.5, 1.0]},
                                        0,
                                    ]
                                }
                            },
                            "work_hours_sum": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                                {"$ne": ["$work_hours", None]},
                                            ]
                                        },
                                        "$work_hours",
                                        0,
                                    ]
                                }
                            },
                            "work_hours_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                                {"$ne": ["$work_hours", None]},
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                            },
                            "total_late_minutes": {
                                "$sum": {
                                    "$cond": [{"$gt": ["$late_minutes", 0]}, "$late_minutes", 0]
                                }
                            },
                            "leave_count": {
                                "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                            },
                            "on_duty_count": {
                                "$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}
                            },
                        }
                    },
                ],
                "as": "stats",
            }
        },
        {
            "$project": {
                "department": "$_id",
                "_id": 0,
                "headcount": 1,
                "stat": {"$arrayElemAt": ["$stats", 0]},
            }
        },
        {"$sort": {"department": 1}},
    ]


@app.get("/analytics/departments/summary", tags=["analytics"])
def department_summary(
    month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    pipeline = get_department_summary_pipeline(month, department)
    results = list(db.employees.aggregate(pipeline))
    items = []
    for r in results:
        stat = r.get("stat") or {}
        wh_count = stat.get("work_hours_count", 0)
        avg_wh = None
        if wh_count > 0:
            val = Decimal(str(stat.get("work_hours_sum", 0))) / Decimal(str(wh_count))
            avg_wh = float(val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        items.append({
            "department": r["department"],
            "headcount": int(r["headcount"]),
            "present_days": float(stat.get("present_days", 0.0)),
            "avg_work_hours": avg_wh,
            "late_count": int(stat.get("late_count", 0)),
            "total_late_minutes": int(stat.get("total_late_minutes", 0)),
            "leave_count": int(stat.get("leave_count", 0)),
            "on_duty_count": int(stat.get("on_duty_count", 0)),
        })

    return {"month": month, "items": items}


def get_late_leaderboard_pipeline(month: str, limit: int = 10, department: Optional[str] = None):
    y, m = map(int, month.split("-"))
    last_day_num = calendar.monthrange(y, m)[1]
    month_start = f"{month}-01"
    month_end = f"{month}-{last_day_num:02d}"

    pipeline = [
        {
            "$match": {
                "date": {"$gte": month_start, "$lte": month_end},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp",
            }
        },
        {"$unwind": "$emp"},
    ]

    if department:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}},
                },
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$emp.name",
                "department": "$emp.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ])
    return pipeline


@app.get("/analytics/leaderboard/late", tags=["analytics"])
def late_leaderboard(
    month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    pipeline = get_late_leaderboard_pipeline(month, limit, department)
    items = list(db.attendance_logs.aggregate(pipeline))
    return {"month": month, "items": items}


def get_department_trend_pipeline(department: str, from_date: str, to_date: str):
    d_start = datetime.fromisoformat(f"{from_date}T00:00:00Z")
    to_dt = datetime.fromisoformat(f"{to_date}T00:00:00Z")
    d_end = to_dt + timedelta(days=1)

    return [
        {"$match": {"department": department}},
        {
            "$group": {
                "_id": "$department",
                "emps": {"$push": {"emp_code": "$emp_code", "joined_on": "$joined_on"}},
            }
        },
        {"$addFields": {"d": d_start}},
        {
            "$densify": {
                "field": "d",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [d_start, d_end],
                },
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"d": 1},
                "output": {
                    "emps": {
                        "$first": "$emps",
                        "window": {"documents": ["unbounded", "current"]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$d"}},
                "dayOfWeek": {"$dayOfWeek": "$d"},
                "emps": 1,
            }
        },
        {
            "$project": {
                "date": 1,
                "is_working_day": {"$in": ["$dayOfWeek", [2, 3, 4, 5, 6]]},
                "headcount": {
                    "$size": {
                        "$filter": {
                            "input": "$emps",
                            "as": "e",
                            "cond": {"$lte": ["$$e.joined_on", "$date"]},
                        }
                    }
                },
                "emp_list": {
                    "$map": {
                        "input": "$emps",
                        "as": "e",
                        "in": "$$e.emp_code",
                    }
                },
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"cur_date": "$date", "emp_list": "$emp_list"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$date", "$$cur_date"]},
                                    {"$in": ["$emp_code", "$$emp_list"]},
                                ]
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "present_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                        {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1.0]},
                                        0,
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]
                                }
                            },
                        }
                    },
                ],
                "as": "day_stats",
            }
        },
        {
            "$addFields": {
                "stat": {"$arrayElemAt": ["$day_stats", 0]},
            }
        },
        {
            "$project": {
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": {"$ifNull": ["$stat.present_count", 0.0]},
                "late_count": {"$ifNull": ["$stat.late_count", 0]},
                "attendance_rate": {
                    "$cond": [
                        {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                        {"$divide": [{"$ifNull": ["$stat.present_count", 0.0]}, "$headcount"]},
                        None,
                    ]
                },
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, "current"]},
                    }
                },
            }
        },
        {"$sort": {"date": 1}},
    ]


@app.get("/analytics/departments/{department}/trend", tags=["analytics"])
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    try:
        d_from = date.fromisoformat(from_date)
        d_to = date.fromisoformat(to_date)
    except Exception:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from and to must be valid YYYY-MM-DD dates",
        )

    if d_to < d_from:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="to date cannot be earlier than from date",
        )

    if (d_to - d_from).days + 1 > 92:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Date range cannot exceed 92 days",
        )

    # 404 if department has no employees
    if not db.employees.find_one({"department": department}):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Department not found")

    pipeline = get_department_trend_pipeline(department, from_date, to_date)
    raw_items = list(db.employees.aggregate(pipeline))
    items = []
    for r in raw_items:
        items.append({
            "date": r["date"],
            "is_working_day": bool(r["is_working_day"]),
            "headcount": int(r["headcount"]),
            "present_count": float(r["present_count"]),
            "late_count": int(r["late_count"]),
            "attendance_rate": round_half_up(r.get("attendance_rate"), 4),
            "moving_avg_7d": round_half_up(r.get("moving_avg_7d"), 4),
        })

    return {"department": department, "items": items}


# --------------------------------------------------------------------------- #
# Admin Explain Endpoint
# --------------------------------------------------------------------------- #
@app.get("/admin/explain/{endpoint}", tags=["admin"])
def explain_endpoint(
    endpoint: ExplainEndpoint,
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status_filter: Optional[Status] = Query(default=None, alias="status"),
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if endpoint == ExplainEndpoint.attendance_list:
        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="date_from cannot be after date_to",
            )
        q = {}
        if emp_code:
            q["emp_code"] = emp_code
        if status_filter:
            q["status"] = status_filter.value
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to

        skip = (page - 1) * page_size
        command_spec = {
            "explain": {
                "find": "attendance_logs",
                "filter": q,
                "sort": {"date": -1, "emp_code": 1},
                "skip": skip,
                "limit": page_size,
            },
            "verbosity": "executionStats",
        }
        res = db.command(command_spec)
        clean = json.loads(json_util.dumps(res))
        return {
            "endpoint": endpoint.value,
            "collection": "attendance_logs",
            "explain": clean,
        }

    elif endpoint == ExplainEndpoint.employee_monthly:
        if not emp_code or not month:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="emp_code and month are required",
            )
        pipeline = get_employee_monthly_pipeline(emp_code, month)
        command_spec = {
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        }
        res = db.command(command_spec)
        clean = json.loads(json_util.dumps(res))
        return {
            "endpoint": endpoint.value,
            "collection": "attendance_logs",
            "explain": clean,
        }

    elif endpoint == ExplainEndpoint.department_summary:
        if not month:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="month is required",
            )
        pipeline = get_department_summary_pipeline(month, department)
        command_spec = {
            "explain": {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        }
        res = db.command(command_spec)
        clean = json.loads(json_util.dumps(res))
        return {
            "endpoint": endpoint.value,
            "collection": "employees",
            "explain": clean,
        }

    elif endpoint == ExplainEndpoint.late_leaderboard:
        if not month:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="month is required",
            )
        pipeline = get_late_leaderboard_pipeline(month, limit, department)
        command_spec = {
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        }
        res = db.command(command_spec)
        clean = json.loads(json_util.dumps(res))
        return {
            "endpoint": endpoint.value,
            "collection": "attendance_logs",
            "explain": clean,
        }

    elif endpoint == ExplainEndpoint.department_trend:
        if not department or not from_date or not to_date:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="department, from, and to are required",
            )
        try:
            d_from = date.fromisoformat(from_date)
            d_to = date.fromisoformat(to_date)
        except Exception:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="from and to must be valid YYYY-MM-DD dates",
            )
        if d_to < d_from:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="to date cannot be earlier than from date",
            )
        if (d_to - d_from).days + 1 > 92:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Date range cannot exceed 92 days",
            )

        pipeline = get_department_trend_pipeline(department, from_date, to_date)
        command_spec = {
            "explain": {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        }
        res = db.command(command_spec)
        clean = json.loads(json_util.dumps(res))
        return {
            "endpoint": endpoint.value,
            "collection": "employees",
            "explain": clean,
        }
