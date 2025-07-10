import streamlit as st, requests, simplekml, io, re
from shapely.geometry import shape, mapping
from shapely.ops import transform
from pyproj import Transformer

# --- REST endpoints -------------------------------------------------
QLD_URL = ("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
           "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW_URL = ("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
           "NSW_Cadastre/MapServer/9/query")

def fetch_geom(lotplan: str):
    """Return iterable of GeoJSON geometries (WGS-84) for one Lot/Plan ID."""
    is_qld = re.match(r"^\d+[A-Z]{1,3}\d+$", lotplan, re.I)  # crude but works
    url, field = (QLD_URL, "lotplan") if is_qld else (NSW_URL, "lotidstring")

    r = requests.get(url, params={
        "where": f"{field}='{lotplan}'",
        "returnGeometry": "true",
        "outFields": "*",
        "f": "geojson"
    }).json()

    for feat in r.get("features", []):
        g = feat["geometry"]
        wkid = g.get("spatialReference", {}).get("wkid", 4326)
        if wkid != 4326:
            t = Transformer.from_crs(wkid, 4326, always_xy=True)
            yield mapping(transform(lambda x, y, *_: t.transform(x, y), shape(g)))
        else:
            yield g

# --- Streamlit UI ---------------------------------------------------
st.set_page_config(page_title="Lot/Plan → KML", layout="centered")
st.title("Lot/Plan → KML (QLD + NSW)")

raw = st.text_area("Paste Lot/Plan IDs (one per line):", height=220,
                   placeholder="6RP702264\n5//DP123456\n7/1/DP98765")

poly_colour = st.color_picker("Polygon colour", "#ff6600")
line_colour = st.color_picker("Boundary line colour", "#444444")

if st.button("Create KML") and raw.strip():
    kml = simplekml.Kml()
    for lp in [l.strip() for l in raw.splitlines() if l.strip()]:
        for geom in fetch_geom(lp):
            ply = kml.newpolygon(
                name = lp,
                outerboundaryis = geom["coordinates"][0]
            )
            ply.style.polystyle.color = poly_colour
            ply.style.linestyle.color = line_colour
            ply.style.linestyle.width = 1.2

# Get the raw KML as a UTF-8 string and wrap it in a BytesIO
    buf = io.BytesIO(kml.kml().encode("utf-8"))
    st.download_button("📥 Download KML",
                       data=buf.getvalue(),
                       file_name="parcels.kml",
                       mime="application/vnd.google-earth.kml+xml")
