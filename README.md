# Parcel Viewer

This repository contains a small Streamlit application for viewing cadastral parcels.
Users can search for Queensland (QLD) or New South Wales (NSW) parcels by lot and plan, visualize them on a map and export the results as KML or shapefiles.

## Setup

Install the required packages:

```bash
pip install -r requirements.txt
```

Run the tests to make sure everything works:

```bash
pytest
```

## Running the API (FastAPI)

**Local**

```bash
pip install -r requirements.txt
uvicorn main:app --reload
# → http://127.0.0.1:8000/docs
```

**Render**

1. Create a *Web Service* from this repo.
2. Build cmd: *pip install -r requirements.txt*
3. Start cmd: **uvicorn main:app --host 0.0.0.0 --port $PORT**
4. Region: **Sydney** (keeps latency low to the front‑end).
5. Add env vars such as `ARCGIS_API_KEY` if used in `kml_utils`.

Your front‑end should reference this with  
`VITE_API_BASE=https://<your-service>.onrender.com`.

# Note: app.py (Streamlit) kept for local visual testing only.

