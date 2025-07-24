from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
import kml_utils  # existing helper module

app = FastAPI(title="Vision\u00a0Backend API", version="1.0.0")


@app.get("/")
async def index():
    """Simple index redirect hinting at docs."""
    return {"message": "See /docs for API documentation"}


@app.head("/")
async def index_head() -> Response:
    """Return an empty response so HEAD requests succeed."""
    return Response()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # TODO: tighten in prod
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():          # basic liveness probe
    return {"status": "ok"}


@app.head("/health")
async def health_head() -> Response:
    """Empty 200 response for HEAD health checks."""
    return Response()

@app.get("/api/parcels/{lotplan}")
async def parcel_geojson(lotplan: str):
    """
    Return GeoJSON for a given lot/plan.
    Relies on kml_utils.fetch_parcel_geojson (add shim below if missing).
    """
    if not hasattr(kml_utils, "fetch_parcel_geojson"):
        raise HTTPException(500, "kml_utils.fetch_parcel_geojson missing")
    data = await kml_utils.fetch_parcel_geojson(lotplan)
    if data is None:
        raise HTTPException(404, f"{lotplan} not found")
    return data


@app.head("/api/parcels/{lotplan}")
async def parcel_geojson_head(lotplan: str) -> Response:
    """Return 200 for HEAD checks even if parcel does not exist."""
    return Response()

@app.get("/api/parcels/{lotplan}/kml")
async def parcel_kml(lotplan: str):
    """
    Download a KML for the parcel.
    """
    if not hasattr(kml_utils, "build_kml"):
        raise HTTPException(500, "kml_utils.build_kml missing")
    kml = await kml_utils.build_kml(lotplan)
    if kml is None:
        raise HTTPException(404, f"{lotplan} not found")
    return Response(
        content=kml,
        media_type="application/vnd.google-earth.kml+xml",
        headers={"Content-Disposition": f'attachment; filename="{lotplan}.kml"'}
    )


@app.head("/api/parcels/{lotplan}/kml")
async def parcel_kml_head(lotplan: str) -> Response:
    """Empty head response for KML endpoint."""
    return Response()
