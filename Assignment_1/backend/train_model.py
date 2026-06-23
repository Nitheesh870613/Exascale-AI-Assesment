import pickle, logging, warnings
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).parent.parent / "data" / "Utility_consumption.csv"
OUT_PATH  = Path(__file__).parent / "model_artifact.pkl"

DHANBAD_HOLIDAYS = {
    "2022-01-14","2022-01-26","2022-03-18","2022-04-02","2022-04-14","2022-08-15",
    "2022-10-02","2022-10-05","2022-10-24","2022-11-08","2022-11-15","2022-12-25",
    "2023-01-14","2023-01-26","2023-03-08","2023-04-14","2023-04-22","2023-08-15",
    "2023-10-02","2023-10-24","2023-11-13","2023-11-15","2023-12-25","2023-12-27",
    "2024-01-14","2024-01-26","2024-03-25","2024-04-09","2024-04-14","2024-08-15",
    "2024-10-02","2024-10-12","2024-10-31","2024-11-15","2024-12-25",
}


def load_and_clean(path: Path):
    logger.info(f"Loading data from {path}")
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = df.columns.str.strip()
    logger.info(f"Columns found: {df.columns.tolist()}")

    # ── Auto-detect datetime column ───────────────────────────────────────────
    ts_col = next((c for c in df.columns if any(
        k in c.lower() for k in ["datetime","timestamp","date","time"])), None)
    if not ts_col:
        raise ValueError(f"No datetime column found. Columns: {df.columns.tolist()}")
    df = df.rename(columns={ts_col: "Timestamp"})
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], format="mixed")
    df = df.sort_values("Timestamp").reset_index(drop=True)

    # ── Auto-detect load / power columns ─────────────────────────────────────
    load_cols = [c for c in df.columns if c != "Timestamp" and any(
        k in c.lower() for k in ["power","load","consumption","demand","mw","kw"])]
    if not load_cols:
        # fallback: all remaining numeric columns
        load_cols = [c for c in df.columns if c != "Timestamp"
                     and pd.api.types.is_numeric_dtype(df[c])]
    logger.info(f"Load columns detected: {load_cols}")

    # ── Auto-detect weather columns already in CSV ────────────────────────────
    has_temp  = next((c for c in df.columns if "temp"  in c.lower()), None)
    has_hum   = next((c for c in df.columns if "humid" in c.lower()), None)
    has_wind  = next((c for c in df.columns if "wind"  in c.lower()), None)
    has_cloud = next((c for c in df.columns if "cloud" in c.lower()), None)
    logger.info(f"Weather cols in CSV → temp={has_temp} | hum={has_hum} | wind={has_wind} | cloud={has_cloud}")

    # ── Clean each load column ────────────────────────────────────────────────
    for col in load_cols:
        neg = (df[col] < 0).sum()
        df.loc[df[col] < 0, col] = np.nan
        logger.info(f"  {col} negatives fixed: {neg}")

        q1, q99 = df[col].quantile([0.01, 0.99])
        iqr = q99 - q1
        lo, hi = q1 - 3 * iqr, q99 + 3 * iqr
        outs = ((df[col] < lo) | (df[col] > hi)).sum()
        df.loc[(df[col] < lo) | (df[col] > hi), col] = np.nan
        logger.info(f"  {col} outliers fixed: {outs}  [valid range {lo:.1f} – {hi:.1f}]")

        df[col] = df[col].interpolate(method="linear", limit=6)

    # ── Standardise column names for downstream use ───────────────────────────
    remap = {c: f"Feeder_{i+1}_Load_MW" for i, c in enumerate(load_cols[:3])}
    df = df.rename(columns=remap)
    feeder_cols = list(remap.values())

    # Rename weather cols (only if not already renamed above)
    if has_temp  and has_temp  not in remap: df = df.rename(columns={has_temp:  "temperature"})
    if has_hum   and has_hum   not in remap: df = df.rename(columns={has_hum:   "humidity"})
    if has_wind  and has_wind  not in remap: df = df.rename(columns={has_wind:  "wind_speed"})
    if has_cloud and has_cloud not in remap: df = df.rename(columns={has_cloud: "cloud_cover"})

    before = len(df)
    df = df.dropna(subset=feeder_cols).reset_index(drop=True)
    logger.info(f"After cleaning: {len(df):,} rows  (dropped {before - len(df)})")

    df["Total_Load_MW"] = df[feeder_cols].sum(axis=1)
    logger.info(f"Total_Load_MW → min={df['Total_Load_MW'].min():.1f}  "
                f"max={df['Total_Load_MW'].max():.1f}  mean={df['Total_Load_MW'].mean():.1f}")

    return df, bool(has_temp), bool(has_hum), bool(has_wind)


def engineer_features(df: pd.DataFrame, has_weather_in_csv: bool) -> pd.DataFrame:
    ts = df["Timestamp"]
    df["hour"]         = ts.dt.hour
    df["minute"]       = ts.dt.minute
    df["day_of_week"]  = ts.dt.dayofweek
    df["month"]        = ts.dt.month
    df["day_of_year"]  = ts.dt.day_of_year
    df["is_weekend"]   = (ts.dt.dayofweek >= 5).astype(int)
    df["block_of_day"] = df["hour"] * 6 + df["minute"] // 10
    df["is_holiday"]   = ts.dt.strftime("%Y-%m-%d").isin(DHANBAD_HOLIDAYS).astype(int)

    # Cyclical encodings — keeps continuity across midnight / month boundaries
    df["hour_sin"]  = np.sin(2 * np.pi * df["hour"]        / 24)
    df["hour_cos"]  = np.cos(2 * np.pi * df["hour"]        / 24)
    df["dow_sin"]   = np.sin(2 * np.pi * df["day_of_week"] / 7)
    df["dow_cos"]   = np.cos(2 * np.pi * df["day_of_week"] / 7)
    df["month_sin"] = np.sin(2 * np.pi * df["month"]       / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"]       / 12)

    # ── Weather features ──────────────────────────────────────────────────────
    if has_weather_in_csv and "temperature" in df.columns:
        logger.info("Using real weather columns from CSV")
        for wc in ["temperature", "humidity", "wind_speed"]:
            if wc in df.columns:
                df[wc] = (df[wc]
                          .interpolate(method="linear", limit=12)
                          .bfill()
                          .ffill())
        # cloud_cover is NOT in your CSV — synthesise it
        if "cloud_cover" not in df.columns:
            logger.info("cloud_cover not in CSV — synthesising")
            df["cloud_cover"] = np.clip(
                40 + np.random.normal(0, 20, len(df)), 0, 100)
    else:
        logger.info("Synthesising all weather features")
        h  = df["hour"].values
        mo = df["month"].values
        df["temperature"] = (28 + 8 * np.sin(np.pi * (h - 6) / 12)
                             - 5 * np.cos(2 * np.pi * (mo - 1) / 12)
                             + np.random.normal(0, 1, len(df)))
        df["humidity"]    = np.clip(
            65 + 10 * np.cos(np.pi * h / 12) + np.random.normal(0, 3, len(df)),
            20, 100)
        df["cloud_cover"] = np.clip(
            40 + np.random.normal(0, 20, len(df)), 0, 100)
        df["wind_speed"]  = np.clip(
            8 + np.random.normal(0, 2, len(df)), 0, 30)

    return df


FEATURE_NAMES = [
    "hour", "minute", "day_of_week", "month", "day_of_year",
    "is_weekend", "block_of_day",
    "temperature", "humidity", "cloud_cover", "wind_speed", "is_holiday",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos", "month_sin", "month_cos",
]


def train():
    df, has_temp, has_hum, has_wind = load_and_clean(DATA_PATH)
    has_weather = has_temp and has_hum and has_wind
    df = engineer_features(df, has_weather)

    # Chronological split — last 90 days as hold-out test set
    split_date = df["Timestamp"].max() - pd.Timedelta(days=90)
    train_df   = df[df["Timestamp"] <= split_date]
    test_df    = df[df["Timestamp"] >  split_date]

    X_train = train_df[FEATURE_NAMES].values
    y_train = train_df["Total_Load_MW"].values
    X_test  = test_df[FEATURE_NAMES].values
    y_test  = test_df["Total_Load_MW"].values

    logger.info(f"Train: {len(X_train):,} samples  |  Test: {len(X_test):,} samples")

    model = GradientBoostingRegressor(
        n_estimators=300,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        min_samples_leaf=10,
        random_state=42,
    )
    logger.info("Training model — this takes ~2 minutes...")
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    mae   = mean_absolute_error(y_test, preds)
    rmse  = mean_squared_error(y_test, preds) ** 0.5
    mape  = np.mean(np.abs((y_test - preds) / (y_test + 1e-9))) * 100

    logger.info(f"Test results →  MAE={mae:.2f}  RMSE={rmse:.2f}  MAPE={mape:.2f}%")

    artifact = {
        "model":         model,
        "feature_names": FEATURE_NAMES,
        "scaler":        None,
        "meta": {
            "trained_on":    str(pd.Timestamp.now()),
            "train_rows":    len(X_train),
            "test_mae":      round(mae,  3),
            "test_rmse":     round(rmse, 3),
            "test_mape_pct": round(mape, 3),
            "algorithm":     "GradientBoostingRegressor",
        },
    }

    with open(OUT_PATH, "wb") as f:
        pickle.dump(artifact, f)
    logger.info(f"model_artifact.pkl saved → {OUT_PATH}")
    return artifact


if __name__ == "__main__":
    train()
    print("\n" + "="*70)
    print("⚡ Model training completed successfully!")
    print("👉 Next step: Start the server and view the dashboard by running:")
    print("   python backend/main.py")
    print("="*70 + "\n")