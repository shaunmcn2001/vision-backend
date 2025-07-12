#!/usr/bin/env python3
# LAWD Parcel Toolkit  · 2025-07

"""
Streamlit one-page app for parcel lookup & export.
─────────────────────────────────────────────────
• Compact sidebar (IDs + Style expander)
• Folium map with parcels layer
• Interactive AgGrid table under the map
    – Tick rows → Zoom / Export KML / Export SHP / Remove
• Export-ALL bar (KML + Shapefile)
"""

import io, re, yaml, pathlib, requests, tempfile, zipfile, uuid, shutil
import streamlit as st
from streamlit_option_menu import option_menu
from streamlit_folium import st_folium
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

import folium, simplekml, geopandas as gpd, pandas as pd
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod
from streamlit_folium import st_folium, get_last_msg
# ───────────────────── STATIC CONFIG ────────────────────────────────────
CFG = pathlib.Path("layers.yaml")
cfg = yaml.safe_load(CFG.read_text()) if CFG.exists() else {}
for k in ("basemaps", "overlays"):
    cfg.setdefault(k, [])

# ───────────────────── STREAMLIT SHELL ──────────────────────────────────
st.set_page_config("Lot/Plan Toolkit", "📍", layout="wide", initial_sidebar_state="collapsed")
st.markdown(
    "<div style='background:#ff6600;color:#fff;"
    "font-size:20px;font-weight:600;padding:6px 20px;"
    "border-radius:8px;margin-bottom:6px'>LAWD – Parcel Toolkit</div>",
    unsafe_allow_html=True
)

with st.sidebar:
    tab = option_menu(
        None, ["Query", "Layers", "Downloads"],
        icons=["search", "layers", "download"], default_index=0,
        styles={
            "container": {"padding": "0", "background": "#262730"},
            "nav-link-selected": {"background": "#ff6600"}
        }
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
                params={"where": f"{fld}='{lp}'", "outFields": "*",
                        "returnGeometry": "true", "f": "geojson"},
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

        # Build results table
        rows = []
        for lp, rec in recs.items():
            props = rec["props"]
            lottype = props.get("lottype") or props.get("PURPOSE") or "n/a"
            area = abs(g_geod.geometry_area_perimeter(rec["geom"])[0]) / 1e4
            rows.append({
                "Lot/Plan": lp,
                "Lot Type": lottype,
                "Area (ha)": round(area, 2)
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
map_obj = folium.Map(location=[-25, 145], zoom_start=5,
                     control_scale=True, width="100%", height="100vh")

# Basemap
if cfg["basemaps"]:
    base = next(b for b in cfg["basemaps"] if b["name"] == st.session_state["basemap"])
    folium.TileLayer(base["url"], name=base["name"], attr=base["attr"],
                     overlay=False, control=True, show=True).add_to(map_obj)
# Overlays
for o in cfg["overlays"]:
    if st.session_state["ov_state"][o["name"]]:
        try:
            if o["type"] == "wms":
                folium.raster_layers.WmsTileLayer(
                    o["url"], layers=str(o["layers"]), transparent=True,
                    fmt=o.get("fmt", "image/png"), version="1.1.1",
                    name=o["name"], attr=o["attr"]).add_to(map_obj)
            else:
                folium.TileLayer(o["url"], name=o["name"], attr=o["attr"]).add_to(map_obj)
        except Exception as e:
            st.warning(f"{o['name']} failed: {e}")

# Parcel layer & auto-zoom
bounds = []
if "parcels" in st.session_state:
    s = st.session_state["style"]
    def sty(_): return {
        "fillColor": s["fill"], "color": s["line"],
        "weight": s["w"], "fillOpacity": s["op"]/100
    }
    pg = folium.FeatureGroup(name="Parcels", show=True).add_to(map_obj)
    for lp, rec in st.session_state["parcels"].items():
        geom, prop = rec["geom"], rec["props"]
        lottype = prop.get("lottype") or prop.get("PURPOSE") or "n/a"
        area = abs(g_geod.geometry_area_perimeter(geom)[0]) / 1e4
        popup_html = (f"<b>Lot/Plan:</b> {lp}<br>"
                      f"<b>Lot Type:</b> {lottype}<br>"
                      f"<b>Area:</b> {area:,.2f} ha")
        folium.GeoJson(mapping(geom), name=lp, style_function=sty,
                       tooltip=lp, popup=popup_html).add_to(pg)
        bounds.append([[geom.bounds[1], geom.bounds[0]],
                       [geom.bounds[3], geom.bounds[2]]])
if bounds:
    ys, xs, ye, xe = zip(*[(b[0][0], b[0][1], b[1][0], b[1][1]) for b in bounds])
    map_obj.fit_bounds([[min(ys), min(xs)], [max(ye), max(xe)]])

# Render map
st_folium(map_obj, height=550, use_container_width=True, key="map")

# ═══════════════════ RESULTS TABLE + ACTIONS ═════════════════════════════
if "table" in st.session_state and not st.session_state["table"].empty:
    st.subheader("Query Results")

    # Build AgGrid
    gdf = gpd.GeoDataFrame(
        st.session_state["table"],
        geometry=[rec["geom"] for rec in st.session_state["parcels"].values()],
        crs=4326
    )
    gob = GridOptionsBuilder.from_dataframe(gdf.drop(columns="geometry"))
    gob.configure_selection("multiple", use_checkbox=True)
    gob.configure_grid_options(
        getContextMenuItems="""function(p){
            return [
              'copy','separator',
              { name:'Zoom to result(s)', action:()=>window.postMessage({type:'zoom'}) },
              { name:'Export KML', action:()=>window.postMessage({type:'kml'}) },
              { name:'Export SHP', action:()=>window.postMessage({type:'shp'}) },
              'separator',
              { name:'Remove result(s)', action:()=>window.postMessage({type:'remove'}) }
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

    # Export-ALL bar
    col1, col2 = st.columns(2)
    with col1:
        # KML
        btn = col1.button("Export ALL (KML)")
        if btn:
            kml = simplekml.Kml()
            fld = kml.newfolder(name=st.session_state["style"]["folder"])
            fk = kml_colour(st.session_state["style"]["fill"], st.session_state["style"]["op"])
            lk = kml_colour(st.session_state["style"]["line"], 100)
            for lp, rec in st.session_state["parcels"].items():
                geom = rec["geom"]
                polys = [geom] if isinstance(geom, Polygon) else list(geom.geoms)
                for i, p in enumerate(polys, 1):
                    name = f"{lp} ({i})" if len(polys)>1 else lp
                    desc = f"Lot/Plan: {lp}<br>Area: {abs(g_geod.geometry_area_perimeter(p)[0])/1e4:,.2f} ha"
                    poly = fld.newpolygon(name=name, description=desc, outerboundaryis=p.exterior.coords)
                    for ring in p.interiors: poly.innerboundaryis.append(ring.coords)
                    poly.style.polystyle.color = fk
                    poly.style.linestyle.color = lk
                    poly.style.linestyle.width = float(st.session_state["style"]["w"])
            data = io.BytesIO(kml.kml().encode())
            st.download_button("Download ALL KML", data, "parcels.kml", "application/vnd.google-earth.kml+xml")

    with col2:
        # Shapefile
        btn2 = col2.button("Export ALL (SHP)")
        if btn2:
            tmp = tempfile.mkdtemp()
            gpd.GeoDataFrame(
                st.session_state["table"],
                geometry=[rec["geom"] for rec in st.session_state["parcels"].values()],
                crs=4326
            ).to_file(tmp + "/all.shp")
            zpath = os.path.join(tmp, "all.zip")
            with zipfile.ZipFile(zpath, "w") as z:
                for f in pathlib.Path(tmp).glob("all.*"):
                    z.write(f, f.name)
            with open(zpath, "rb") as f:
                st.download_button("Download ALL SHP", f.read(), "parcels.zip", "application/zip")

    # Handle row-level actions
    js = get_last_msg()
    if js and js.get("type") in {"zoom","kml","shp","remove"}:
        selected = grid["selected_rows"]
        if not selected:
            st.warning("Select rows first!"); st.stop()
        ids = [r["Lot/Plan"] for r in selected]
        geoms = [st.session_state["parcels"][i]["geom"] for i in ids]

        if js["type"]=="zoom":
            bb = gpd.GeoSeries(geoms).total_bounds
            st.session_state["__zoom"] = [[bb[1],bb[0]],[bb[3],bb[2]]]
            st.experimental_rerun()

        elif js["type"]=="remove":
            for i in ids: st.session_state["parcels"].pop(i,None)
            st.session_state["table"] = st.session_state["table"][~st.session_state["table"]["Lot/Plan"].isin(ids)]
            st.experimental_rerun()

        else:
            # Export selected KML or SHP same pattern as above but filtered
            df = st.session_state["table"][st.session_state["table"]["Lot/Plan"].isin(ids)]
            if js["type"]=="kml":
                kml = simplekml.Kml(); fld=kml.newfolder("Selected")
                fk=kml_colour(st.session_state["style"]["fill"], st.session_state["style"]["op"])
                lk=kml_colour(st.session_state["style"]["line"], 100)
                for lp in ids:
                    geom=st.session_state["parcels"][lp]["geom"]
                    polys=[geom] if isinstance(geom,Polygon) else list(geom.geoms)
                    for p in polys:
                        poly=fld.newpolygon(name=lp, outerboundaryis=p.exterior.coords)
                        poly.style.polystyle.color=fk; poly.style.linestyle.color=lk; poly.style.linestyle.width=float(st.session_state["style"]["w"])
                data=io.BytesIO(kml.kml().encode())
                st.download_button("Download Selected KML", data, "selected.kml", "application/vnd.google-earth.kml+xml")

            if js["type"]=="shp":
                tmp2=tempfile.mkdtemp()
                gpd.GeoDataFrame(df, geometry=geoms, crs=4326).to_file(tmp2+"/sel.shp")
                z2=os.path.join(tmp2,"sel.zip")
                with zipfile.ZipFile(z2,"w") as z:
                    for f in pathlib.Path(tmp2).glob("sel.*"):
                        z.write(f,f.name)
                with open(z2,"rb") as f:
                    st.download_button("Download Selected SHP", f.read(), "selected.zip","application/zip")

# Honor zoom
if "__zoom" in st.session_state:
    map_obj.fit_bounds(st.session_state.pop("__zoom"))
