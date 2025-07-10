# app.py  –  top of file
import streamlit as st
import requests
import simplekml
import io, re
import geopandas as gpd
from shapely.geometry import shape, mapping
from shapely.ops import unary_union, transform
from pyproj import Transformer
import leafmap.foliumap as leafmap
# ---------- ArcGIS REST endpoints ----------
QLD_URL = ("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
           "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW_URL = ("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
           "NSW_Cadastre/MapServer/9/query")

# ---------- Helper functions ----------
def fetch_merged_geom(lotplan: str):
    """Return a merged (Multi)Polygon for a Lot/Plan, or None if not found."""
    is_qld = bool(re.match(r"^\d+[A-Z]{1,3}\d+$", lotplan, re.I))
    url, field = (QLD_URL, "lotplan") if is_qld else (NSW_URL, "lotidstring")

    js = requests.get(url, params={
        "where": f"{field}='{lotplan}'",
        "returnGeometry": "true", "f": "geojson"
    }).json()

    feats = js.get("features", [])
    if not feats:
        return None

    shapes = []
    for f in feats:
        geom = f["geometry"]
        wkid = geom.get("spatialReference", {}).get("wkid", 4326)
        g = shape(geom)
        if wkid != 4326:
            t = Transformer.from_crs(wkid, 4326, always_xy=True)
            g = transform(t.transform, g)
        shapes.append(g)
    return unary_union(shapes)          # merge into one geometry


def rgba_to_kml(hex_rgb: str, opacity_pct: int) -> str:
    """'#rrggbb' + opacity -> 'aabbggrr' (KML colour)"""
    r, g, b = hex_rgb.lstrip("#")[:2], hex_rgb[3:5], hex_rgb[5:]
    alpha = int(round(255 * opacity_pct / 100))
    return f"{alpha:02x}{b}{g}{r}"


# ---------- Streamlit UI ----------
st.set_page_config(page_title="Lot/Plan → KML", layout="centered")
st.title("Lot/Plan → KML  |  QLD + NSW")

lot_text = st.text_area("Paste Lot/Plan IDs (one per line):",
                        height=180,
                        placeholder="6RP702264\n5//DP123456")

folder_name   = st.text_input("Folder name inside the KML", "Parcels")
poly_hex      = st.color_picker("Polygon fill colour", "#ff6600")
poly_opacity  = st.number_input("Polygon opacity (0–100 %)", 0, 100, 70)
line_hex      = st.color_picker("Boundary line colour", "#444444")
line_width    = st.number_input("Line width (px)", 0.1, 10.0, 1.2, step=0.1)

if st.button("🔍 Search Lots") and lot_text.strip():
    lot_ids = [lp.strip() for lp in lot_text.splitlines() if lp.strip()]
    lot_geoms, missing = {}, []

    for lp in lot_ids:
        g = fetch_merged_geom(lp)
        if g:
            lot_geoms[lp] = g
        else:
            missing.append(lp)

    if missing:
        st.warning(f"No parcel found for: {', '.join(missing)}")

    if lot_geoms:
        # --- Preview map with leafmap -------------------------------
        m = leafmap.Map(center=[-25, 145], zoom=4, draw_export=False)
        for lp, geom in lot_geoms.items():
            gdf = gpd.GeoDataFrame({"lotplan": [lp]}, geometry=[geom], crs=4326)
            style = {
                "fillColor": poly_hex,
                "color": line_hex,
                "weight": line_width,
                "fillOpacity": poly_opacity / 100
            }
            m.add_gdf(gdf, layer_name=lp, style=style)
        m.to_streamlit(height=500)      # shiny map in the app   [oai_citation:0‡GitHub](https://github.com/opengeos/leafmap/discussions/69?utm_source=chatgpt.com)

        # --- Build KML for download -------------------------------
        poly_kml = rgba_to_kml(poly_hex, poly_opacity)
        line_kml = rgba_to_kml(line_hex, 100)

        kml = simplekml.Kml()
        folder = kml.newfolder(name=(folder_name or "Parcels"))
        for lp, geom in lot_geoms.items():
            coords = mapping(geom)["coordinates"][0]
            p = folder.newpolygon(name=lp, outerboundaryis=coords)
            p.style.polystyle.color = poly_kml
            p.style.linestyle.color = line_kml
            p.style.linestyle.width = float(line_width)

        st.download_button(
            "📥 Download KML",
            data=io.BytesIO(kml.kml().encode("utf-8")).getvalue(),
            file_name="parcels.kml",
            mime="application/vnd.google-earth.kml+xml"
        )
    else:
        st.error("No valid parcels returned—nothing to preview or download.")
