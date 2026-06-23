import os, json, pickle, logging
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="APU Power Demand Forecasting API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_PATH = Path(__file__).parent / "model_artifact.pkl"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
DHANBAD_LAT, DHANBAD_LON = 23.7957, 86.4304

# ── Dhanbad-specific holiday list ──────────────────────────────────────────────
DHANBAD_HOLIDAYS = {
    "2024-01-14": "Makar Sankranti / Tusu Puja",
    "2024-01-26": "Republic Day",
    "2024-03-25": "Holi",
    "2024-04-09": "Ram Navami",
    "2024-04-14": "Ambedkar Jayanti",
    "2024-04-17": "Good Friday",
    "2024-05-23": "Buddha Purnima",
    "2024-07-17": "Muharram",
    "2024-08-15": "Independence Day",
    "2024-08-26": "Janmashtami",
    "2024-09-07": "Karma Puja (Jharkhand)",
    "2024-10-02": "Gandhi Jayanti",
    "2024-10-12": "Dussehra",
    "2024-10-31": "Kali Puja / Diwali",
    "2024-11-15": "Jharkhand Foundation Day",
    "2024-11-26": "Sarhul (Jharkhand Tribal)",
    "2024-12-25": "Christmas",
    "2025-01-14": "Makar Sankranti",
    "2025-01-26": "Republic Day",
    "2025-03-14": "Holi",
    "2025-08-15": "Independence Day",
    "2025-10-02": "Gandhi Jayanti",
    "2025-11-15": "Jharkhand Foundation Day",
}

COAL_SHUTDOWN_DAYS = {
    "2024-05-01": "Labour Day (BCCL/ECL mine shutdown)",
    "2024-11-01": "BCCL Annual Maintenance Day",
}

def get_holidays_for_range(start_dt: datetime, end_dt: datetime) -> list:
    result = []
    all_holidays = {**DHANBAD_HOLIDAYS, **COAL_SHUTDOWN_DAYS}
    for date_str, name in all_holidays.items():
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if start_dt.date() <= dt.date() <= end_dt.date():
            result.append({"date": date_str, "name": name,
                           "type": "industrial" if date_str in COAL_SHUTDOWN_DAYS else "festive"})
    return sorted(result, key=lambda x: x["date"])


def fetch_weather(lat: float, lon: float, days: int = 2) -> dict:
    """Fetch weather from Open-Meteo (free, no key needed)."""
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,relative_humidity_2m,cloud_cover,wind_speed_10m",
        "forecast_days": days,
        "timezone": "Asia/Kolkata",
    }
    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=2)
        resp.raise_for_status()
        data = resp.json()
        return {
            "times": data["hourly"]["time"],
            "temperature": data["hourly"]["temperature_2m"],
            "humidity": data["hourly"]["relative_humidity_2m"],
            "cloud_cover": data["hourly"]["cloud_cover"],
            "wind_speed": data["hourly"]["wind_speed_10m"],
        }
    except Exception as e:
        logger.warning(f"Weather API failed: {e}, using synthetic data")
        now = datetime.now()
        times = [(now + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(48)]
        hour_arr = np.array([int(t[11:13]) for t in times])
        return {
            "times": times,
            "temperature": list(np.round(28 + 6 * np.sin(np.pi * (hour_arr - 6) / 12) + np.random.normal(0, 1, 48), 1)),
            "humidity": list(np.round(65 + 10 * np.cos(np.pi * hour_arr / 12) + np.random.normal(0, 3, 48), 1)),
            "cloud_cover": list(np.round(np.clip(40 + np.random.normal(0, 20, 48), 0, 100), 1)),
            "wind_speed": list(np.round(np.clip(8 + np.random.normal(0, 2, 48), 0, 30), 1)),
        }


def generate_forecast_internal() -> dict:
    """Generate a 144-block (10-min) 24-hour forecast."""
    if not MODEL_PATH.exists():
        raise HTTPException(status_code=503, detail="Model artifact not found. Run train_model.py first.")

    with open(MODEL_PATH, "rb") as f:
        artifact = pickle.load(f)

    model = artifact["model"]
    feature_names = artifact["feature_names"]
    scaler = artifact.get("scaler")
    meta = artifact.get("meta", {})

    weather = fetch_weather(DHANBAD_LAT, DHANBAD_LON, days=2)
    now = datetime.now()
    blocks = pd.date_range(start=now.replace(minute=0, second=0, microsecond=0),
                           periods=144, freq="10min")

    rows = []
    for i, ts in enumerate(blocks):
        hour_idx = i // 6  # which weather hour
        hour_idx = min(hour_idx, len(weather["temperature"]) - 1)
        rows.append({
            "hour": ts.hour,
            "minute": ts.minute,
            "day_of_week": ts.dayofweek,
            "month": ts.month,
            "day_of_year": ts.day_of_year,
            "is_weekend": int(ts.dayofweek >= 5),
            "block_of_day": i % 144,
            "temperature": weather["temperature"][hour_idx],
            "humidity": weather["humidity"][hour_idx],
            "cloud_cover": weather["cloud_cover"][hour_idx],
            "wind_speed": weather["wind_speed"][hour_idx],
            "is_holiday": int(ts.strftime("%Y-%m-%d") in {**DHANBAD_HOLIDAYS, **COAL_SHUTDOWN_DAYS}),
            "hour_sin": np.sin(2 * np.pi * ts.hour / 24),
            "hour_cos": np.cos(2 * np.pi * ts.hour / 24),
            "dow_sin": np.sin(2 * np.pi * ts.dayofweek / 7),
            "dow_cos": np.cos(2 * np.pi * ts.dayofweek / 7),
            "month_sin": np.sin(2 * np.pi * ts.month / 12),
            "month_cos": np.cos(2 * np.pi * ts.month / 12),
        })

    feat_df = pd.DataFrame(rows)[feature_names]
    if scaler:
        feat_arr = scaler.transform(feat_df)
    else:
        feat_arr = feat_df.values

    preds = model.predict(feat_arr)

    return {
        "generated_at": now.isoformat(),
        "location": "Dhanbad, Jharkhand, India",
        "blocks": [
            {
                "timestamp": ts.isoformat(),
                "block_index": idx,
                "forecast_mw": round(float(preds[idx]), 2),
            }
            for idx, ts in enumerate(blocks)
        ],
    }


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    index_path = Path(__file__).parent.parent / "frontend" / "index.html"
    if not index_path.exists():
        index_path = Path(__file__).parent / "frontend" / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"service": "APU Power Forecasting API", "version": "1.0.0",
            "endpoints": ["/forecast", "/weather", "/holidays", "/health"]}


# Mount frontend folder if it exists
frontend_path = Path(__file__).parent.parent / "frontend"
if not frontend_path.exists():
    frontend_path = Path(__file__).parent / "frontend"
if frontend_path.exists():
    app.mount("/frontend", StaticFiles(directory=frontend_path), name="frontend")


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": MODEL_PATH.exists(),
            "timestamp": datetime.now().isoformat()}


@app.get("/forecast")
def get_forecast():
    """Return 24-hour ahead forecast (144 × 10-min blocks)."""
    try:
        return JSONResponse(generate_forecast_internal())
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Forecast error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/weather")
def get_weather():
    """Return next 48 hours of weather data for Dhanbad."""
    weather = fetch_weather(DHANBAD_LAT, DHANBAD_LON, days=2)
    now = datetime.now()
    end = now + timedelta(hours=24)
    return {
        "location": "Dhanbad, Jharkhand",
        "latitude": DHANBAD_LAT,
        "longitude": DHANBAD_LON,
        "forecast_period": {"start": now.isoformat(), "end": end.isoformat()},
        "hourly": weather,
    }


@app.get("/holidays")
def get_holidays(days_ahead: int = 30):
    """Return localized Dhanbad holidays."""
    start = datetime.now()
    end = start + timedelta(days=days_ahead)
    holidays = get_holidays_for_range(start, end)
    return {
        "location": "Dhanbad, Jharkhand",
        "period": {"start": start.date().isoformat(), "end": end.date().isoformat()},
        "holidays": holidays,
        "total": len(holidays),
        "notes": "Includes Jharkhand state holidays, BCCL/ECL industrial shutdowns, and major festive days.",
    }


@app.get("/forecast/full")
def get_full_forecast():
    """Return forecast + weather + holidays in a single response for the dashboard."""
    forecast = generate_forecast_internal()
    weather = fetch_weather(DHANBAD_LAT, DHANBAD_LON, days=2)
    now = datetime.now()
    holidays = get_holidays_for_range(now, now + timedelta(days=1))
    return {
        "forecast": forecast,
        "weather": weather,
        "holidays": holidays,
        "meta": {
            "location": "Dhanbad, Jharkhand, India",
            "generated_at": now.isoformat(),
        },
    }


if __name__ == "__main__":
    import uvicorn
    import sys
    # Add parent directory to path so imports work when run directly
    sys.path.append(str(Path(__file__).parent.parent))
    
    print("\n" + "="*70)
    print("⚡ APU Power Demand Forecasting App is starting up...")
    print("👉 CLICK HERE TO OPEN THE WEBSITE: http://localhost:8000/")
    print("="*70 + "\n")
    
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
