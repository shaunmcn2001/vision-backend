"""
Lot/Plan  ➜  styled KML (QLD + NSW)
-----------------------------------
• Paste Lot/Plan IDs (one per line)
• Choose basemap (Esri imagery / topo or OSM)
• Click “🔍 Search lots” → preview polygons
• Click “📥 Download KML” to save a colour-styled KML
"""

import os, re, io
import streamlit as st
import pydeck as pdk
import requests, simplekml
from shapely.geometry import shape, mapping
from shapely.ops import unary_union, transform
from pyproj import Transformer

# ────────────────────────────────────────────────────────────
#  ArcGIS REST endpoints for parcel lookup
# ────────────────────────────────────────────────────────────
QLD_URL = (
    "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
    "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query"
)
NSW_URL = (
    "https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
    "NSW_Cadastre/MapServer/9/query"
)

# ────────────────────────────────────────────────────────────
#  Helper functions
# ────────────────────────────────────────────────────────────
def fetch_merged_geom(lotplan: str):
    """Return merged Shapely geometry (WGS-84) or None if not found."""
    is_qld = bool(re.match(r"^\d+[A-Z]{1,3}\d+$", lotplan, re.I))
    url, fld = (QLD_URL, "lotplan") if is_qld else (NSW_URL, "lotidstring")

    js = requests.get(
        url,
        params={"where": f"{fld}='{lotplan}'", "returnGeometry": "true", "f": "geojson"},
        timeout=15,
    ).json()

    feats = js.get("features", [])
    if not feats:
        return None

    geoms = []
    for f in feats:
        wkid = f["geometry"].get("spatialReference", {}).get("wkid", 4326)
        g = shape(f["geometry"])
        if wkid != 4326:
            t = Transformer.from_crs(wkid, 4326, always_xy=True)
            g = transform(t.transform, g)
        geoms.append(g)

    return unary_union(geoms)


def hex_opacity_to_rgba(hex_rgb: str, opacity_pct: int):
    r = int(hex_rgb[1:3], 16)
    g = int(hex_rgb[3:5], 16)
    b = int(hex_rgb[5:7], 16)
    a = int(round(255 * opacity_pct / 100))
    return [r, g, b, a]


def kml_colour(hex_rgb: str, opacity_pct: int):
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    a = int(round(255 * opacity_pct / 100))
    return f"{a:02x}{b}{g}{r}"  # KML needs aabbggrr

# ────────────────────────────────────────────────────────────
#  Streamlit layout
# ────────────────────────────────────────────────────────────
st.set_page_config(page_title="Lot/Plan → KML", layout="wide")
col_map, col_ctrl = st.columns([3, 1], gap="large")

# ─── Controls ────────────────────────────────────────────────
with col_ctrl:
    st.markdown("### Parcel search")
    ctrl = st.container(border=True)

with ctrl:
    lot_text = st.text_area("Lot/Plan IDs (one per line)",
                            height=150,
                            placeholder="6RP702264\n5//DP123456")

    # Basemap selector
    basemap_url = st.selectbox(
        "Basemap",
        {
            "Esri World Imagery (satellite)":
            "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",

            "Esri World Topo":
            "https://services.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",

            "OpenStreetMap":
            "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        }
    )

    poly_hex     = st.color_picker("Fill colour", "#ff6600")
    poly_opacity = st.number_input("Fill opacity (%)", 0, 100, 70)
    line_hex     = st.color_picker("Outline colour", "#2e2e2e")
    line_width   = st.number_input("Outline width (px)", 0.1, 10.0, 1.2, step=0.1)
    folder_name  = st.text_input("Folder name inside KML", "Parcels")
    do_search    = st.button("🔍 Search lots", use_container_width=True)

# ─── Search logic ───────────────────────────────────────────
if do_search and lot_text.strip():
    ids   = [lp.strip() for lp in lot_text.splitlines() if lp.strip()]
    geoms = {}; missing = []

    with st.spinner("Fetching parcels…"):
        for lp in ids:
            g = fetch_merged_geom(lp)
            (geoms if g else missing.append(lp)) and (geoms.update({lp: g}) if g else None)

    if missing:
        st.warning("No parcel found for: " + ", ".join(missing))

    st.session_state["lot_geoms"] = geoms
    st.session_state["style"] = dict(
        fill_hex=poly_hex,
        fill_opacity=poly_opacity,
        line_hex=line_hex,
        line_width=line_width,
        folder=folder_name or "Parcels",
        basemap=basemap_url
    )

# ─── Map preview ────────────────────────────────────────────
with col_map:
    st.markdown("### Preview")

    layers = [
        pdk.Layer(  # Basemap first
            "TileLayer",
            data=st.session_state.get("style", {}).get(
                "basemap",
                "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            ),
            min_zoom=0, max_zoom=19, tile_size=256,
        )
    ]

    if "lot_geoms" in st.session_state and st.session_state["lot_geoms"]:
        s = st.session_state["style"]
        layers.append(
            pdk.Layer(
                "GeoJsonLayer",
                {
                    "type": "FeatureCollection",
                    "features": [
                        {"type": "Feature",
                         "geometry": mapping(g),
                         "properties": {"lp": lp}}
                        for lp, g in st.session_state["lot_geoms"].items()
                    ]
                },
                get_fill_color=hex_opacity_to_rgba(s["fill_hex"], s["fill_opacity"]),
                get_line_color=hex_opacity_to_rgba(s["line_hex"], 100),
                line_width_min_pixels=s["line_width"],
                pickable=True,
                auto_highlight=True,
            )
        )

    st.pydeck_chart(
        pdk.Deck(
            layers=layers,
            initial_view_state=pdk.ViewState(latitude=-25, longitude=145, zoom=4),
            tooltip={"html": "<b>{lp}</b>", "style": {"color": "white"}},
        ),
        use_container_width=True,
    )

# ─── Download KML ───────────────────────────────────────────
if (
    "lot_geoms" in st.session_state
    and st.session_state["lot_geoms"]
    and col_ctrl.button("📥 Download KML", use_container_width=True)
):
    s = st.session_state["style"]
    fill_kml = kml_colour(s["fill_hex"], s["fill_opacity"])
    line_kml = kml_colour(s["line_hex"], 100)

    kml = simplekml.Kml()
    parent = kml.newfolder(name=s["folder"])
    for lp, geom in st.session_state["lot_geoms"].items():
        coords = mapping(geom)["coordinates"][0]
        p = parent.newpolygon(name=lp, outerboundaryis=coords)
        p.style.polystyle.color = fill_kml
        p.style.linestyle.color = line_kml
        p.style.linestyle.width = float(s["line_width"])

    st.download_button(
        "Save KML",
        data=io.BytesIO(kml.kml().encode("utf-8")).getvalue(),
        file_name="parcels.kml",
        mime="application/vnd.google-earth.kml+xml",
        use_container_width=True,
    )
