"""
Carbon Emissions Reporting Platform — FastAPI Backend
Exascale Deeptech & AI Pvt. Ltd.
"""
import sqlite3, json, uuid
from datetime import datetime, date
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from pydantic import BaseModel, Field

DB_PATH = Path(__file__).parent / "emissions.db"

app = FastAPI(title="Carbon Emissions Reporting Platform", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        # Drop existing tables to start fresh
        db.executescript("""
        DROP TABLE IF EXISTS EmissionFactors;
        DROP TABLE IF EXISTS EmissionRecords;
        DROP TABLE IF EXISTS AuditLog;
        DROP TABLE IF EXISTS BusinessMetrics;
        
        CREATE TABLE EmissionFactors (
            id TEXT PRIMARY KEY,
            activity_name TEXT NOT NULL,
            activity_category TEXT NOT NULL,
            unit TEXT NOT NULL,
            co2e_per_unit REAL NOT NULL,
            scope INTEGER NOT NULL,
            source TEXT NOT NULL,
            valid_from DATE NOT NULL,
            valid_to DATE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE EmissionRecords (
            id TEXT PRIMARY KEY,
            activity_date DATE NOT NULL,
            activity_name TEXT NOT NULL,
            scope INTEGER NOT NULL,
            quantity REAL NOT NULL,
            unit TEXT NOT NULL,
            emission_factor_id TEXT NOT NULL,
            emission_factor_used REAL NOT NULL,
            calculated_co2e REAL NOT NULL,
            is_override INTEGER DEFAULT 0,
            override_value REAL,
            final_co2e REAL NOT NULL,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (emission_factor_id) REFERENCES EmissionFactors(id)
        );

        CREATE TABLE AuditLog (
            id TEXT PRIMARY KEY,
            record_id TEXT NOT NULL,
            action TEXT NOT NULL,
            old_co2e REAL,
            new_co2e REAL,
            reason TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE BusinessMetrics (
            id TEXT PRIMARY KEY,
            date DATE NOT NULL,
            metric_name TEXT NOT NULL,
            value REAL NOT NULL,
            unit TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Load seed data from JSON
        seed_file = Path(__file__).parent / "seeded_data.json"
        if not seed_file.exists():
            return
        
        with open(seed_file, "r", encoding="utf-8") as f:
            seed_data = json.load(f)
            
        factors = seed_data["factors"]
        records = seed_data["records"]
        
        # 1. Seed EmissionFactors
        # We will register both 2024 (current) and 2023 (historical versioned) factors.
        def slug(s):
            return s.lower().replace(" ", "-").replace("(", "").replace(")", "").replace("/", "-")
            
        for f in factors:
            sec = f["section"]
            mat = f["material"]
            unit = f["unit"]
            ef_val = f["ef"]
            source = f["source"]
            
            # 2024 factor (valid from 2024-01-01)
            fid_2024 = f"ef-{slug(sec)}-{slug(mat)}-2024"
            ef_val_kg_2024 = ef_val * 1000.0 # convert to kgCO2e/unit (since original was in tCO2)
            db.execute("""INSERT INTO EmissionFactors 
                (id, activity_name, activity_category, unit, co2e_per_unit, scope, source, valid_from, valid_to)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (fid_2024, mat, sec, unit, ef_val_kg_2024, 1, source, "2024-01-01", None))
                
            # 2023 factor (valid in 2023, 5% higher for historical versioning demo)
            fid_2023 = f"ef-{slug(sec)}-{slug(mat)}-2023"
            ef_val_kg_2023 = ef_val * 1000.0 * 1.05
            db.execute("""INSERT INTO EmissionFactors 
                (id, activity_name, activity_category, unit, co2e_per_unit, scope, source, valid_from, valid_to)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (fid_2023, mat, sec, unit, ef_val_kg_2023, 1, source, "2023-01-01", "2023-12-31"))

        # 2. Seed EmissionRecords
        # For 2024 Q1 and Q2, we split quarterly quantities into monthly quantities
        # For 2023 Q1, Q2, Q3, Q4, we simulate them to get a full YoY timeline.
        def lookup_factor(db, name, dt):
            return db.execute("""
                SELECT * FROM EmissionFactors
                WHERE activity_name = ? AND valid_from <= ?
                  AND (valid_to IS NULL OR valid_to >= ?)
                ORDER BY valid_from DESC LIMIT 1""",
                (name, dt, dt)).fetchone()

        records_to_insert = []

        # Scope 1 user records (split into months)
        for r in records:
            mat = r["material"]
            timeline = r["timeline"]
            qty = r["quantity"]
            
            if timeline == "Q1":
                # 2024 Q1 (Jan, Feb, Mar)
                for mo in [1, 2, 3]:
                    dt_2024 = f"2024-{mo:02d}-15"
                    records_to_insert.append((dt_2024, mat, qty / 3.0, "Q1 2024 Monthly Split"))
                # 2023 Q1 (Jan, Feb, Mar) - 95% quantity
                for mo in [1, 2, 3]:
                    dt_2023 = f"2023-{mo:02d}-15"
                    records_to_insert.append((dt_2023, mat, (qty * 0.95) / 3.0, "Q1 2023 Monthly Split"))
                # 2023 Q3 (Jul, Aug, Sep) - 96% quantity
                for mo in [7, 8, 9]:
                    dt_2023 = f"2023-{mo:02d}-15"
                    records_to_insert.append((dt_2023, mat, (qty * 0.96) / 3.0, "Q3 2023 Synthesized"))
            elif timeline == "Q2":
                # 2024 Q2 (Apr, May, Jun)
                for mo in [4, 5, 6]:
                    dt_2024 = f"2024-{mo:02d}-15"
                    records_to_insert.append((dt_2024, mat, qty / 3.0, "Q2 2024 Monthly Split"))
                # 2023 Q2 (Apr, May, Jun) - 95% quantity
                for mo in [4, 5, 6]:
                    dt_2023 = f"2023-{mo:02d}-15"
                    records_to_insert.append((dt_2023, mat, (qty * 0.95) / 3.0, "Q2 2023 Monthly Split"))
                # 2023 Q4 (Oct, Nov, Dec) - 94% quantity
                for mo in [10, 11, 12]:
                    dt_2023 = f"2023-{mo:02d}-15"
                    records_to_insert.append((dt_2023, mat, (qty * 0.94) / 3.0, "Q4 2023 Synthesized"))

        # Insert records into DB using correct historical factor
        for act_date, act_name, qty, notes in records_to_insert:
            ef = lookup_factor(db, act_name, act_date)
            if not ef:
                continue
            calc_co2e = round(qty * ef["co2e_per_unit"], 4)
            db.execute("""INSERT INTO EmissionRecords
                (id, activity_date, activity_name, scope, quantity, unit,
                 emission_factor_id, emission_factor_used, calculated_co2e, final_co2e, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), act_date, act_name, ef["scope"], round(qty, 2),
                 ef["unit"], ef["id"], ef["co2e_per_unit"], calc_co2e, calc_co2e, notes))

        # 3. Seed BusinessMetrics dynamically based on emissions
        monthly_emissions = {}
        rows = db.execute("SELECT activity_date, final_co2e FROM EmissionRecords").fetchall()
        for r in rows:
            m_str = r["activity_date"][:7] # YYYY-MM
            monthly_emissions[m_str] = monthly_emissions.get(m_str, 0.0) + r["final_co2e"]

        import random
        random.seed(42)
        
        for m_str, total_co2e in monthly_emissions.items():
            dt = f"{m_str}-28"
            prod_val = round(total_co2e / 2100.0, 2) # target intensity is ~2100 kgCO2e/tonne (2.1 tCO2/tonne)
            db.execute("INSERT INTO BusinessMetrics (id, date, metric_name, value, unit) VALUES (?, ?, ?, ?, ?)",
                       (str(uuid.uuid4()), dt, "Tonnes of Steel Produced", prod_val, "tonnes"))
            
            emp_val = random.randint(14800, 15200)
            db.execute("INSERT INTO BusinessMetrics (id, date, metric_name, value, unit) VALUES (?, ?, ?, ?, ?)",
                       (str(uuid.uuid4()), dt, "Number of Employees", float(emp_val), "persons"))


class EmissionRecordCreate(BaseModel):
    activity_date: str
    activity_name: str
    quantity: float = Field(gt=0)
    notes: Optional[str] = None


class OverrideRequest(BaseModel):
    record_id: str
    override_co2e: float = Field(gt=0)
    reason: str


class BusinessMetricCreate(BaseModel):
    date: str
    metric_name: str
    value: float
    unit: str


def get_factor_for_date(db, activity_name: str, activity_date: str):
    """Historical accuracy: pick the emission factor valid on activity_date."""
    row = db.execute("""
        SELECT * FROM EmissionFactors
        WHERE activity_name = ? AND valid_from <= ?
          AND (valid_to IS NULL OR valid_to >= ?)
        ORDER BY valid_from DESC LIMIT 1""",
        (activity_name, activity_date, activity_date)).fetchone()
    return row


@app.get("/")
def get_frontend():
    frontend_path = Path(__file__).parent.parent / "frontend" / "index.html"
    if not frontend_path.exists():
        frontend_path = Path("/app/frontend/index.html")
    if not frontend_path.exists():
        return {"error": "frontend index.html not found"}
    with open(frontend_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, status_code=200)


@app.get("/health")
def health():
    return {"status": "ok", "db": str(DB_PATH)}


@app.get("/factors")
def list_factors(scope: Optional[int] = None, active_only: bool = True):
    with get_db() as db:
        query = "SELECT * FROM EmissionFactors"
        params = []
        conditions = []
        if scope is not None:
            conditions.append("scope = ?")
            params.append(scope)
        if active_only:
            conditions.append("(valid_to IS NULL OR valid_to >= date('now'))")
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY scope, activity_category, activity_name"
        
        rows = db.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@app.post("/records", status_code=201)
def create_record(body: EmissionRecordCreate):
    with get_db() as db:
        ef = get_factor_for_date(db, body.activity_name, body.activity_date)
        if not ef:
            raise HTTPException(404, f"No emission factor found for '{body.activity_name}' on {body.activity_date}")
        calc = round(body.quantity * ef["co2e_per_unit"], 4)
        rid = str(uuid.uuid4())
        db.execute("""INSERT INTO EmissionRecords
            (id,activity_date,activity_name,scope,quantity,unit,
             emission_factor_id,emission_factor_used,calculated_co2e,final_co2e,notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, body.activity_date, body.activity_name, ef["scope"],
             body.quantity, ef["unit"], ef["id"], ef["co2e_per_unit"],
             calc, calc, body.notes))
        return {"id": rid, "calculated_co2e": calc, "unit": "kgCO2e",
                "factor_used": ef["co2e_per_unit"], "scope": ef["scope"]}


@app.post("/records/override")
def override_record(body: OverrideRequest):
    with get_db() as db:
        rec = db.execute("SELECT * FROM EmissionRecords WHERE id=?", (body.record_id,)).fetchone()
        if not rec:
            raise HTTPException(404, "Record not found")
        old = rec["final_co2e"]
        db.execute("UPDATE EmissionRecords SET is_override=1,override_value=?,final_co2e=? WHERE id=?",
                   (body.override_co2e, body.override_co2e, body.record_id))
        db.execute("INSERT INTO AuditLog (id,record_id,action,old_co2e,new_co2e,reason) VALUES (?,?,?,?,?,?)",
                   (str(uuid.uuid4()), body.record_id, "MANUAL_OVERRIDE", old, body.override_co2e, body.reason))
        return {"message": "Override applied", "old_co2e": old, "new_co2e": body.override_co2e}


@app.get("/records")
def list_records(limit: int = 50, scope: Optional[int] = None):
    with get_db() as db:
        if scope:
            rows = db.execute("SELECT * FROM EmissionRecords WHERE scope=? ORDER BY activity_date DESC LIMIT ?", (scope, limit)).fetchall()
        else:
            rows = db.execute("SELECT * FROM EmissionRecords ORDER BY activity_date DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/audit")
def get_audit():
    with get_db() as db:
        rows = db.execute("SELECT * FROM AuditLog ORDER BY timestamp DESC").fetchall()
    return [dict(r) for r in rows]


# ── Business Metrics ───────────────────────────────────────────────────────────
@app.post("/metrics", status_code=201)
def add_metric(body: BusinessMetricCreate):
    with get_db() as db:
        mid = str(uuid.uuid4())
        db.execute("INSERT INTO BusinessMetrics (id,date,metric_name,value,unit) VALUES (?,?,?,?,?)",
                   (mid, body.date, body.metric_name, body.value, body.unit))
    return {"id": mid}


@app.get("/analytics/yoy")
def yoy_emissions():
    """Year-over-Year total emissions by scope for current and previous year."""
    with get_db() as db:
        rows = db.execute("""
            SELECT strftime('%Y', activity_date) AS year,
                   scope,
                   SUM(final_co2e) AS total_co2e
            FROM EmissionRecords
            GROUP BY year, scope
            ORDER BY year, scope""").fetchall()
    result = {}
    for r in rows:
        yr = r["year"]
        if yr not in result:
            result[yr] = {"year": yr, "scope1": 0, "scope2": 0, "total": 0}
        if r["scope"] == 1:
            result[yr]["scope1"] = round(r["total_co2e"], 2)
        else:
            result[yr]["scope2"] = round(r["total_co2e"], 2)
        result[yr]["total"] = round(result[yr]["scope1"] + result[yr]["scope2"], 2)
    years = sorted(result.values(), key=lambda x: x["year"])
    for i, yr_data in enumerate(years):
        if i > 0:
            prev = years[i-1]["total"]
            curr = yr_data["total"]
            yr_data["yoy_change_pct"] = round((curr - prev) / prev * 100, 2) if prev else 0
    return {"data": years}


@app.get("/analytics/intensity")
def emission_intensity(year: int = 2024, metric_name: str = "Tonnes of Steel Produced"):
    """kgCO2e per unit of production metric."""
    with get_db() as db:
        total_co2e = db.execute("""
            SELECT SUM(final_co2e) AS total FROM EmissionRecords
            WHERE strftime('%Y', activity_date) = ?""", (str(year),)).fetchone()["total"] or 0
        total_metric = db.execute("""
            SELECT SUM(value) AS total FROM BusinessMetrics
            WHERE strftime('%Y', date) = ? AND metric_name = ?""",
            (str(year), metric_name)).fetchone()["total"] or 1
    intensity = round(total_co2e / total_metric, 4)
    return {
        "year": year,
        "metric_name": metric_name,
        "total_co2e_kg": round(total_co2e, 2),
        "total_production": round(total_metric, 2),
        "intensity_kg_per_unit": intensity,
        "label": f"kgCO₂e / {metric_name.split()[-1]}",
    }


@app.get("/analytics/hotspot")
def emission_hotspot(year: Optional[int] = None):
    """Breakdown by emission source to identify hotspots."""
    with get_db() as db:
        if year:
            rows = db.execute("""
                SELECT activity_name, scope, SUM(final_co2e) AS total_co2e, COUNT(*) AS records
                FROM EmissionRecords WHERE strftime('%Y',activity_date)=?
                GROUP BY activity_name,scope ORDER BY total_co2e DESC""", (str(year),)).fetchall()
        else:
            rows = db.execute("""
                SELECT activity_name, scope, SUM(final_co2e) AS total_co2e, COUNT(*) AS records
                FROM EmissionRecords GROUP BY activity_name,scope ORDER BY total_co2e DESC""").fetchall()
    rows = [dict(r) for r in rows]
    grand_total = sum(r["total_co2e"] for r in rows)
    for r in rows:
        r["pct"] = round(r["total_co2e"] / grand_total * 100, 2) if grand_total else 0
        r["total_co2e"] = round(r["total_co2e"], 2)
    return {"grand_total_co2e": round(grand_total, 2), "sources": rows}


@app.get("/analytics/monthly-trend")
def monthly_trend(year: int = 2024):
    """Monthly emissions trend for the given year."""
    with get_db() as db:
        rows = db.execute("""
            SELECT strftime('%Y-%m', activity_date) AS month,
                   scope, SUM(final_co2e) AS total_co2e
            FROM EmissionRecords WHERE strftime('%Y',activity_date)=?
            GROUP BY month,scope ORDER BY month""", (str(year),)).fetchall()
    result = {}
    for r in rows:
        m = r["month"]
        if m not in result:
            result[m] = {"month": m, "scope1": 0, "scope2": 0}
        if r["scope"] == 1:
            result[m]["scope1"] = round(r["total_co2e"], 2)
        else:
            result[m]["scope2"] = round(r["total_co2e"], 2)
    data = sorted(result.values(), key=lambda x: x["month"])
    for d in data:
        d["total"] = round(d["scope1"] + d["scope2"], 2)
    return {"year": year, "data": data}


@app.get("/analytics/summary")
def summary():
    """Dashboard summary — all key numbers in one call."""
    yoy = yoy_emissions()
    hotspot = emission_hotspot()
    trend = monthly_trend(2024)
    intensity = emission_intensity(2024)
    return {
        "yoy": yoy["data"],
        "hotspot": hotspot,
        "monthly_trend": trend["data"],
        "intensity": intensity,
    }


@app.on_event("startup")
def startup():
    init_db()
    print("\n" + "="*60)
    print("CARBON EMISSIONS DASHBOARD IS LIVE!")
    print("Open in your browser: http://localhost:8001/")
    print("="*60 + "\n")


if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*60)
    print("CARBON EMISSIONS DASHBOARD IS LIVE!")
    print("Open in your browser: http://localhost:8001/")
    print("="*60 + "\n")
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
