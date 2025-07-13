#!/usr/bin/env python3
# LAWD Parcel Toolkit · 2025-07

import io, pathlib, requests, tempfile, zipfile, re
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
from streamlit_folium import st_folium
import folium, simplekml, geopandas as gpd, pandas as pd
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod

# ─── Static config ─────────────────────────────────────
CFG = pathlib.Path("layers.yaml")
try:
    import yaml
    cfg = yaml.safe_load(CFG.read_text()) if CFG.exists() else {}
except ImportError:
    cfg = {}
for k in ("basemaps","overlays"):
    cfg.setdefault(k, [])

# ─── Page setup & tabs ─────────────────────────────────
st.set_page_config("LAWD Parcel Toolkit", "📍", layout="wide")
tab1, tab2, tab3 = st.tabs(["🔍 Query", "🗺 Layers", "💾 Downloads"])

# initialize state
if cfg["basemaps"]:
    st.session_state.setdefault("basemap", cfg["basemaps"][0]["name"])
st.session_state.setdefault("ov_state", {o["name"]:False for o in cfg["overlays"]})

GEOD = Geod(ellps="WGS84")

def fetch_parcels(ids):
    QLD = "https://spatial-gis.information.qld.gov.au/.../4/query"
    NSW = "https://maps.six.nsw.gov.au/.../9/query"
    out, miss = {}, []
    for lp in ids:
        url,fld = (QLD,"lotplan") if re.match(r"^\d+[A-Z]{1,3}\d+$",lp,re.I) else (NSW,"lotidstring")
        try:
            js = requests.get(url, params={
              "where":f"{fld}='{lp}'","outFields":"*",
              "returnGeometry":"true","f":"geojson"
            }, timeout=12).json()
            feats = js.get("features",[])
            if not feats: miss.append(lp); continue
            wkid = feats[0]["geometry"].get("spatialReference",{}).get("wkid",4326)
            tfm = Transformer.from_crs(wkid,4326,always_xy=True).transform if wkid!=4326 else None
            polys, props = [], {}
            for ft in feats:
                g = shape(ft["geometry"])
                polys.append(transform(tfm,g) if tfm else g)
                props = ft["properties"]
            out[lp] = {"geom":unary_union(polys),"props":props}
        except:
            miss.append(lp)
    return out, miss

def kml_colour(h,pct):
    r,g,b = h[1:3],h[3:5],h[5:7]
    a = int(round(255*pct/100))
    return f"{a:02x}{b}{g}{r}"

# ─── QUERY TAB ─────────────────────────────────────────
with tab1:
    st.header("Lot/Plan Lookup")

    # --- Search & style on one row ---
    c1, c2, c3, c4 = st.columns([2,1,1,1], gap="small")
    with c1:
        ids_txt = st.text_area("IDs (one per line)", height=80,
                               placeholder="6RP702264\n5//DP123456")
    with c2:
        fx = st.color_picker("Fill", "#ff6600")
    with c3:
        fo = st.slider("Opacity", 0, 100, 70)
    with c4:
        folder = st.text_input("KML folder", "Parcels")

    if st.button("🔍 Search Parcels"):
        ids = [s.strip() for s in ids_txt.splitlines() if s.strip()]
        recs, miss = fetch_parcels(ids)
        if miss: st.warning("Not found: " + ", ".join(miss))
        rows = []
        for lp,rec in recs.items():
            a = abs(GEOD.geometry_area_perimeter(rec["geom"])[0]) / 1e4
            rows.append({"Lot/Plan":lp, "Area (ha)":round(a,2)})
        st.session_state.update(parcels=recs, table=pd.DataFrame(rows),
                                style=dict(fill=fx,op=fo,folder=folder))
        st.success(f"{len(recs)} loaded")

    # --- Full-width map ---
    m = folium.Map(location=[-25,145], zoom_start=5,
                   width="100%", height="60vh", control_scale=True)
    if "parcels" in st.session_state:
        s = st.session_state["style"]
        def sty(f): return {"fillColor":s["fill"],"fillOpacity":s["op"]/100,"color":"#2e2e2e","weight":1}
        fg = folium.FeatureGroup("Parcels",show=True).add_to(m)
        for lp,rec in st.session_state["parcels"].items():
            folium.GeoJson(mapping(rec["geom"]), style_function=sty, tooltip=lp).add_to(fg)
    st_folium(m, key="map")

    # --- Results & actions in expander ---
    if "table" in st.session_state and not st.session_state["table"].empty:
        with st.expander("📊 Results & Actions", expanded=True):
            df = st.session_state["table"]
            gob = GridOptionsBuilder.from_dataframe(df)
            gob.configure_selection("multiple",use_checkbox=True)
            grid = AgGrid(df, gridOptions=gob.build(),
                          update_mode=GridUpdateMode.SELECTION_CHANGED,
                          theme="streamlit")
            sel = [r["Lot/Plan"] for r in grid["selected_rows"]]
            b1,b2,b3 = st.columns(3)
            with b1:
                if st.button("🔍 Zoom", disabled=not sel):
                    bb = gpd.GeoSeries([st.session_state["parcels"][i]["geom"] for i in sel]).total_bounds
                    m.fit_bounds([[bb[1],bb[0]],[bb[3],bb[2]]])
                    st_folium(m, key="map2")
            with b2:
                if st.button("💾 Export KML", disabled=not sel):
                    k = simplekml.Kml()
                    for lp in sel:
                        g = st.session_state["parcels"][lp]["geom"]
                        poly = k.newpolygon(name=lp,outerboundaryis=g.exterior.coords)
                    st.download_button("Download",io.BytesIO(k.kml().encode()),
                                       "sel.kml","application/vnd.google-earth.kml+xml")
            with b3:
                if st.button("🗑 Remove",disabled=not sel):
                    for lp in sel: st.session_state["parcels"].pop(lp,None)
                    st.session_state["table"]=df[~df["Lot/Plan"].isin(sel)]

# ─── LAYERS TAB ────────────────────────────────────────
with tab2:
    st.header("Basemap & Overlays")
    if cfg["basemaps"]:
        names=[b["name"] for b in cfg["basemaps"]]
        st.selectbox("Basemap",names,index=names.index(st.session_state["basemap"]),key="basemap")
    st.markdown("**Static Overlays**")
    for o in cfg["overlays"]:
        st.checkbox(o["name"],key=("ov_"+o["name"]))

# ─── DOWNLOADS TAB ─────────────────────────────────────
with tab3:
    st.header("Export All Parcels")
    if "parcels" in st.session_state and st.session_state["parcels"]:
        if st.button("💾 Generate KML"):
            s=st.session_state["style"]; k=simplekml.Kml();fld=k.newfolder(name=s["folder"])
            for lp,rec in st.session_state["parcels"].items():
                g=rec["geom"];poly=fld.newpolygon(name=lp,outerboundaryis=g.exterior.coords)
            st.download_button("Download parcels.kml",io.BytesIO(k.kml().encode()),
                               "parcels.kml","application/vnd.google-earth.kml+xml")
    else:
        st.info("Run a query first.")
