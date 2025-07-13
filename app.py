#!/usr/bin/env python3
# app.py — Streamlit Parcel Toolkit (QLD / NSW)

import streamlit as st
import folium
from folium.plugins import MousePosition
from streamlit_folium import st_folium
from streamlit_option_menu import option_menu
import pandas as pd
import numpy as np
import requests, json, io, tempfile, zipfile, os
import simplekml
import shapefile
from shapely.geometry import shape, mapping
from pyproj import Geod

# ─── Page Config & CSS ───────────────────────────────────
st.set_page_config(
    page_title="Parcel Toolkit", page_icon="📍",
    layout="wide", initial_sidebar_state="collapsed"
)
st.markdown("""
<style>
/* Dark theme adjustments */
#MainMenu, footer, header {visibility:hidden !important;}
div.block-container {padding:0 1rem !important;}
/* Right-side panel CSS hack */
section[data-testid="stSidebar"] {left:unset !important; right:0 !important;}
/* Folium layer control dark background */
.leaflet-control-layers-list {background:rgba(40,40,40,0.8)!important;color:#fafafa!important;}
</style>
""", unsafe_allow_html=True)

# ─── Session State Init ─────────────────────────────────
if "features_qld" not in st.session_state: st.session_state.features_qld = []
if "features_nsw" not in st.session_state: st.session_state.features_nsw = []
if "results_df"  not in st.session_state:
    st.session_state.results_df = pd.DataFrame(
        columns=["Parcel ID","State","Locality","Area (m²)"]
    )

# Style defaults
if "style_fill"    not in st.session_state: st.session_state.style_fill = "#009FDF"
if "style_opacity" not in st.session_state: st.session_state.style_opacity = 40
if "style_weight"  not in st.session_state: st.session_state.style_weight = 3

# Panel toggle
if "panel_expanded" not in st.session_state: st.session_state.panel_expanded = True
toggle = st.button("Hide Panel" if st.session_state.panel_expanded else "Show Panel")
if toggle: st.session_state.panel_expanded = not st.session_state.panel_expanded

# ─── Layout Columns ──────────────────────────────────────
if st.session_state.panel_expanded:
    map_col, panel_col = st.columns([3,1])
else:
    map_col = st.container()
    panel_col = None

# ─── Panel Content ──────────────────────────────────────
with panel_col:
    st.subheader("Lot/Plan Search")
    lot_input = st.text_area(
        "Enter IDs (one per line)", height=140,
        placeholder="e.g.\n6RP702264\n5//15006\n5/1/1000"
    )
    search = st.button("🔍 Search Parcels", use_container_width=True)

    if search and lot_input.strip():
        ids = [l.strip() for l in lot_input.splitlines() if l.strip()]
        qld_ids, nsw_ids = [], []
        # classify IDs
        for idv in ids:
            parts = idv.split("/")
            if len(parts)==3: nsw_ids.append(f"{parts[0]}//{parts[2]}")
            elif len(parts)==2:
                plan = parts[1].upper()
                if plan.startswith(("DP","SP","PP")):
                    nsw_ids.append(f"{parts[0]}//{plan}")
                else:
                    qld_ids.append(parts[0]+plan)
            else:
                qld_ids.append(idv.upper())
        qld_ids = list(dict.fromkeys(qld_ids))
        nsw_ids = list(dict.fromkeys(nsw_ids))

        # fetch QLD
        if qld_ids:
            qld_url = ("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
                       "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
            where = " OR ".join([f"lotplan='{i}'" for i in qld_ids])
            p = {"where":where,"outFields":"lotplan,locality,lot_area","f":"geojson","outSR":"4326"}
            try:
                r = requests.get(qld_url, params=p, timeout=12).json()
                st.session_state.features_qld = r.get("features",[])
            except: st.error("QLD query failed.")
        # fetch NSW
        if nsw_ids:
            nsw_url = ("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
                       "NSW_Cadastre/MapServer/9/query")
            where = " OR ".join([f"lotidstring='{i}'" for i in nsw_ids])
            p = {"where":where,"outFields":"lotidstring,planlotarea","f":"geojson","outSR":"4326"}
            try:
                r = requests.get(nsw_url, params=p, timeout=12).json()
                st.session_state.features_nsw = r.get("features",[])
            except: st.error("NSW query failed.")

        # build results_df
        recs = []
        for f in st.session_state.features_qld:
            pr = f["properties"]
            recs.append({
                "Parcel ID": pr.get("lotplan",""),
                "State": "QLD",
                "Locality": pr.get("locality",""),
                "Area (m²)": pr.get("lot_area",None)
            })
        for f in st.session_state.features_nsw:
            pr = f["properties"]
            recs.append({
                "Parcel ID": pr.get("lotidstring",""),
                "State": "NSW",
                "Locality": "",
                "Area (m²)": pr.get("planlotarea",None)
            })
        st.session_state.results_df = pd.DataFrame(recs)

    # style controls & results
    df = st.session_state.results_df
    if not df.empty:
        st.markdown("**Style Settings**")
        c1,c2,c3 = st.columns(3)
        fill = c1.color_picker("Fill Color", st.session_state.style_fill)
        op   = c2.slider("Opacity %",0,100,st.session_state.style_opacity)
        wt   = c3.slider("Outline px",1,10,st.session_state.style_weight)
        st.session_state.style_fill, st.session_state.style_opacity, st.session_state.style_weight = fill,op,wt

        st.markdown("**Search Results**")
        from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
        gb = GridOptionsBuilder.from_dataframe(df)
        gb.configure_pagination(True)
        gb.configure_selection("multiple", use_checkbox=True)
        gb.configure_default_column(filter=True, sortable=True)
        grid = AgGrid(df, gb.build(), update_mode=GridUpdateMode.MODEL_CHANGED, theme="dark")

        selected = grid["selected_rows"]

        # action buttons
        b1,b2,b3,b4,b5 = st.columns(5)
        # Zoom
        if b1.button("Zoom to Selected"):
            lats,lons = [],[]
            for r in selected:
                pid = r["Parcel ID"]
                for f in st.session_state.features_qld+st.session_state.features_nsw:
                    pr=f["properties"]
                    if pr.get("lotplan",pr.get("lotidstring"))==pid:
                        geom=f["geometry"]
                        coords=[]
                        if geom["type"]=="Polygon":
                            coords=geom["coordinates"][0]
                        elif geom["type"]=="MultiPolygon":
                            coords=geom["coordinates"][0][0]
                        for lon,lat in coords:
                            lons.append(lon); lats.append(lat)
            if lats and lons:
                m.fit_bounds([[min(lats),min(lons)],[max(lats),max(lons)]])

        # Export Selected KML
        if b2.button("Export Selected KML"):
            kml = simplekml.Kml()
            op_hex = f"{int(op/100*255):02x}"
            rgb = fill.lstrip("#")
            kml_col = op_hex + rgb[4:6]+rgb[2:4]+rgb[0:2]
            for r in selected:
                pid=r["Parcel ID"]
                for f in st.session_state.features_qld+st.session_state.features_nsw:
                    pr=f["properties"]
                    if pr.get("lotplan",pr.get("lotidstring"))==pid:
                        g=f["geometry"]
                        pol=kml.newpolygon(name=pid)
                        if g["type"]=="Polygon":
                            pol.outerboundaryis=g["coordinates"][0]
                        kml_colrk=kml_col
                        pol.style.polystyle.color=kml_colrk
                        pol.style.linestyle.color=kml_colrk
                        pol.style.linestyle.width=wt
            kb=kml.kml().encode("utf-8")
            b2.download_button("⬇️",kb,"sel_parcels.kml","application/vnd.google-earth.kml+xml")

        # Export Selected SHP
        if b3.button("Export Selected SHP"):
            prj_wkt = """GEOGCS["WGS 84",DATUM["WGS_1984"],
SPHEROID["WGS 84",6378137,298.257223563]],
PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]"""
            shp_io=io.BytesIO(); zf=zipfile.ZipFile(shp_io,"w")
            w=shapefile.Writer("sel")
            w.field("PID","C"); w.field("ST","C")
            for r in selected:
                pid,stt=r["Parcel ID"],r["State"]
                for f in st.session_state.features_qld+st.session_state.features_nsw:
                    pr=f["properties"]
                    if pr.get("lotplan",pr.get("lotidstring"))==pid:
                        g=f["geometry"]
                        if g["type"]=="Polygon":
                            coords=[(x,y) for x,y in g["coordinates"][0]]
                            w.poly([coords])
                            w.record(pid,stt)
            w.close()
            for ext in ["shp","shx","dbf"]:
                zf.write(f"sel.{ext}",arcname=f"sel.{ext}")
            zf.writestr("sel.prj",prj_wkt)
            zf.close()
            shp_io.seek(0)
            b3.download_button("⬇️",shp_io.getvalue(),"sel_parcels.zip","application/zip")

        # Export All KML
        if b4.button("Export All KML"):
            kml= simplekml.Kml()
            op_hex = f"{int(op/100*255):02x}"; rgb=fill.lstrip("#")
            kcol=op_hex+rgb[4:6]+rgb[2:4]+rgb[0:2]
            for f in st.session_state.features_qld+st.session_state.features_nsw:
                pid=f["properties"].get("lotplan",f["properties"].get("lotidstring",""))
                g=f["geometry"]
                pol=kml.newpolygon(name=pid)
                if g["type"]=="Polygon": pol.outerboundaryis=g["coordinates"][0]
                pol.style.polystyle.color=kcol; pol.style.linestyle.color=kcol; pol.style.linestyle.width=wt
            kb=kml.kml().encode("utf-8")
            b4.download_button("⬇️",kb,"all_parcels.kml","application/vnd.google-earth.kml+xml")

        # Export All SHP
        if b5.button("Export All SHP"):
            prj_wkt = """GEOGCS["WGS 84",DATUM["WGS_1984"],
SPHEROID["WGS 84",6378137,298.257223563]],
PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]"""
            shp_io=io.BytesIO(); zf=zipfile.ZipFile(shp_io,"w")
            w=shapefile.Writer("allp")
            w.field("PID","C"); w.field("ST","C")
            for f in st.session_state.features_qld+st.session_state.features_nsw:
                pid=f["properties"].get("lotplan",f["properties"].get("lotidstring",""))
                stt="QLD" if f in st.session_state.features_qld else "NSW"
                g=f["geometry"]
                if g["type"]=="Polygon":
                    coords=[(x,y) for x,y in g["coordinates"][0]]
                    w.poly([coords]); w.record(pid,stt)
            w.close()
            for ext in ["shp","shx","dbf"]:
                zf.write(f"allp.{ext}",arcname=f"allp.{ext}")
            zf.writestr("allp.prj",prj_wkt)
            zf.close(); shp_io.seek(0)
            b5.download_button("⬇️",shp_io.getvalue(),"all_parcels.zip","application/zip")

# ─── Map Rendering ───────────────────────────────────────
with map_col:
    # initialize map
    m = folium.Map(location=[-25,145], zoom_start=6, control_scale=True)
    # basemaps
    folium.TileLayer("OpenStreetMap",name="OSM",overlay=False).add_to(m)
    folium.TileLayer("CartoDB dark_matter",name="Carto Dark",overlay=False).add_to(m)
    folium.TileLayer(
        tiles="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
        name="Google Satellite",overlay=False
    ).add_to(m)
    # mouse coords & layers
    MousePosition().add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    # add search results
    style_fn = lambda feat: {
        "fillColor": st.session_state.style_fill,
        "color": st.session_state.style_fill,
        "fillOpacity": st.session_state.style_opacity/100,
        "weight": st.session_state.style_weight
    }
    for f in st.session_state.features_qld+st.session_state.features_nsw:
        folium.GeoJson(f, style_function=style_fn).add_to(m)
    # render
    st_folium(m, width="100%", height=700)
