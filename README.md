# AgriTrace — Agricultural Records & Evidence-Based Guidance

AgriTrace is a transparent agricultural records and farm memory platform. It maintains a persistent record of a farmer's land, fields, crop cycles, observations, uploaded photos, prior investigations, and findings. When a farmer reports a problem, the system connects it to earlier historical measurements, evaluates possible causes using agronomic rules or AI, and presents findings with evidence strength and practical actions.

---

## Key Highlights

- **Persistent Farm Memory**: Historical measurements are never overwritten. Time-series observation deltas (e.g. soil moisture drops from 28% to 13.5%) are preserved and compared.
- **Works Without Keys**: Operates end-to-end with no external API keys required. Without Gemini, deterministic rule-based guidance is used; without Supabase, records stay safely on the local server; without FalkorDB, the embedded property graph is used.
- **Privacy by Design**: User photos have EXIF GPS coordinates stripped upon upload. Precise coordinates are never sent to external AI providers.
- **Mobile First & Accessible**: Designed for smallholder farmers on low and mid-range Android phones with patchy connectivity, 16px base typography, 44px touch targets, and multilingual support (English, Hindi, Odia).
- **Scientific Datasets & Provenance**:
  - `Crop_recommendationV2.csv`: Kaggle public agronomic benchmark for crop suitability reference.
  - `data_core.csv`: Empirical soil and nutrient benchmark table used as reference lookup.
  - `sensor_Crop_Dataset (1).csv`: Benchmark observations across various soil types.
  - `AgriNet_Dataset-G7TD2K.csv`: Soil health dataset from KVK, provided for academic and research use with privacy-redacted sample cards.

---

## 5 Primary Farmer Screens

1. **Home (Dashboard)**: Real-time land overview, recent investigations, quick action shortcuts.
2. **Ask (New Investigation)**: Voice input, compressed photo upload (< 1 MB), optional measurements, and progress feedback.
3. **My Farm**: Manage land holdings, parcel fields, crop cycles, and recorded readings.
4. **Results**:
   - Investigation Workspace: One-sentence finding, evidence strength, what was checked vs what could not be checked, persistent action checklist, and Kisan helpline contact.
   - History List: Archive of all past investigations.
   - Farm Memory Timeline: Unified chronological record of profile events, readings, and findings.
5. **About the Data**: Reference datasets, lazy-loaded redacted soil test cards, and privacy controls.

---

## Quickstart Instructions

### 1. Requirements
- Python 3.10+
- (Optional) FalkorDB / Redis running on `localhost:6379`
- (Optional) Supabase Project URL & Service Role Key
- (Optional) Google AI Studio Gemini API Key

### 2. Environment Setup
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
The app starts cleanly even with an empty `.env`. To persist sessions across server restarts, generate a secure random secret key:
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 3. Launch the Server
```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```
Open **http://localhost:8000** in your browser.

### 4. Seed Demo Data (Optional)
To test with sample pre-populated demo data:
```bash
python -m scripts.seed_demo
```
This registers `demo_farmer` with baseline readings and marks all screens with a visible DEMO DATA banner.

### 5. Run the Test Suite
```bash
pytest tests/test_agritrace.py -v
```
