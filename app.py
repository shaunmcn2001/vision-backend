import os, re, io, json
import streamlit as st
MAPBOX_TOKEN = st.secrets["MAPBOX_API_KEY"]
import pydeck as pdk
import requests, simplekml
import geopandas as gpd
from shapely.geometry import shape, mapping
from shapely.ops import unary_union, transform
from pyproj import Transformer

# ------------------------------------------------------------------
#  !!!  Mapbox token (required for deck.gl basemap)  !!!
#  1) set as env var:      export MAPBOX_API_KEY="pk.XXXXX"
#  2) or add to .streamlit/secrets.toml as 'MAPBOX_API_KEY'
# ------------------------------------------------------------------
MAPBOX_TOKEN = (
    st.secrets.get("MAPBOX_API_KEY")
    if "MAPBOX_API_KEY" in st.secrets
    else os.getenv("MAPBOX_API_KEY", "")
)

# ------------------------------------------------------------------
#  REST endpoints
# ------------------------------------------------------------------
QLD_URL = (
    "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
    "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query"
)
NSW_URL = (
    "https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
    "NSW_Cadastre/MapServer/9/query"
)

# ------------------------------------------------------------------
#  helpers
# ------------------------------------------------------------------
def fetch_merged_geom(lotplan: str):
    """Merged Shapely geometry or None if not found"""
    is_qld = bool(re.match(r"^\d+[A-Z]{1,3}\d+$", lotplan, re.I))
    url, fld = (QLD_URL, "lotplan") if is_qld else (NSW_URL, "lotidstring")

    js = requests.get(
        url,
        params={
            "where": f"{fld}='{lotplan}'",
            "returnGeometry": "true",
            "f": "geojson",
        },
        timeout=15,
    ).json()

    feats = js.get("features", [])
    if not feats:
        return None

    polys = []
    for f in feats:
        geom = f["geometry"]
        wkid = geom.get("spatialReference", {}).get("wkid", 4326)
        g = shape(geom)
        if wkid != 4326:
            tfm = Transformer.from_crs(wkid, 4326, always_xy=True)
            g = transform(tfm.transform, g)
        polys.append(g)
    return unary_union(polys)


def hex_opacity_to_rgba(hex_rgb: str, opacity_pct: int):
    r = int(hex_rgb[1:3], 16)
    g = int(hex_rgb[3:5], 16)
    b = int(hex_rgb[5:7], 16)
    a = int(round(255 * opacity_pct / 100))
    return [r, g, b, a]


def kml_colour(hex_rgb: str, opacity_pct: int):
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    a = int(round(255 * opacity_pct / 100))
    return f"{a:02x}{b}{g}{r}"  # KML = aabbggrr


# ------------------------------------------------------------------
#  Streamlit page config
# ------------------------------------------------------------------
st.set_page_config(
    page_title="Lot/Plan → KML",
    layout="wide",
)

# ------------------------------------------------------------------
#  Layout: map (left) | controls (right)
# ------------------------------------------------------------------
col_map, col_ctrl = st.columns([3, 1], gap="large")

# ---------- controls box ----------
with col_ctrl:
    st.markdown("### Parcel search")
    ctrl = st.container(border=True)

with ctrl:
    lot_text = st.text_area(
        "Paste Lot/Plan IDs (one per line)",
        height=150,
        placeholder="6RP702264\n5//DP123456",
    )

    poly_hex = st.color_picker("Fill colour", "#ff6600")
    poly_opacity = st.number_input(
        "Fill opacity %", 0, 100, 70, help="0 = transparent, 100 = opaque"
    )
    line_hex = st.color_picker("Outline colour", "#2e2e2e")
    line_width = st.number_input("Line width (px)", 0.1, 10.0, 1.2, step=0.1)
    folder_name = st.text_input("Folder name inside KML", "Parcels")

    do_search = st.button("🔍 Search lots", use_container_width=True)

# ---------- run search ----------
if do_search and lot_text.strip():
    lot_ids = [lp.strip() for lp in lot_text.splitlines() if lp.strip()]
    lot_geoms, missing = {}, []

    with st.spinner("Fetching parcels…"):
        for lp in lot_ids:
            g = fetch_merged_geom(lp)
            (lot_geoms if g else missing.append(lp)) and (lot_geoms.update({lp: g}) if g else None)

    if missing:
        st.warning(f"No parcel found for: {', '.join(missing)}")

    # store in session for map + download
    st.session_state["lot_geoms"] = lot_geoms
    st.session_state["style"] = {
        "fill_hex": poly_hex,
        "fill_opacity": poly_opacity,
        "line_hex": line_hex,
        "line_width": line_width,
        "folder": folder_name or "Parcels",
    }

# ---------- build map ----------
with col_map:
    st.markdown("### Preview")
    base_view = pdk.ViewState(latitude=-25, longitude=145, zoom=4)

    layers = []
    if "lot_geoms" in st.session_state and st.session_state["lot_geoms"]:
        feats = [
            {
                "type": "Feature",
                "geometry": mapping(g),
                "properties": {"lp": lp},
            }
            for lp, g in st.session_state["lot_geoms"].items()
        ]
        geojson = {"type": "FeatureCollection", "features": feats}

        style = st.session_state["style"]
        layers.append(
            pdk.Layer(
                "GeoJsonLayer",
                geojson,
                get_fill_color=hex_opacity_to_rgba(
                    style["fill_hex"], style["fill_opacity"]
                ),
                get_line_color=hex_opacity_to_rgba(style["line_hex"], 100),
                line_width_min_pixels=style["line_width"],
                pickable=True,
                auto_highlight=True,
            )
        )

    deck = pdk.Deck(
        layers=layers,
        initial_view_state=base_view,
        map_style="mapbox://styles/mapbox/outdoors-v12",
        mapbox_key=MAPBOX_TOKEN,
        tooltip={"html": "<b>{lp}</b>", "style": {"color": "white"}},
    )
    st.pydeck_chart(deck, use_container_width=True)

# ---------- download button ----------
if (
    "lot_geoms" in st.session_state
    and st.session_state["lot_geoms"]
    and col_ctrl.button("📥 Download KML", use_container_width=True)
):
    style = st.session_state["style"]
    fill_kml = kml_colour(style["fill_hex"], style["fill_opacity"])
    line_kml = kml_colour(style["line_hex"], 100)

    kml = simplekml.Kml()
    parent = kml.newfolder(name=style["folder"])

    for lp, geom in st.session_state["lot_geoms"].items():
        coords = mapping(geom)["coordinates"][0]
        p = parent.newpolygon(name=lp, outerboundaryis=coords)
        p.style.polystyle.color = fill_kml
        p.style.linestyle.color = line_kml
        p.style.linestyle.width = float(style["line_width"])

    st.download_button(
        "Save KML",
        data=io.BytesIO(kml.kml().encode("utf-8")).getvalue(),
        file_name="parcels.kml",
        mime="application/vnd.google-earth.kml+xml",
        use_container_width=True,
    )
