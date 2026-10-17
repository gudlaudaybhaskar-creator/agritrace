# AgriTrace — Demonstration & Validation Guide

This walkthrough verifies the core features of **AgriTrace**, including persistent farmer onboarding, historical farm memory, evidence-backed reasoning, and cloud synchronization upon logout.

---

## 1. Starting the Application

From the project root:
```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser.

---

## 2. Using Seeded Demo Data (Fastest Path)

To immediately test the application with pre-populated records:
```bash
python -m scripts.seed_demo
```
Sign in with:
- **Username**: `demo_farmer`
- **Password**: `demoPass123`

The user interface will display the sample farm and readings clearly marked with a `DEMO DATA` banner.

---

## 3. Manual Step-by-Step Walkthrough

### Step 1: Farmer Registration
1. In the onboarding screen, choose **New Farmer Registration**.
2. Enter your username, full name, and password (at least 8 characters).
3. Optionally add your farm parcel and crop cycle, or skip to add them later.
4. Click **Save my farm**.

### Step 2: Logging Baseline Field Readings (My Farm)
1. Go to the **My Farm** tab.
2. Click **Record Reading**.
3. Log a baseline measurement:
   - Soil Moisture: `28.0%`
   - Nitrogen: `65 kg/ha`
4. Click **Save Reading**. The reading is stored in your permanent memory history.

### Step 3: Submitting a Problem & Memory Comparison (Ask)
1. Navigate to the **Ask** tab.
2. Select your field.
3. In the problem description box, enter:
   > *"Lower leaves are turning pale yellow and wilting after heavy rain."*
4. Optionally record a current measurement (e.g. Soil Moisture: `13.5%`).
5. Click **Start Investigation**.
6. The system calculates the moisture delta (`28.0% -> 13.5%`, net decline of `14.5%`) and evaluates matching hypotheses.

### Step 4: Reviewing Findings (Results Workspace)
1. In the Results tab, review:
   - **One-sentence finding summary**
   - **Evidence strength badge** (Strong, Moderate, or Weak)
   - **What we checked vs what we could not check**
   - **Practical checklist actions** that persist when ticked
   - **Kisan Call Centre helpline card** (1800-180-1551)
   - **Technical details accordion** showing actual execution timestamps

### Step 5: Logout & Cloud Synchronization
1. Click **Log Out** in the header.
2. Any newly modified or added records synchronize to Supabase if configured.
3. If Supabase is unconfigured, all records stay safely on the local server.
4. The local DOM and tokens are cleared for privacy on shared phones.
