#!/usr/bin/env python3
# LAWD Parcel Toolkit  · 2025-07-12 ❶

"""
Key updates (2025-07-12):
• Export‑ALL now offers only Shapefile (.shp in a ZIP) and KML.
• Removed the “Pulse” and “Buffer 200 m” tools.
• Fixed import for streamlit‑folium (use st_folium.get_last_msg instead of missing get_last_msg symbol).
"""

import io, re, json, yaml, pathlib, requests, tempfile, zipfile, os, base64
from collections import defaultdict

import streamlit as st
from streamlit_option_menu import option_menu
from streamlit_folium import st_folium         # ↩ fixed import
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

import folium, simplekml, geopandas as gpd, pandas as pd
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod
import uuid, time

# ────── STATIC CONFIG (basemap + overlays only) ────────────────────────────
CFG = pathlib.Path("layers.yaml")
cfg = yaml.safe_load(CFG.read_text()) if CFG.exists() else {}
for k in ("basemaps", "overlays"):
    cfg.setdefault(k, [])

# ────── STREAMLIT SHELL ────────────────────────────────────────────────────
st.set_page_config("Lot/Plan → KML", "📍", layout="wide",
                   initial_sidebar_state="collapsed")
st.title("LAWD – Parcel Toolkit")

tab = st.sidebar.radio("Navigate to", ["Query", "Layers", "Downloads"])

if cfg["basemaps"]:
    st.session_state.setdefault("basemap", cfg["basemaps"][0]["name"])
st.session_state.setdefault("ov_state", {o["name"]: False for o in cfg["overlays"]})

# ────── CADASTRE QUERIES ───────────────────────────────────────────────────
QLD = (
    "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
    "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query"
)
NSW = (
    "https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
    "NSW_Cadastre/MapServer/9/query"
)
geod = Geod(ellps="WGS84")

def fetch_parcels(ids, bbox=None):
    """Return {lotplan: {"geom": shapely, "props": dict}}, [not_found…]"""
    out, miss = {}, []
    for lp in ids:
        url, fld = (
            (QLD, "lotplan") if re.match(r"^\d+[A-Z]{1,3}\d+$", lp, re.I)
            else (NSW, "lotidstring")
        )
        params = {
            "where": f"{fld}='{lp}'",
            "outFields": "*",
            "returnGeometry": True,
            "f": "geojson",
        }
        if bbox:
            sw, ne = bbox
            params.update({
                "geometry": f"{sw[1]},{sw[0]},{ne[1]},{ne[0]}",
                "geometryType": "esriGeometryEnvelope",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
            })
        try:
            js = requests.get(url, params=params, timeout=12).json()
            feats = js.get("features", [])
            if not feats:
                miss.append(lp)
                continue
            wkid = feats[0]["geometry"].get("spatialReference", {}).get("wkid", 4326)
            tfm = Transformer.from_crs(wkid, 4326, always_xy=True).transform if wkid != 4326 else None
            geoms, props = [], {}
            for ft in feats:
                g = shape(ft["geometry"])
                geoms.append(transform(tfm, g) if tfm else g)
                props = ft["properties"]
            out[lp] = {"geom": unary_union(geoms), "props": props}
        except Exception:
            miss.append(lp)
    return out, miss

def kml_colour(hexrgb, pct):   # AABBGGRR
    r, g, b = hexrgb[1:3], hexrgb[3:5], hexrgb[5:7]
    a = int(round(255 * pct / 100))
    return f"{a:02x}{b}{g}{r}"

# ────── TAB : QUERY ────────────────────────────────────────────────────────
if tab == "Query":
    ids_txt = st.sidebar.text_area("Lot/Plan IDs", height=140,
                                   placeholder="6RP702264\n5//DP123456")
    restrict = st.sidebar.checkbox("🔲 Restrict to map extent", value=False,
                                   help="Only fetch parcels within the current map view")
    fx = st.sidebar.color_picker("Fill", "#ff6600")
    fo = st.sidebar.slider("Opacity %", 0, 100, 70)
    lx = st.sidebar.color_picker("Outline", "#2e2e2e")
    lw = st.sidebar.slider("Outline width px", 0.5, 6.0, 1.2, 0.1)
    folder = st.sidebar.text_input("KML folder", "Parcels")

    if st.sidebar.button("🔍 Search") and ids_txt.strip():
        ids = [s.strip() for s in ids_txt.splitlines() if s.strip()]
        folium_out = st.session_state.get("last_bounds")
        bbox = folium_out if (restrict and folium_out) else None
        with st.spinner("Fetching parcels…"):
            recs, miss = fetch_parcels(ids, bbox=bbox)
        if miss:
            st.sidebar.warning("Not found: " + ", ".join(miss))
        rows = []
        for lp, rec in recs.items():
            props = rec["props"]
            lottype = props.get("lottype") or props.get("PURPOSE") or "n/a"
            area = abs(geod.geometry_area_perimeter(rec["geom"])[0]) / 1e4
            rows.append({"Lot/Plan": lp, "Lot Type": lottype, "Area (ha)": round(area, 2)})
        st.session_state["parcels"] = recs
        st.session_state["table"] = pd.DataFrame(rows)
        st.session_state["style"] = dict(fill=fx, op=fo, line=lx, w=lw, folder=folder)
        st.sidebar.success(f"{len(recs)} parcel{'s'*(len(recs)!=1)} loaded.")

# ────── TAB : LAYERS ───────────────────────────────────────────────────────
if tab == "Layers":
    if cfg["basemaps"]:
        st.sidebar.subheader("Basemap")
        names = [b["name"] for b in cfg["basemaps"]]
        st.session_state["basemap"] = st.sidebar.radio("", names,
            index=names.index(st.session_state["basemap"]))
    st.sidebar.subheader("Static overlays")
    for o in cfg["overlays"]:
        st.session_state["ov_state"][o["name"]] = st.sidebar.checkbox(o["name"], value=st.session_state["ov_state"][o["name"]])

# ────── BUILD FOLIUM MAP ───────────────────────────────────────────────────
m = folium.Map(location=[-25, 145], zoom_start=5,
               control_scale=True, width="100%", height="100vh")
# basemap
if cfg["basemaps"]:
    b = next(bb for bb in cfg["basemaps"] if bb["name"] == st.session_state["basemap"])
    folium.TileLayer(b["url"], name=b["name"], attr=b["attr"], overlay=False, control=True, show=True).add_to(m)
# overlays
for o in cfg["overlays"]:
    if not st.session_state["ov_state"][o["name"]]:
        continue
    try:
        if o["type"] == "wms":
            folium.raster_layers.WmsTileLayer(o["url"], layers=str(o["layers"]), transparent=True, fmt=o.get("fmt", "image/png"), name=o["name"], attr=o["attr"], version="1.1.1").add_to(m)
        else:
            folium.TileLayer(o["url"], name=o["name"], attr=o["attr"]).add_to(m)
    except Exception as e:
        st.warning(f"{o['name']} failed: {e}")
# parcels overlay
if "parcels" in st.session_state:
    s = st.session_state["style"]
    def sty(_):
        return {"fillColor": s["fill"], "color": s["line"], "weight": s["w"], "fillOpacity": s["op"]/100}
    pg = folium.FeatureGroup(name="Parcels", show=True).add_to(m)
    bounds = []
    for lp, rec in st.session_state["parcels"].items():
        g = rec["geom"]
        folium.GeoJson(mapping(g), name=lp, style_function=sty,
                       tooltip=lp,
                       popup=folium.Popup(f"<b>Lot/Plan:</b> {lp}<br><b>Area:</b> {abs(geod.geometry_area_perimeter(g)[0])/1e4:,.2f} ha")).add_to(pg)
        bounds.append([[g.bounds[1], g.bounds[0]], [g.bounds[3], g.bounds[2]]])
    if bounds:
        ys, xs, ye, xe = zip(*((b[0][0], b[0][1], b[1][0], b[1][1]) for b in bounds))
        m.fit_bounds([[min(ys), min(xs)], [max(ye), max(xe)]])

# render map and capture bounds
folium_out = st_folium(m, height=700, use_container_width=True, key="fol", return_bounds=True)
st.session_state["last_bounds"] = folium_out.get("bounds")

# ────── RESULTS TABLE + ACTION MENU + EXPORT-ALL BAR ─────────────────────
if "table" in st.session_state and not st.session_state["table"].empty:

    st.subheader("Query results")

    gdf = gpd.GeoDataFrame(st.session_state["table"],
                           geometry=[rec["geom"] for rec in st.session_state["parcels"].values()],
                           crs=4326)

    # -------- Grid & Context Menu (Pulse + Buffer removed) --------
    gob = GridOptionsBuilder.from_dataframe(gdf.drop(columns="geometry"), enableRowGroup=False)
    gob.configure_selection("multiple", use_checkbox=True)
    gob.configure_grid_options(
        getContextMenuItems="""function(p){
           var z=[ 'copy', 'separator',
             { name:'Zoom to result(s)', action:()=>window.postMessage({type:'zoom'}) },
             'separator',
             { name:'Export to Shapefile', action:()=>window.postMessage({type:'shp'}) },
             'separator',
             { name:'Remove result(s)', action:()=>window.postMessage({type:'remove'}) }]; return z; }""")

    grid = AgGrid(
        gdf.drop(columns="geometry"),
        gridOptions=gob.build(),
        update_mode=GridUpdateMode.MODEL_CHANGED,
        allow_unsafe_jscode=True,
        height=250,
    )

    # ---------------- Export-ALL (Shapefile + KML) ----------------
    col1, col2 = st.columns(2)
    with col1:
        # Shapefile
        tmp = tempfile.mkdtemp()
        gdf.to_file(tmp + "/parcels.shp")
        zname = pathlib.Path(tmp, "parcels.zip")
        with zipfile.ZipFile(zname, "w", zipfile.ZIP_DEFLATED) as z:
            for f in pathlib.Path(tmp).glob("parcels.*"):
                z.write(f, f.name)
        zbytes = open(zname, "rb").read()
        st.download_button("⬇️ Export ALL (Shapefile)", zbytes,
                           "parcels.zip", "application/zip")
    with col2:
        # KML
        s = st.session_state["style"]; gs = st.session_state["parcels"]
        kml = simplekml.Kml(); fld = kml.newfolder(name=s["folder"])
        fk, lk = kml_colour(s["fill"], s["op"]), kml_colour(s["line"], 100)
        for lp, rec in gs.items():
            g = rec["geom"]
            polys = [g] if isinstance(g, Polygon) else list(g.geoms)
            for i, p in enumerate(polys, 1):
                area = abs(geod.geometry_area_perimeter(p)[0]) / 1e4
                nm = f"{lp} ({i})" if len(polys) > 1 else lp
                desc = f"Lot/Plan: {lp}<br>Area: {area:,.2f} ha"
                pl = fld.newpolygon(name=nm, description=desc,
                                    outerboundaryis=p.exterior.coords)
                for r in p.interiors:
                    pl.innerboundaryis.append(r.coords)
                pl.style.polystyle.color = fk
                pl.style.linestyle.color = lk
                pl.style.linestyle.width = float(s["w"])
        st.download_button("⬇️ Export ALL (KML)",
                           io.BytesIO(kml.kml().encode()).getvalue(),
                           "parcels.kml", "application/vnd.google-earth.kml+xml")

    # ---------- handle row-level actions ----------
    js_msg = get_last_msg()
    if js_msg and js_msg.get("type") in {"zoom", "shp", "remove"}:
        sel_rows = grid["selected_rows"]
        if not sel_rows:
            st.warning("No rows selected."); st.stop()

        sel_ids = [r["Lot/Plan"] for r in sel_rows]
        sel_geoms = [st.session_state["parcels"][lp]["geom"] for lp in sel_ids]

        if js_msg["type"] == "zoom":
            bb = gpd.GeoSeries(sel_geoms).total_bounds
            st.session_state["__zoom_bounds"] = [[bb[1], bb[0]], [bb[3], bb[2]]]
            st.experimental_rerun()

        elif js_msg["type"] == "shp":
            # Export selected rows as Shapefile (ZIP)
            tmp2 = tempfile.mkdtemp()
            gpd.GeoDataFrame(st.session_state["table"][st.session_state["table"]["Lot/Plan"].isin(sel_ids)],
                             geometry=sel_geoms, crs=4326).to_file(tmp2 + "/sel.shp")
            zsel = pathlib.Path(tmp2, "selected.zip")
            with zipfile.ZipFile(zsel, "w", zipfile.ZIP_DEFLATED) as z:
                for f in pathlib.Path(tmp2).glob("sel.*"):
                    z.write(f, f.name)
            st.download_button("Download Shapefile ZIP",
                               open(zsel, "rb").read(), "selected.zip", "application/zip")

        elif js_msg["type"] == "remove":
            for lp in sel_ids:
                st.session_state["parcels"].pop(lp, None)
            st.session_state["table"] = st.session_state["table"][~st.session_state["table"]["Lot/Plan"].isin(sel_ids)]
            st.experimental_rerun()

# honour zoom request
if "__zoom_bounds" in st.session_state:
    m.fit_bounds(st.session_state.pop("__zoom_bounds"))

# ────── TAB : DOWNLOADS (legacy KML button) ───────────────────────────────
if tab == "Downloads":
    st.sidebar.subheader("Export")
    if "parcels" in st.session_state and st.session_state["parcels"]:
        if st.sidebar.button("💾 Generate KML"):
            s = st.session_state["style"]; gs = st.session_state["parcels"]
            kml = simplekml.Kml(); fld = kml.newfolder(name=s["folder"])
            fk, lk = kml_colour(s["fill"], s["op"]), kml_colour(s["line"], 100)
            for lp, rec in gs.items():
                g = rec["geom"]
                polys = [g] if isinstance(g, Polygon) else list(g.geoms)
                for i, p in enumerate(polys, 1):
                    area = abs(geod.geometry_area_perimeter(p)[0]) / 1e4
                    nm = f"{lp} ({i})" if len(polys) > 1 else lp
                    desc = f"Lot/Plan: {lp}<br>Area: {area:,.2f} ha"
                    pl = fld.newpolygon(name=nm, description=desc,
                                        outerboundaryis=p.exterior.coords)
                    for r in p.interiors: pl.innerboundaryis.append(r.coords)
                    pl.style.polystyle.color = fk
                    pl.style.linestyle.color = lk
                    pl.style.linestyle.width = float(s["w"])
            st.sidebar.download_button(
                "Save KML", io.BytesIO(kml.kml().encode()).getvalue(),
                "parcels.kml", "application/vnd.google-earth.kml+xml")
    else:
        st.sidebar.info("Load parcels in Query first.")
