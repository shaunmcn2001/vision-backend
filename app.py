import streamlit as st, requests, simplekml, io, re
from shapely.geometry import shape, mapping
from shapely.ops import transform
from pyproj import Transformer

# ---------- REST endpoints ----------
QLD_URL = ("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
           "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW_URL = ("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
           "NSW_Cadastre/MapServer/9/query")

# ---------- helpers ----------
def fetch_geom(lotplan: str):
    is_qld = re.match(r"^\d+[A-Z]{1,3}\d+$", lotplan, re.I)
    url, field = (QLD_URL, "lotplan") if is_qld else (NSW_URL, "lotidstring")
    r = requests.get(url, params={
        "where": f"{field}='{lotplan}'",
        "returnGeometry": "true", "outFields": "*", "f": "geojson"
    }).json()
    for feat in r.get("features", []):
        geom = feat["geometry"]
        wkid = geom.get("spatialReference", {}).get("wkid", 4326)
        if wkid != 4326:
            tfm = Transformer.from_crs(wkid, 4326, always_xy=True)
            yield mapping(transform(lambda x, y, *_: tfm.transform(x, y), shape(geom)))
        else:
            yield geom

def rgba_to_kml(hex_rgb: str, opacity_pct: int) -> str:
    hex_rgb = hex_rgb.lstrip("#")
    r, g, b = hex_rgb[:2], hex_rgb[2:4], hex_rgb[4:6]
    alpha = int(round(255 * opacity_pct / 100))
    return f"{alpha:02x}{b}{g}{r}"          # KML expects aabbggrr

# ---------- Streamlit UI ----------
st.set_page_config(page_title="Lot/Plan → KML", layout="centered")
st.title("Lot/Plan → KML (QLD & NSW)")

lot_text = st.text_area(
    "Paste Lot/Plan IDs (one per line):",
    height=220,
    placeholder="6RP702264\n5//DP123456\n7/1/DP98765"
)

folder_name = st.text_input("Folder name inside the KML", "Parcels")

poly_hex = st.color_picker("Polygon fill colour", "#ff6600")
poly_opacity = st.number_input("Polygon opacity (0–100 %)", min_value=0, max_value=100, value=70)

line_hex = st.color_picker("Boundary line colour", "#444444")
line_width = st.number_input("Line width (px)", min_value=0.1, max_value=10.0, value=1.2, step=0.1)

if st.button("Create KML") and lot_text.strip():
    kml = simplekml.Kml()
    parent_folder = kml.newfolder(name=folder_name.strip() or "Parcels")

    poly_kml_col  = rgba_to_kml(poly_hex,  poly_opacity)
    line_kml_col  = rgba_to_kml(line_hex, 100)          # outlines stay opaque

    for lp in [l.strip() for l in lot_text.splitlines() if l.strip()]:
        for geom in fetch_geom(lp):
            poly = parent_folder.newpolygon(
                name=lp,
                outerboundaryis=geom["coordinates"][0]
            )
            poly.style.polystyle.color  = poly_kml_col
            poly.style.linestyle.color  = line_kml_col
            poly.style.linestyle.width  = float(line_width)

    kml_bytes = io.BytesIO(kml.kml().encode("utf-8"))
    st.download_button(
        "📥 Download KML",
        data=kml_bytes.getvalue(),
        file_name="parcels.kml",
        mime="application/vnd.google-earth.kml+xml"
    )
