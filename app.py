#!/usr/bin/env python3
# LAWD Parcel Toolkit — SEED-style layout  ·  2025-07-12

import io, re, yaml, pathlib, requests, tempfile, zipfile, uuid, time
from collections import defaultdict

import streamlit as st
from streamlit_folium import st_folium
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod
import geopandas as gpd, pandas as pd, folium, simplekml

# ─── static config (basemap + overlays) ───────────────────────────────────
CFG = pathlib.Path("layers.yaml")
cfg = yaml.safe_load(CFG.read_text()) if CFG.exists() else {}
for k in ("basemaps", "overlays"):
    cfg.setdefault(k, [])

# ─── page setup & global style --- mimic SEED look ────────────────────────
st.set_page_config("Lot/Plan viewer", "🌱", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
/* pill search box */
input[data-baseweb="input"] {border-radius:28px;font-size:18px;padding:8px 18px;}
/* hide Streamlit default labels */
div[data-testid="stHorizontalBlock"] label {display:none;}
/* move the result drawer flush left */
div[data-testid="column"] > div:first-child {padding-top:0px;}
/* give drawer fixed height & scroll */
.drawer {height:78vh;overflow:auto;}
</style>
""", unsafe_allow_html=True)

geod = Geod(ellps="WGS84")

# ─── session defaults ──────────────────────────────────────────────────────
if cfg["basemaps"]:
    st.session_state.setdefault("basemap", cfg["basemaps"][0]["name"])
st.session_state.setdefault("ov_state", {o["name"]: False for o in cfg["overlays"]})

# ─── cadastral endpoints & helper ──────────────────────────────────────────
QLD = ("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
       "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW = ("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
       "NSW_Cadastre/MapServer/9/query")

def fetch_parcels(lotplans: list[str]):
    out, missing = {}, []
    for lp in lotplans:
        url, fld = (QLD, "lotplan") if re.match(r"^\d+[A-Z]{1,3}\d+$", lp, re.I) else (NSW, "lotidstring")
        try:
            js = requests.get(url, params={
                    "where": f"{fld}='{lp}'",
                    "outFields": "*",
                    "returnGeometry": "true",
                    "f": "geojson"}, timeout=12).json()
            feats = js.get("features", [])
            if not feats: missing.append(lp); continue
            wkid = feats[0]["geometry"].get("spatialReference", {}).get("wkid", 4326)
            tfm = Transformer.from_crs(wkid, 4326, always_xy=True).transform if wkid != 4326 else None
            geoms, props = [], {}
            for ft in feats:
                g = shape(ft["geometry"])
                geoms.append(transform(tfm, g) if tfm else g)
                props = ft["properties"]
            out[lp] = {"geom": unary_union(geoms), "props": props}
        except Exception:
            missing.append(lp)
    return out, missing

def kml_colour(hexrgb, pct):  # AABBGGRR
    r,g,b = hexrgb[1:3], hexrgb[3:5], hexrgb[5:7]
    a = int(round(255*pct/100))
    return f"{a:02x}{b}{g}{r}"

# ─── top pill search bar ───────────────────────────────────────────────────
with st.container():
    col_search, col_btn = st.columns([6,1])
    query_text = col_search.text_input("", placeholder="Lot/Plan IDs: 6RP702264, 5//DP123456")
    go = col_btn.button("🔍", use_container_width=True)

# ─── colour / width pickers in expander top-right (optional) ───────────────
with st.expander("Style options", expanded=False):
    fx = st.color_picker("Fill",      st.session_state.get("style", {}).get("fill",  "#ff6600"))
    lx = st.color_picker("Outline",   st.session_state.get("style", {}).get("line",  "#2e2e2e"))
    fo = st.slider      ("Opacity %", 0, 100, st.session_state.get("style", {}).get("op", 70))
    lw = st.slider      ("Line px",   0.5, 6.0, st.session_state.get("style", {}).get("w", 1.2), 0.1)

# ─── run query when 🔍 clicked ─────────────────────────────────────────────
if go and query_text.strip():
    ids = [s.strip() for s in re.split(r"[,\n;]", query_text) if s.strip()]
    with st.spinner("Fetching parcels…"):
        recs, miss = fetch_parcels(ids)
    if miss: st.warning("Not found: " + ", ".join(miss))

    rows = []
    for lp, rec in recs.items():
        props = rec["props"]
        lottype = props.get("lottype") or props.get("PURPOSE") or "n/a"
        area = abs(geod.geometry_area_perimeter(rec["geom"])[0]) / 1e4
        rows.append({"Lot/Plan": lp, "Lot Type": lottype, "Area (ha)": round(area, 2)})

    st.session_state["parcels"] = recs
    st.session_state["table"]   = pd.DataFrame(rows)
    st.session_state["style"]   = dict(fill=fx, op=fo, line=lx, w=lw)
    st.success(f"{len(recs)} parcel{'s'*(len(recs)!=1)} loaded.")

# ─── page layout: drawer (left) + map (right) ─────────────────────────────
left, right = st.columns([3, 9], gap="small")

# === left drawer ===
with left:
    with st.expander("📋 Query Results", expanded=True):
        # show AgGrid only if we have data
        if "table" in st.session_state and not st.session_state["table"].empty:

            gdf = gpd.GeoDataFrame(
                st.session_state["table"],
                geometry=[rec["geom"] for rec in st.session_state["parcels"].values()],
                crs=4326)

            gob = GridOptionsBuilder.from_dataframe(gdf.drop(columns="geometry"))
            gob.configure_selection("multiple", use_checkbox=True)
            gob.configure_grid_options(getContextMenuItems="""
            function(p){return [
              'copy','separator',
              {name:'Zoom to selection', action:()=>window.postMessage({type:'zoom'})},
              {name:'Pulse',             action:()=>window.postMessage({type:'pulse'})},
              {name:'Buffer 200 m',      action:()=>window.postMessage({type:'buffer'})},
              'separator',
              {name:'Export to CSV',     action:()=>window.postMessage({type:'csv'})},
              {name:'Export to XLSX',    action:()=>window.postMessage({type:'xlsx'})},
              {name:'Export to Shapefile',action:()=>window.postMessage({type:'shp'})},
              'separator',
              {name:'Remove',           action:()=>window.postMessage({type:'remove'})}
            ]; }""")

            grid = AgGrid(gdf.drop(columns="geometry"), gridOptions=gob.build(),
                          update_mode=GridUpdateMode.MODEL_CHANGED,
                          height=300, allow_unsafe_jscode=True, key="grid")

            # Export-ALL bar
            c1,c2,c3 = st.columns(3)
            csv_all = st.session_state["table"].to_csv(index=False).encode()
            c1.download_button("⬇️ CSV",  csv_all, "parcels.csv", "text/csv", key="csv_all")
            xls = io.BytesIO(); st.session_state["table"].to_excel(xls, index=False); xls.seek(0)
            c2.download_button("⬇️ XLSX", xls.getvalue(), "parcels.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="xlsx_all")
            tmp = tempfile.mkdtemp()
            gdf.to_file(tmp+"/all.shp")
            zname = pathlib.Path(tmp, "all.zip")
            with zipfile.ZipFile(zname,"w",zipfile.ZIP_DEFLATED) as z:
                for f in pathlib.Path(tmp).glob("all.*"): z.write(f, f.name)
            c3.download_button("⬇️ SHP", open(zname,"rb").read(), "parcels.zip", "application/zip", key="shp_all")

            # handle row-level menu actions
            def handle_grid_action(action:str, sel_rows:list[dict]):
                if not sel_rows: st.warning("No rows selected."); return
                sel_ids = [r["Lot/Plan"] for r in sel_rows]
                sel_geoms = [st.session_state["parcels"][lp]["geom"] for lp in sel_ids]

                if action=="zoom":
                    bb = gpd.GeoSeries(sel_geoms).total_bounds
                    st.session_state["__zoom_bounds"] = [[bb[1],bb[0]],[bb[3],bb[2]]]
                elif action=="pulse":
                    pulse = folium.GeoJson({'type':'FeatureCollection',
                                            'features':[mapping(g) for g in sel_geoms]},
                                           style_function=lambda _:{'color':'red','weight':4,'fillOpacity':0})
                    st.session_state["__pulse_layer"] = pulse._template.render(pulse=None)
                elif action=="buffer":
                    buf = gpd.GeoSeries(sel_geoms, crs=4326).to_crs(3857).buffer(200).to_crs(4326)
                    st.session_state["__buffer_layer"] = buf.__geo_interface__
                elif action in {"csv","xlsx","shp"}:
                    df_sel = st.session_state["table"][st.session_state["table"]["Lot/Plan"].isin(sel_ids)]
                    if action=="csv":
                        st.download_button("Download selected CSV",
                                           df_sel.to_csv(index=False).encode(),
                                           "selected.csv", "text/csv", key=str(uuid.uuid4()))
                    elif action=="xlsx":
                        bio = io.BytesIO(); df_sel.to_excel(bio, index=False); bio.seek(0)
                        st.download_button("Download selected XLSX", bio.getvalue(),
                                           "selected.xlsx",
                                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                           key=str(uuid.uuid4()))
                    else:  # shp
                        tmp2=tempfile.mkdtemp()
                        gpd.GeoDataFrame(df_sel, geometry=sel_geoms, crs=4326
                                         ).to_file(tmp2+"/sel.shp")
                        zfile=pathlib.Path(tmp2,"sel.zip")
                        with zipfile.ZipFile(zfile,"w",zipfile.ZIP_DEFLATED) as z:
                            for f in pathlib.Path(tmp2).glob("sel.*"): z.write(f, f.name)
                        st.download_button("Download selected SHP",
                                           open(zfile,"rb").read(),"selected.zip",
                                           "application/zip", key=str(uuid.uuid4()))
                elif action=="remove":
                    for lp in sel_ids:
                        st.session_state["parcels"].pop(lp, None)
                    st.session_state["table"]=st.session_state["table"][~st.session_state["table"]["Lot/Plan"].isin(sel_ids)]
                    st.experimental_rerun()

            jsmsg = st_folium.get_last_msg()
            if jsmsg and jsmsg.get("type"):
                handle_grid_action(jsmsg["type"], grid["selected_rows"])

# === right column : map ===
with right:
    m = folium.Map(location=[-25,145], zoom_start=5, control_scale=True,
                   tiles=None, width="100%", height="80vh")

    # basemap
    if cfg["basemaps"]:
        bm = next(b for b in cfg["basemaps"] if b["name"]==st.session_state["basemap"])
        folium.TileLayer(bm["url"], name=bm["name"], attr=bm["attr"],
                         overlay=False, control=True, show=True).add_to(m)

    # overlays
    for o in cfg["overlays"]:
        if not st.session_state["ov_state"][o["name"]]: continue
        if o["type"]=="wms":
            folium.raster_layers.WmsTileLayer(o["url"], layers=str(o["layers"]),
                                              transparent=True, version="1.1.1",
                                              fmt=o.get("fmt","image/png"),
                                              name=o["name"], attr=o["attr"]).add_to(m)
        else:
            folium.TileLayer(o["url"], name=o["name"], attr=o["attr"]).add_to(m)

    # parcels
    bounds=[]
    if "parcels" in st.session_state:
        stl = st.session_state["style"]
        style_fn = lambda _:{'fillColor':stl["fill"], 'color':stl["line"],
                             'weight':stl["w"], 'fillOpacity':stl["op"]/100}
        pg = folium.FeatureGroup(name="Parcels", show=True).add_to(m)
        for lp, rec in st.session_state["parcels"].items():
            g=rec["geom"]; p=rec["props"]
            lottype=p.get("lottype") or p.get("PURPOSE") or "n/a"
            area=abs(geod.geometry_area_perimeter(g)[0])/1e4
            pop=(f"<b>Lot/Plan:</b> {lp}<br><b>Lot Type:</b> {lottype}"
                 f"<br><b>Area:</b> {area:,.2f} ha")
            folium.GeoJson(mapping(g), style_function=style_fn,
                           tooltip=lp, popup=pop).add_to(pg)
            bounds.append([[g.bounds[1],g.bounds[0]],[g.bounds[3],g.bounds[2]]])

    # zoom or pulse request
    if "__zoom_bounds" in st.session_state:
        m.fit_bounds(st.session_state.pop("__zoom_bounds"))
    if "__pulse_layer" in st.session_state:
        html=st.session_state.pop("__pulse_layer")
        folium.map.LayerControl().add_to(m)  # ensure top
        m.get_root().html.add_child(folium.Element(html))
    if "__buffer_layer" in st.session_state:
        buf_geo=st.session_state.pop("__buffer_layer")
        folium.GeoJson(buf_geo, style_function=lambda _:{'color':'blue','weight':2,'fillOpacity':0.1}
                       ).add_to(m)

    if bounds and "__zoom_bounds" not in st.session_state:  # initial zoom
        xs,ys,xe,ye=zip(*[(b[0][1],b[0][0],b[1][1],b[1][0]) for b in bounds])
        m.fit_bounds([[min(ys),min(xs)],[max(ye),max(xe)]])

    st_folium(m, height=700, use_container_width=True, key="folium_map")
