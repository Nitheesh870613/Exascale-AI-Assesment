# Carbon Emissions Reporting Platform
This repository contains a dashboard application built for tracking, calculating, and visualizing Greenhouse Gas (GHG) emissions for the Central Steel Plant.

## Project Structure

```
Assignment_2/
├── backend/
│   ├── main.py            # FastAPI backend (API endpoints, seeding, SQLite database operations)
│   ├── seeded_data.json   # Central Steel Plant Q1 & Q2 dataset
│   ├── emissions.db       # SQLite database (generated on startup)
│   └── requirements.txt   # Python package dependencies
├── frontend/
│   └── index.html         # Single-page dashboard (Chart.js visualizations)
├── Dockerfile             # Container configuration
└── README.md              # Documentation
```

---

## Tech Stack

* **Backend**: FastAPI (Python)
* **Database**: SQLite (WAL mode enabled)
* **Frontend**: HTML5, CSS3, Vanilla JavaScript
* **Charts**: Chart.js 4.4.1
* **Typography**: Outfit, JetBrains Mono
* **Containerization**: Docker

---

## Database Schema

The SQLite database consists of four tables to store factors, records, manual overrides, and business metrics.

### 1. EmissionFactors
Stores the carbon conversion factors for different materials and plant sections. It is versioned using date boundaries to allow historical calculation accuracy.
* `id` (TEXT, Primary Key)
* `activity_name` (TEXT)
* `activity_category` (TEXT) - Maps to plant sections (e.g., Pellet Plant, DRI)
* `unit` (TEXT) - tonnes, kNm3, KL, Nm3
* `co2e_per_unit` (REAL) - Stored in kgCO2e/unit
* `scope` (INTEGER) - Scope 1 (Direct)
* `source` (TEXT) - e.g., IPCC 2006 Guidelines
* `valid_from` (DATE)
* `valid_to` (DATE, Nullable)

### 2. EmissionRecords
Stores logged activities, quantities, the snapshot of the factor used, and final calculations.
* `id` (TEXT, Primary Key)
* `activity_date` (DATE)
* `activity_name` (TEXT)
* `scope` (INTEGER)
* `quantity` (REAL)
* `unit` (TEXT)
* `emission_factor_id` (TEXT)
* `emission_factor_used` (REAL)
* `calculated_co2e` (REAL)
* `is_override` (INTEGER) - Boolean flag (0 or 1)
* `override_value` (REAL, Nullable)
* `final_co2e` (REAL)
* `notes` (TEXT, Nullable)

### 3. AuditLog
Stores manual override history for auditing.
* `id` (TEXT, Primary Key)
* `record_id` (TEXT)
* `action` (TEXT) - e.g., MANUAL_OVERRIDE
* `old_co2e` (REAL)
* `new_co2e` (REAL)
* `reason` (TEXT)
* `timestamp` (TEXT)

### 4. BusinessMetrics
Stores plant production data used for carbon intensity calculations.
* `id` (TEXT, Primary Key)
* `date` (DATE)
* `metric_name` (TEXT) - "Tonnes of Steel Produced" or "Number of Employees"
* `value` (REAL)
* `unit` (TEXT)

---

## Pre-seeded Data
Upon first run, the database is automatically created and seeded:
* **Factors**: Seeding contains 63 materials from the Central Steel Plant dataset. To verify historical accuracy, 2023 versioned factors are pre-seeded with values 5% higher than 2024.
* **Records**: Contains 1,107 records. Seeding splits your Q1 and Q2 dataset monthly. 2023 records are synthesized at a 95% baseline for YoY comparison.
* **Metrics**: Monthly steel production is dynamically seeded to scale with emissions, yielding a carbon intensity of exactly 2.1 tCO2e/tonne.

---

## Setup and Running

Follow these instructions to run the application locally or via Docker.

### Option 1: Running Locally

#### 1. Navigate to the project folder
Open your terminal and make sure you are in the directory containing the Dockerfile:
```bash
cd Assignment_2
```

#### 2. Create and Activate a Virtual Environment
* **On Windows (PowerShell):**
  ```powershell
  python -m venv venv
  .\venv\Scripts\Activate
  ```
* **On macOS/Linux:**
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

#### 3. Install Dependencies
```bash
pip install -r backend/requirements.txt
```

#### 4. Run the Server
```bash
cd backend
python main.py
```
*(Or use `uvicorn main:app --reload --port 8001`)*

#### 5. Access the Dashboard
Open your web browser and go to:
http://localhost:8001/

---

### Option 2: Running with Docker

Make sure Docker is running on your machine.

#### 1. Build the Docker Image
Navigate to the directory containing the Dockerfile and run:
```bash
docker build -t carbon-platform .
```

#### 2. Run the Container
```bash
docker run -d -p 8001:8001 --name carbon-app carbon-platform
```

To view the server logs:
```bash
docker logs carbon-app
```

#### 3. Access the Dashboard
Open your browser and navigate to:
http://localhost:8001/

#### 4. Stop the Container
```bash
docker stop carbon-app
docker rm carbon-app
```

---

## API Reference

### Core Endpoints
* `GET /` - Serves the dashboard UI.
* `GET /factors` - Lists active factors.
* `POST /records` - Creates a new record.
* `GET /records` - Lists logged records.
* `POST /records/override` - Modifies a record and creates an audit trail entry.
* `GET /audit` - Lists override history.
* `POST /metrics` - Logs business metrics.

### Analytics Endpoints
* `GET /analytics/yoy` - YoY emissions comparison.
* `GET /analytics/hotspot` - Top emission sources.
* `GET /analytics/monthly-trend` - Monthly emissions trend.
* `GET /analytics/intensity` - Returns emission intensity.
* `GET /analytics/summary` - Aggregated analytical payload for the dashboard.
