#!/usr/bin/env python3
# LAWD Parcel Toolkit · 2025-07-12

"""
Streamlit one-page app for parcel lookup & export.
─────────────────────────────────────────────────
• Compact sidebar (IDs + Style expander)
• Folium map with parcels layer
• Interactive AgGrid table under the map
    – Tick rows → Zoom / Export KML / Export SHP / Remove
• Export-ALL bar (KML + Shapefile)
"""

import io
import os
import re
import pathlib
import requests
import tempfile
import zipfile
import uuid
import shutil

import streamlit as st
from streamlit_option_menu import option_menu
from streamlit_folium import st_folium
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

import folium
import simplekml
import geopandas as gpd
import pandas as pd
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod

# ───────────────────── STATIC CONFIG ────────────────────────────────────
CFG = pathlib.Path("layers.yaml")
cfg = {}
try:
    import yaml
    if CFG.exists():
        cfg = yaml.safe_load(CFG.read_text()) or {}
except ImportError:
    cfg = {}
for k in ("basemaps", "overlays"):
    cfg.setdefault(k, [])

# ───────────────────── STREAMLIT SHELL ──────────────────────────────────
st.set_page_config("Lot/Plan Toolkit", "📍", layout="wide", initial_sidebar_state="collapsed")
st.markdown(
    "<div style='background:#ff6600;color:#fff;"
    "font-size:20px;font-weight:600;padding:6px 20px;"
    "border-radius:8px;margin-bottom:6px'>LAWD – Parcel Toolkit</div>",
    unsafe_allow_html=True,
)

with st.sidebar:
    tab = option_menu(
        None, ["Query", "Layers", "Downloads"],
        icons=["search", "layers", "download"], default_index=0,
        styles={
            "container": {"padding": "0", "background": "#262730"},
            "nav-link-selected": {"background": "#ff6600"},
        },
    )

# ───────────────────── SESSION DEFAULTS ────────────────────────────────
if cfg["basemaps"]:
    st.session_state.setdefault("basemap", cfg["basemaps"][0]["name"])
st.session_state.setdefault("ov_state", {o["name"]: False for o in cfg["overlays"]})

# ───────────────────── CADASTRE SERVICES ────────────────────────────────
QLD = (
    "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
    "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query"
)
NSW = (
    "https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
    "NSW_Cadastre/MapServer/9/query"
)

def fetch_parcels(ids):
    """Return dict{lp:{geom,props}} and list[missing]"""
    out, miss = {}, []
    for lp in ids:
        url, fld = (
            (QLD, "lotplan")
            if re.match(r"^\d+[A-Z]{1,3}\d+$", lp, re.I)
            else (NSW, "lotidstring")
        )
        try:
            js = requests.get(
                url,
                params={
                    "where": f"{fld}='{lp}'",
                    "outFields": "*",
                    "returnGeometry": "true",
                    "f": "geojson",
                },
                timeout=15
            ).json()
            feats = js.get("features", [])
            if not feats:
                miss.append(lp)
                continue
            wkid = feats[0]["geometry"].get("spatialReference", {}).get("wkid", 4326)
            tfm = (
                Transformer.from_crs(wkid, 4326, always_xy=True).transform
                if wkid != 4326 else None
            )
            geoms, props = [], {}
            for ft in feats:
                g = shape(ft["geometry"])
                geoms.append(transform(tfm, g) if tfm else g)
                props = ft["properties"]
            out[lp] = {"geom": unary_union(geoms), "props": props}
        except Exception:
            miss.append(lp)
    return out, miss

def kml_colour(hexrgb: str, pct: int):
    """Return KML AABBGGRR from #RRGGBB and opacity pct."""
    r, g, b = hexrgb[1:3], hexrgb[3:5], hexrgb[5:7]
    a = int(round(255 * pct / 100))
    return f"{a:02x}{b}{g}{r}"

g_geod = Geod(ellps="WGS84")

# ═════════════════════ TAB : QUERY ══════════════════════════════════════
if tab == "Query":
    ids_txt = st.sidebar.text_area(
        "Lot/Plan IDs", height=110,
        placeholder="6RP702264\n5//DP123456"
    )
    with st.sidebar.expander("Style & KML", expanded=False):
        c1, c2 = st.columns(2, gap="small")
        with c1:
            fx = st.color_picker("Fill", "#ff6600", label_visibility="collapsed")
            lx = st.color_picker("Outline", "#2e2e2e", label_visibility="collapsed")
        with c2:
            fo = st.slider("Opacity %", 0, 100, 70, label_visibility="collapsed")
            lw = st.slider("Width px", 0.5, 6.0, 1.2, 0.1, label_visibility="collapsed")
        folder = st.text_input("KML folder", "Parcels")

    if st.sidebar.button("🔍 Search", use_container_width=True) and ids_txt.strip():
        ids = [s.strip() for s in ids_txt.splitlines() if s.strip()]
        with st.spinner("Fetching parcels…"):
            recs, miss = fetch_parcels(ids)
        if miss:
            st.sidebar.warning("Not found: " + ", ".join(miss))

        rows = []
        for lp, rec in recs.items():
            props = rec["props"]
            lottype = props.get("lottype") or props.get("PURPOSE") or "n/a"
            area = abs(g_geod.geometry_area_perimeter(rec["geom"])[0]) / 1e4
            rows.append({
                "Lot/Plan": lp,
                "Lot Type": lottype,
                "Area (ha)": round(area, 2),
            })
        st.session_state["parcels"] = recs
        st.session_state["table"] = pd.DataFrame(rows)
        st.session_state["style"] = dict(fill=fx, op=fo, line=lx, w=lw, folder=folder)
        st.success(f"{len(recs)} parcel{'s'*(len(recs)!=1)} loaded.")

# ═════════════════════ TAB : LAYERS ═════════════════════════════════════
if tab == "Layers":
    if cfg["basemaps"]:
        st.sidebar.subheader("Basemap")
        names = [b["name"] for b in cfg["basemaps"]]
        st.session_state["basemap"] = st.sidebar.radio(
            "", names, index=names.index(st.session_state["basemap"])
        )
    st.sidebar.subheader("Static overlays")
    for o in cfg["overlays"]:
        st.session_state["ov_state"][o["name"]] = st.sidebar.checkbox(
            o["name"], value=st.session_state["ov_state"][o["name"]]
        )

# ═════════════════════ MAP BUILD ════════════════════════════════════════
map_obj = folium.Map(
    location=[-25, 145], zoom_start=5,
    control_scale=True, width="100%", height="100vh"
)
# Basemap
if cfg["basemaps"]:
    base = next(b for b in cfg["basemaps"] if b["name"] == st.session_state["basemap"])
    folium.TileLayer(
        base["url"], name=base["name"], attr=base["attr"],
        overlay=False, control=True, show=True
    ).add_to(map_obj)
# Overlays
for o in cfg["overlays"]:
    if st.session_state["ov_state"][o["name"]]:
        try:
            if o["type"] == "wms":
                folium.raster_layers.WmsTileLayer(
                    o["url"], layers=str(o["layers"]), transparent=True,
                    fmt=o.get("fmt", "image/png"), version="1.1.1",
                    name=o["name"], attr=o["attr"]
                ).add_to(map_obj)
            else:
                folium.TileLayer(o["url"], name=o["name"], attr=o["attr"]).add_to(map_obj)
        except Exception as e:
            st.warning(f"{o['name']} failed: {e}")

# Render map + capture JS events
folium_data = st_folium(
    map_obj,
    height=550,
    use_container_width=True,
    key="map",
    returned_objects=["bounds", "js_events"]
)
js = folium_data.get("js_events", [])[-1] if folium_data.get("js_events") else None

# ═══════════════════ RESULTS TABLE + ACTIONS ═════════════════════════════
if "table" in st.session_state and not st.session_state["table"].empty:
    st.subheader("Query Results")

    # Add ellipsis column for per-row actions
    df = st.session_state["table"].copy()
    df["⋮"] = "⋮"

    gdf = gpd.GeoDataFrame(
        df,
        geometry=[rec["geom"] for rec in st.session_state["parcels"].values()],
        crs=4326
    )
    gob = GridOptionsBuilder.from_dataframe(gdf.drop(columns="geometry"))
    gob.configure_selection("multiple", use_checkbox=True)
    gob.configure_column("⋮", header_name="", width=60,
                         cellRenderer="""(p)=>'<span style="cursor:pointer">⋮</span>'""",
                         suppressMenu=True)
    gob.configure_grid_options(
        getContextMenuItems="""function(p){
            return [
              'copy','separator',
              { name:'Zoom to result(s)', action:()=>window.postMessage({type:'zoom'}) },
              { name:'Export KML',         action:()=>window.postMessage({type:'kml'}) },
              { name:'Export SHP',         action:()=>window.postMessage({type:'shp'}) },
              'separator',
              { name:'Remove result(s)',   action:()=>window.postMessage({type:'remove'}) }
            ];
        }"""
    )
    grid = AgGrid(
        gdf.drop(columns="geometry"),
        gridOptions=gob.build(),
        update_mode=GridUpdateMode.MODEL_CHANGED,
        allow_unsafe_jscode=True,
        height=250,
    )

    # --- NEW robust selection handling ----------------------------------
    sel_rows = grid.get("selected_rows", []) if isinstance(grid, dict) else getattr(grid, "selected_rows", [])
    st.session_state["_sel"] = [
    r.get("Lot/Plan") or r.get("Lot_Plan")  # AgGrid replaces '/' with '_'
    for r in sel_rows
    ]

    # Export-ALL bar
    with st.expander("Export ALL", expanded=True):
        colA, colB = st.columns(2)
        if colA.button("Generate KML"):
            kml = simplekml.Kml()
            fld = kml.newfolder(name=st.session_state["style"]["folder"])
            fk = kml_colour(st.session_state["style"]["fill"], st.session_state["style"]["op"])
            lk = kml_colour(st.session_state["style"]["line"], 100)
            for lp, rec in st.session_state["parcels"].items():
                geom = rec["geom"]
                polys = [geom] if isinstance(geom, Polygon) else list(geom.geoms)
                for i, p in enumerate(polys, 1):
                    nm = f"{lp} ({i})" if len(polys) > 1 else lp
                    desc = f"Lot/Plan: {lp}<br>Area: {abs(g_geod.geometry_area_perimeter(p)[0]) / 1e4:,.2f} ha"
                    poly = fld.newpolygon(name=nm, description=desc, outerboundaryis=p.exterior.coords)
                    for ring in p.interiors:
                        poly.innerboundaryis.append(ring.coords)
                    poly.style.polystyle.color = fk
                    poly.style.linestyle.color = lk
                    poly.style.linestyle.width = float(st.session_state["style"]["w"])
            st.download_button("Download KML",
                               io.BytesIO(kml.kml().encode()),
                               "parcels.kml",
                               "application/vnd.google-earth.kml+xml")
        if colB.button("Generate SHP"):
            tmp = tempfile.mkdtemp()
            gpd.GeoDataFrame(
                st.session_state["table"],
                geometry=[rec["geom"] for rec in st.session_state["parcels"].values()],
                crs=4326
            ).to_file(tmp + "/all.shp")
            zpath = pathlib.Path(tmp, "all.zip")
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in pathlib.Path(tmp).glob("all.*"):
                    zf.write(f, f.name)
            st.download_button("Download SHP",
                               open(zpath, "rb"),
                               "parcels.zip",
                               "application/zip")

    # Handle context-menu events
    if js and js.get("type") in {"zoom", "kml"}:
        ids = [
            r.get("Lot/Plan") or r.get("Lot_Plan")
            for r in sel_rows
        ] or st.session_state.get("_sel", [])
        geoms = [st.session_state["parcels"][i]["geom"] for i in ids]

        if js["type"] == "zoom":
            bb = gpd.GeoSeries(geoms).total_bounds
            st.session_state["__zoom"] = [[bb[1], bb[0]], [bb[3], bb[2]]]
            st.experimental_rerun()

        elif js["type"] == "kml":
            kml = simplekml.Kml()
            fk = kml_colour(st.session_state["style"]["fill"], st.session_state["style"]["op"])
            lk = kml_colour(st.session_state["style"]["line"], 100)
            for lp in ids:
                geom = st.session_state["parcels"][lp]["geom"]
                poly = kml.newpolygon(name=lp,
                                      outerboundaryis=geom.exterior.coords if isinstance(geom, Polygon)
                                      else list(geom.geoms)[0].exterior.coords)
                poly.style.polystyle.color = fk
                poly.style.linestyle.color = lk
                poly.style.linestyle.width = float(st.session_state["style"]["w"])
            st.download_button("Download Selected KML",
                               io.BytesIO(kml.kml().encode()),
                               "selected.kml",
                               "application/vnd.google-earth.kml+xml")

# Honor zoom request (if any)
if "__zoom" in st.session_state:
    map_obj.fit_bounds(st.session_state.pop("__zoom"))
