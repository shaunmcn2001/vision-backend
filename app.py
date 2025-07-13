#!/usr/bin/env python3
# app.py — Streamlit Parcel Toolkit (QLD / NSW)

import streamlit as st
import folium
from folium.plugins import MousePosition
from streamlit_folium import st_folium
from streamlit_option_menu import option_menu
import pandas as pd
import requests, io, tempfile, zipfile, os
import simplekml
import geopandas as gpd
from shapely.geometry import shape
from pyproj import Geod

# ─── Page Config & CSS ───────────────────────────────────
st.set_page_config(
    page_title="Parcel Toolkit",
    page_icon="📍",
    layout="wide",
    initial_sidebar_state="collapsed"
)
st.markdown("""
<style>
#MainMenu, footer, header {visibility:hidden !important;}
div.block-container {padding:0 1rem !important;}
.leaflet-control-layers-list {
    background:rgba(40,40,40,0.8)!important;
    color:#fafafa!important;
}
</style>
""", unsafe_allow_html=True)

# ─── Session State Defaults ──────────────────────────────
if "features_qld" not in st.session_state: st.session_state.features_qld = []
if "features_nsw" not in st.session_state: st.session_state.features_nsw = []
if "results_df" not in st.session_state:
    st.session_state.results_df = pd.DataFrame(
        columns=["Parcel ID","State","Locality","Area (m²)"]
    )
if "style_fill" not in st.session_state:    st.session_state.style_fill = "#009FDF"
if "style_opacity" not in st.session_state: st.session_state.style_opacity = 40
if "style_weight" not in st.session_state:  st.session_state.style_weight = 3
if "panel_expanded" not in st.session_state: st.session_state.panel_expanded = True

# ─── Panel Toggle Button ─────────────────────────────────
if st.button("Hide Panel" if st.session_state.panel_expanded else "Show Panel"):
    st.session_state.panel_expanded = not st.session_state.panel_expanded

# ─── Layout ───────────────────────────────────────────────
if st.session_state.panel_expanded:
    map_col, panel_col = st.columns([3,1])
else:
    map_col = st.container()
    panel_col = None

# ─── Helper to zip shapefile ──────────────────────────────
def gdf_to_shp_zip(gdf: gpd.GeoDataFrame):
    tmpdir = tempfile.mkdtemp()
    shp_path = os.path.join(tmpdir, "parcels.shp")
    gdf.to_file(shp_path, driver="ESRI Shapefile")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fname in os.listdir(tmpdir):
            zf.write(os.path.join(tmpdir, fname), arcname=fname)
    return buf.getvalue()

# ─── Side Panel (only if expanded) ───────────────────────
if panel_col:
    with panel_col:
        st.subheader("Lot/Plan Search")
        lot_input = st.text_area(
            "Enter IDs (one per line)",
            height=140,
            placeholder="e.g.\n6RP702264\n5//15006\n5/1/1000"
        )
        if st.button("🔍 Search Parcels", use_container_width=True):
            ids = [l.strip() for l in lot_input.splitlines() if l.strip()]
            qld_ids, nsw_ids = [], []
            for idv in ids:
                parts = idv.split("/")
                if len(parts) == 3:
                    nsw_ids.append(f"{parts[0]}//{parts[2]}")
                elif len(parts) == 2:
                    plan = parts[1].upper()
                    if plan.startswith(("DP","SP","PP")):
                        nsw_ids.append(f"{parts[0]}//{plan}")
                    else:
                        qld_ids.append(parts[0] + plan)
                else:
                    qld_ids.append(idv.upper())
            qld_ids = list(dict.fromkeys(qld_ids))
            nsw_ids = list(dict.fromkeys(nsw_ids))

            # QLD query
            if qld_ids:
                qld_url = (
                    "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
                    "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query"
                )
                where = " OR ".join([f"lotplan='{i}'" for i in qld_ids])
                params = {"where": where,
                          "outFields":"lotplan,locality,lot_area",
                          "f":"geojson","outSR":"4326"}
                try:
                    resp = requests.get(qld_url, params=params, timeout=12).json()
                    st.session_state.features_qld = resp.get("features", [])
                except:
                    st.error("QLD query failed.")
            # NSW query
            if nsw_ids:
                nsw_url = (
                    "https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
                    "NSW_Cadastre/MapServer/9/query"
                )
                where = " OR ".join([f"lotidstring='{i}'" for i in nsw_ids])
                params = {"where":where,
                          "outFields":"lotidstring,planlotarea",
                          "f":"geojson","outSR":"4326"}
                try:
                    resp = requests.get(nsw_url, params=params, timeout=12).json()
                    st.session_state.features_nsw = resp.get("features", [])
                except:
                    st.error("NSW query failed.")

            # Build results DataFrame
            recs = []
            for feat in st.session_state.features_qld:
                p = feat["properties"]
                recs.append({
                    "Parcel ID": p.get("lotplan",""),
                    "State":      "QLD",
                    "Locality":   p.get("locality",""),
                    "Area (m²)":  p.get("lot_area", None)
                })
            for feat in st.session_state.features_nsw:
                p = feat["properties"]
                recs.append({
                    "Parcel ID": p.get("lotidstring",""),
                    "State":      "NSW",
                    "Locality":   "",
                    "Area (m²)":  p.get("planlotarea", None)
                })
            st.session_state.results_df = pd.DataFrame(recs)

        # Style & Results Section
        df = st.session_state.results_df
        if not df.empty:
            st.markdown("**Style Settings**")
            c1, c2, c3 = st.columns(3)
            fill = c1.color_picker("Fill Color",    st.session_state.style_fill)
            op   = c2.slider("Opacity (%)", 0,100,  st.session_state.style_opacity)
            wt   = c3.slider("Outline (px)",1,10,   st.session_state.style_weight)
            st.session_state.style_fill    = fill
            st.session_state.style_opacity = op
            st.session_state.style_weight  = wt

            st.markdown("**Search Results**")
            from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
            gb = GridOptionsBuilder.from_dataframe(df)
            gb.configure_pagination(True)
            gb.configure_selection("multiple", use_checkbox=True)
            gb.configure_default_column(filter=True, sortable=True)
            grid = AgGrid(
                df, 
                gb.build(), 
                update_mode=GridUpdateMode.MODEL_CHANGED, 
                theme="balham-dark"
            )
            selected = grid["selected_rows"]

            b1, b2, b3, b4, b5 = st.columns(5)
            # Zoom to Selected
            if b1.button("Zoom to Selected"):
                lats, lons = [], []
                for r in selected:
                    pid = r["Parcel ID"]
                    for feat in st.session_state.features_qld + st.session_state.features_nsw:
                        props = feat["properties"]
                        key = props.get("lotplan", props.get("lotidstring"))
                        if key == pid:
                            geom = feat["geometry"]
                            coords = (
                                geom["coordinates"][0]
                                if geom["type"] == "Polygon"
                                else geom["coordinates"][0][0]
                            )
                            for lon, lat in coords:
                                lons.append(lon)
                                lats.append(lat)
                if lats and lons:
                    sw, ne = [min(lats), min(lons)], [max(lats), max(lons)]
                    m.fit_bounds([sw, ne])

            # Export Selected KML
            if b2.button("Export Selected KML"):
                kml = simplekml.Kml()
                ah = f"{int(op/100*255):02x}"
                hc = fill.lstrip("#")
                kcol = ah + hc[4:6] + hc[2:4] + hc[0:2]
                for r in selected:
                    pid = r["Parcel ID"]
                    for feat in st.session_state.features_qld + st.session_state.features_nsw:
                        props = feat["properties"]
                        key = props.get("lotplan", props.get("lotidstring"))
                        if key == pid:
                            poly = kml.newpolygon(name=pid)
                            g = feat["geometry"]
                            if g["type"] == "Polygon":
                                poly.outerboundaryis = g["coordinates"][0]
                            poly.style.polystyle.color   = kcol
                            poly.style.linestyle.color   = kcol
                            poly.style.linestyle.width   = wt
                data = kml.kml().encode("utf-8")
                b2.download_button("⬇️ KML", data, "selected.kml", "application/vnd.google-earth.kml+xml")

            # Export Selected SHP
            if b3.button("Export Selected SHP"):
                rows, geoms = [], []
                for r in selected:
                    pid, stt = r["Parcel ID"], r["State"]
                    for feat in st.session_state.features_qld + st.session_state.features_nsw:
                        props = feat["properties"]
                        key = props.get("lotplan", props.get("lotidstring"))
                        if key == pid:
                            rows.append({"Parcel ID": pid, "State": stt})
                            geoms.append(shape(feat["geometry"]))
                gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
                zip_bytes = gdf_to_shp_zip(gdf)
                b3.download_button("⬇️ SHP", zip_bytes, "selected.zip", "application/zip")

            # Export All KML
            if b4.button("Export All KML"):
                kml = simplekml.Kml()
                ah = f"{int(op/100*255):02x}"
                hc = fill.lstrip("#")
                kcol = ah + hc[4:6] + hc[2:4] + hc[0:2]
                for feat in st.session_state.features_qld + st.session_state.features_nsw:
                    pid = feat["properties"].get("lotplan", feat["properties"].get("lotidstring",""))
                    poly = kml.newpolygon(name=pid)
                    g = feat["geometry"]
                    if g["type"] == "Polygon":
                        poly.outerboundaryis = g["coordinates"][0]
                    poly.style.polystyle.color   = kcol
                    poly.style.linestyle.color   = kcol
                    poly.style.linestyle.width   = wt
                data = kml.kml().encode("utf-8")
                b4.download_button("⬇️ KML", data, "all_parcels.kml", "application/vnd.google-earth.kml+xml")

            # Export All SHP
            if b5.button("Export All SHP"):
                rows, geoms = [], []
                for feat in st.session_state.features_qld + st.session_state.features_nsw:
                    props = feat["properties"]
                    pid = props.get("lotplan", props.get("lotidstring",""))
                    stt = "QLD" if feat in st.session_state.features_qld else "NSW"
                    rows.append({"Parcel ID": pid, "State": stt})
                    geoms.append(shape(feat["geometry"]))
                gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
                zip_bytes = gdf_to_shp_zip(gdf)
                b5.download_button("⬇️ SHP", zip_bytes, "all_parcels.zip", "application/zip")

# ─── Map Rendering ───────────────────────────────────────
with map_col:
    m = folium.Map(location=[-25,145], zoom_start=6, control_scale=True)
    # Basemaps
    folium.TileLayer("OpenStreetMap",      name="OSM",         overlay=False).add_to(m)
    folium.TileLayer("CartoDB dark_matter",name="Carto Dark",  overlay=False).add_to(m)
    folium.TileLayer(
        tiles="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
        attr="Google Satellite",
        name="Google Satellite",
        overlay=False,
        control=True
    ).add_to(m)
    # Mouse coords & layers
    MousePosition().add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    # Draw parcels
    style_fn = lambda feat: {
        "fillColor":   st.session_state.style_fill,
        "color":       st.session_state.style_fill,
        "fillOpacity": st.session_state.style_opacity/100,
        "weight":      st.session_state.style_weight
    }
    for feat in st.session_state.features_qld + st.session_state.features_nsw:
        folium.GeoJson(feat, style_function=style_fn).add_to(m)

    st_folium(m, width="100%", height=700)
