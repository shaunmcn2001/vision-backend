"""
Lot/Plan  ➜  styled KML  (Leaflet edition)
------------------------------------------
• Paste Lot/Plan IDs (one per line)
• ArcGIS address search, measurement, basemap picker
• Preview polygons on Esri/OSM imagery
• Download colour-styled KML
"""

import io, re, requests
import streamlit as st
import leafmap.foliumap as leafmap
from shapely.geometry import shape, mapping
from shapely.ops import unary_union, transform
from pyproj import Transformer
import simplekml

# ---------- ArcGIS parcel services ----------
QLD_URL = ("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
           "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW_URL = ("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
           "NSW_Cadastre/MapServer/9/query")

# ---------- helpers ----------
def fetch_merged_geom(lotplan: str):
    is_qld = bool(re.match(r"^\d+[A-Z]{1,3}\d+$", lotplan, re.I))
    url, fld = (QLD_URL, "lotplan") if is_qld else (NSW_URL, "lotidstring")
    js = requests.get(url, params={"where": f"{fld}='{lotplan}'",
                                   "returnGeometry": "true",
                                   "f": "geojson"}, timeout=15).json()
    feats = js.get("features", [])
    if not feats:
        return None
    geoms = []
    for f in feats:
        g = shape(f["geometry"])
        wkid = f["geometry"].get("spatialReference", {}).get("wkid", 4326)
        if wkid != 4326:
            g = transform(Transformer.from_crs(wkid, 4326, always_xy=True).transform, g)
        geoms.append(g)
    return unary_union(geoms)

def kml_colour(hex_rgb: str, opacity_pct: int):
    r,g,b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    a = int(round(255*opacity_pct/100))
    return f"{a:02x}{b}{g}{r}"

# ---------- Streamlit layout ----------
st.set_page_config(page_title="Lot/Plan → KML", layout="wide")
col_map, col_ctrl = st.columns([3,1], gap="large")

# ---------- right-hand controls ----------
with col_ctrl:
    st.markdown("### Parcel search")
    lot_text = st.text_area("Lot/Plan IDs (one per line)",
                            height=150, placeholder="6RP702264\n5//DP123456")
    poly_hex     = st.color_picker("Fill colour", "#ff6600")
    poly_opacity = st.number_input("Fill opacity (%)", 0,100,70)
    line_hex     = st.color_picker("Outline colour", "#2e2e2e")
    line_width   = st.number_input("Outline width (px)", 0.1,10.0,1.2,step=0.1)
    folder_name  = st.text_input("Folder name inside KML", "Parcels")
    do_search    = st.button("🔍 Search lots", use_container_width=True)

# ---------- search logic ----------
if do_search and lot_text.strip():
    ids, geoms, missing = [i.strip() for i in lot_text.splitlines() if i.strip()], {}, []
    with st.spinner("Fetching parcels…"):
        for lp in ids:
            g = fetch_merged_geom(lp)
            (geoms if g else missing.append(lp)) and (geoms.update({lp:g}) if g else None)
    if missing:
        st.warning("No parcel found for: " + ", ".join(missing))
    st.session_state["lot_geoms"] = geoms
    st.session_state["style"] = dict(
        fill=poly_hex, op=poly_opacity, line=line_hex, width=line_width,
        folder=folder_name or "Parcels"
    )

# ---------- map column ----------
with col_map:
    st.markdown("### Preview")
    m = leafmap.Map(center=[-25,145], zoom=4, draw_export=False)
    # built-in basemap picker, measure, geocoder
    m.add_basemap("Esri.WorldImagery")
    m.add_basemap("Esri.WorldTopoMap")
    m.add_basemap("OpenStreetMap.Mapnik")
    m.add_measure_control()                         # distance / area tool
    m.add_geocoder(name="Search address", position="topleft",
                   provider="arcgis", add_marker=True)

    if "lot_geoms" in st.session_state and st.session_state["lot_geoms"]:
        s = st.session_state["style"]
        for lp,g in st.session_state["lot_geoms"].items():
            gj = {"type":"Feature","geometry":mapping(g),"properties":{"name":lp}}
            m.add_geojson(gj,
                          layer_name=lp,
                          style={"fillColor":s["fill"],
                                 "color":s["line"],
                                 "weight":s["width"],
                                 "fillOpacity":s["op"]/100})

    m.to_streamlit(height=560)

# ---------- download ----------
if ("lot_geoms" in st.session_state and st.session_state["lot_geoms"]
    and col_ctrl.button("📥 Download KML", use_container_width=True)):
    s, kml = st.session_state["style"], simplekml.Kml()
    folder = kml.newfolder(name=s["folder"])
    fill_k, line_k = kml_colour(s["fill"], s["op"]), kml_colour(s["line"],100)
    for lp,g in st.session_state["lot_geoms"].items():
        p = folder.newpolygon(name=lp, outerboundaryis=mapping(g)["coordinates"][0])
        p.style.polystyle.color, p.style.linestyle.color = fill_k, line_k
        p.style.linestyle.width = float(s["width"])
    data = io.BytesIO(kml.kml().encode("utf-8")).getvalue()
    st.download_button("Save KML", data, "parcels.kml",
                       "application/vnd.google-earth.kml+xml",
                       use_container_width=True)
