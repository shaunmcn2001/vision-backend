#!/usr/bin/env python3
# app.py — LAWD Parcel Toolkit

import streamlit as st
import folium
from streamlit_option_menu import option_menu
from streamlit_folium import st_folium
import geopandas as gpd
import pandas as pd
import numpy as np
import requests, json
import io, tempfile, zipfile, os
import simplekml
from shapely.geometry import shape, mapping
from shapely.ops import unary_union
from pyproj import Geod

# ─── Page & CSS Setup ───────────────────────────────────
st.set_page_config(
    page_title="Parcel Toolkit",
    page_icon="📍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# inject our custom CSS
with open("style.css") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# ─── REST Endpoints ─────────────────────────────────────
QLD_URL = (
    "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
    "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query"
)
NSW_URL = (
    "https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
    "NSW_Cadastre/MapServer/9/query"
)
geod = Geod(ellps="WGS84")

def fetch_parcels(ids):
    """
    Fetch parcels for the list of ID strings.
    QLD uses `lotplan='{ID}'`, NSW uses `lotidstring='{ID}'`.
    Returns dict {ID: {"geom": shapely_geom, "props": dict}} and a list of not-found IDs.
    """
    out, miss = {}, []
    for lp in ids:
        fld = "lotplan" if not lp.count("/") >= 1 or "//" in lp or lp.upper().startswith(tuple("0123456789")) else "lotidstring"
        url = QLD_URL if fld=="lotplan" else NSW_URL
        where = f"{fld}='{lp.upper()}'"
        params = {
            "where": where,
            "outFields": "*",
            "returnGeometry": "true",
            "f": "geojson"
        }
        try:
            js = requests.get(url, params=params, timeout=12).json()
            feats = js.get("features", [])
            if not feats:
                miss.append(lp)
                continue
            # unify geometries
            geoms, props = [], {}
            for ft in feats:
                g = shape(ft["geometry"])
                geoms.append(g)
                props = ft["properties"]
            geom = unary_union(geoms)
            out[lp] = {"geom": geom, "props": props}
        except Exception:
            miss.append(lp)
    return out, miss

# ─── Session State Init ─────────────────────────────────
if "parcels" not in st.session_state:
    st.session_state.parcels = {}  # {ID: {...}}
if "results_df" not in st.session_state:
    st.session_state.results_df = pd.DataFrame(columns=["ID","Area (ha)"])
# default style controls
if "style_fill" not in st.session_state:
    st.session_state.style_fill = "#009FDF"
if "style_opacity" not in st.session_state:
    st.session_state.style_opacity = 40
if "style_width" not in st.session_state:
    st.session_state.style_width = 3

# ─── Sidebar Menu ──────────────────────────────────────
with st.sidebar:
    choice = option_menu(
        None,
        ["Query", "Layers"],
        icons=["search","layers"],
        default_index=0,
        orientation="vertical",
        styles={
            "container": {"padding":"0","background-color":"#2b3035"},
            "icon": {"color":"#fafafa","font-size":"18px"},
            "nav-link": {"color":"#fafafa","text-align":"left","padding":"10px"},
            "nav-link-selected": {"background-color":"#f48020","color":"#fafafa"}
        },
    )

    if choice == "Query":
        st.subheader("Lot/Plan Query")
        ids_txt = st.text_area(
            "Enter Lot/Plan IDs (one per line)",
            placeholder="6RP702264\n5//15006"
        )
        if st.button("🔍 Search Parcels", use_container_width=True):
            ids = [s.strip() for s in ids_txt.splitlines() if s.strip()]
            recs, miss = fetch_parcels(ids)
            if miss:
                st.warning("Not found: " + ", ".join(miss))
            st.session_state.parcels = recs
            # build results DataFrame
            rows = []
            for lp, rec in recs.items():
                area = abs(geod.geometry_area_perimeter(rec["geom"])[0]) / 1e4
                rows.append({"ID": lp, "Area (ha)": round(area,2)})
            st.session_state.results_df = pd.DataFrame(rows)

    elif choice == "Layers":
        st.subheader("Basemaps")
        bm = st.radio("Select basemap",
                      ["OpenStreetMap","Google Satellite","Carto Dark"])
        st.session_state.basemap = bm
        st.subheader("Overlays")
        st.session_state.qld_wms = st.checkbox("QLD Parcels (WMS)", value=False)
        st.session_state.nsw_wms = st.checkbox("NSW Parcels (WMS)", value=False)

# ─── Build Folium Map ──────────────────────────────────
# default center
m = folium.Map(location=[-25,145], zoom_start=6, control_scale=True)

# add basemaps
if st.session_state.get("basemap","OpenStreetMap")=="OpenStreetMap":
    folium.TileLayer("OpenStreetMap", name="OSM", control=True, overlay=False).add_to(m)
elif st.session_state.basemap=="Google Satellite":
    folium.TileLayer(
        tiles="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
        attr="Google Satellite",
        name="Google Satellite",
        overlay=False, control=True
    ).add_to(m)
else:
    folium.TileLayer(
        tiles="CartoDB dark_matter",
        attr="CartoDB",
        name="Carto Dark",
        overlay=False, control=True
    ).add_to(m)

# add WMS overlays
if st.session_state.get("qld_wms",False):
    folium.raster_layers.WmsTileLayer(
        url=QLD_URL.replace("/query","/WMSServer"),
        layers="4",  # layer ID
        fmt="image/png",
        transparent=True,
        name="QLD Cadastre",
        control=True,
        attr="QLD Gov",
        version="1.1.1"
    ).add_to(m)
if st.session_state.get("nsw_wms",False):
    folium.raster_layers.WmsTileLayer(
        url=NSW_URL.replace("/query","/WMSServer"),
        layers="9",
        fmt="image/png",
        transparent=True,
        name="NSW Cadastre",
        control=True,
        attr="NSW",
        version="1.1.1"
    ).add_to(m)

# add searched parcels
if st.session_state.parcels:
    style = lambda feat: {
        "fillColor": st.session_state.style_fill,
        "color": st.session_state.style_fill,
        "weight": st.session_state.style_width,
        "fillOpacity": st.session_state.style_opacity/100
    }
    fg = folium.FeatureGroup(name="Parcels", show=True)
    for lp, rec in st.session_state.parcels.items():
        gj = folium.GeoJson(
            data=mapping(rec["geom"]),
            name=lp,
            style_function=style,
            tooltip=lp
        )
        gj.add_to(fg)
    fg.add_to(m)
    # auto-zoom to all parcels
    all_bounds = [rec["geom"].bounds for rec in st.session_state.parcels.values()]
    lons = [b[0] for b in all_bounds] + [b[2] for b in all_bounds]
    lats = [b[1] for b in all_bounds] + [b[3] for b in all_bounds]
    m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])

# layer control
folium.LayerControl(collapsed=False).add_to(m)

# render map
st_data = st_folium(m, width=0, height=600)

# ─── Results & Export Panel ───────────────────────────
with st.expander("Results & Export", expanded=True):
    df = st.session_state.results_df
    if df.empty:
        st.write("No parcels loaded yet.")
    else:
        st.dataframe(df, use_container_width=True)
        # per-row Zoom buttons
        for i, row in df.iterrows():
            c1, c2, c3 = st.columns([3,1,1])
            c1.write(f"**{row['ID']}** — {row['Area (ha)']} ha")
            if c2.button("🔍 Zoom", key=f"zoom{i}"):
                geom = st.session_state.parcels[row["ID"]]["geom"]
                b = geom.bounds  # (minx,miny,maxx,maxy)
                m.fit_bounds([[b[1],b[0]],[b[3],b[2]]])
                st_folium(m, width=0, height=600)
            # single-parcel export (GeoJSON)
            single_geojson = json.dumps({
                "type":"FeatureCollection",
                "features":[{
                    "type":"Feature",
                    "properties":{},
                    "geometry":mapping(st.session_state.parcels[row["ID"]]["geom"])
                }]
            }).encode("utf-8")
            c3.download_button(
                "💾 Export",
                data=single_geojson,
                file_name=f"{row['ID']}.geojson",
                mime="application/geo+json",
                key=f"exp{i}"
            )

        st.markdown("---")
        st.write("### Bulk export")
        # style controls
        col1, col2, col3 = st.columns(3)
        fill = col1.color_picker("Fill Color", st.session_state.style_fill)
        op = col2.slider("Opacity %", 0, 100, st.session_state.style_opacity)
        wd = col3.number_input("Outline px", 0, 10, st.session_state.style_width)
        # persist style changes
        st.session_state.style_fill = fill
        st.session_state.style_opacity = op
        st.session_state.style_width = wd

        # Export All KML
        kml = simplekml.Kml()
        alpha = format(int(op/100*255),"02x")
        # KML color AABBGGRR
        rgb = fill.lstrip("#")
        r, g, b = rgb[0:2], rgb[2:4], rgb[4:6]
        kml_col = alpha + b + g + r
        for lp, rec in st.session_state.parcels.items():
            g = rec["geom"]
            polys = [g] if g.geom_type=="Polygon" else list(g.geoms)
            for poly in polys:
                coords = list(poly.exterior.coords)
                pol = kml.newpolygon(name=lp, outerboundaryis=coords)
                pol.style.polystyle.color = kml_col
                pol.style.linestyle.color = kml_col
                pol.style.linestyle.width = wd
        kml_bytes = io.BytesIO(kml.kml().encode("utf-8")).getvalue()
        st.download_button("💾 Export All (KML)", kml_bytes, "parcels.kml", "application/vnd.google-earth.kml+xml")

        # Export All Shapefile (ZIP)
        tmp = tempfile.TemporaryDirectory()
        shp_dir = tmp.name
        gdf = gpd.GeoDataFrame(df.assign(geometry=[st.session_state.parcels[id]["geom"] for id in df["ID"]]),
                               crs="EPSG:4326")
        gdf.to_file(os.path.join(shp_dir,"parcels.shp"))
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf,"w") as zf:
            for ext in [".shp",".shx",".dbf",".prj"]:
                p = os.path.join(shp_dir,f"parcels{ext}")
                if os.path.exists(p):
                    zf.write(p, f"parcels{ext}")
        st.download_button("💾 Export All (SHP)", zip_buf.getvalue(), "parcels.zip", "application/zip")
