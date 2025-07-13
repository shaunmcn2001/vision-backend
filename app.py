#!/usr/bin/env python3
# LAWD Parcel Toolkit · 2025-07-12

import io, pathlib, requests, tempfile, zipfile, re
import streamlit as st
from streamlit_option_menu import option_menu
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
from streamlit_folium import st_folium
import folium, simplekml, geopandas as gpd, pandas as pd
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod
# ─────────────────────────────────────────────────────────
# ─── Static config ──────────────────────────────────────
CFG = pathlib.Path("layers.yaml")
try:
    import yaml
    cfg = yaml.safe_load(CFG.read_text()) if CFG.exists() else {}
except ImportError:
    cfg = {}
for k in ("basemaps","overlays"):
    cfg.setdefault(k, [])

# ─── Streamlit shell & tabs ─────────────────────────────
st.set_page_config("LAWD Parcel Toolkit", "📍", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown(
    "<h1 style='background:#ff6600;color:#fff;padding:10px;border-radius:4px;'>"
    "LAWD – Parcel Toolkit</h1>", unsafe_allow_html=True
)
with st.sidebar:
    page = option_menu(None, ["Query","Layers","Downloads"],
                       icons=["search","layers","download"],
                       menu_icon="cast", default_index=0,
                       styles={"container":{"padding":"0"}},
    )

# initialize session state
if cfg["basemaps"]:
    st.session_state.setdefault("basemap", cfg["basemaps"][0]["name"])
st.session_state.setdefault("ov_state", {o["name"]:False for o in cfg["overlays"]})

# ─── Cadastre lookup helpers ────────────────────────────
QLD = ("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
       "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW = ("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
       "NSW_Cadastre/MapServer/9/query")
GEOD = Geod(ellps="WGS84")

def fetch_parcels(ids):
    out, miss = {}, []
    for lp in ids:
        url,fld = (QLD,"lotplan") if re.match(r"^\d+[A-Z]{1,3}\d+$",lp,re.I) else (NSW,"lotidstring")
        try:
            js = requests.get(url, params={
                "where":f"{fld}='{lp}'","outFields":"*",
                "returnGeometry":"true","f":"geojson"
            }, timeout=12).json()
            feats = js.get("features",[])
            if not feats:
                miss.append(lp)
                continue
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

# ─────────────────────────────────────────────────────────
# ─── QUERY PAGE ────────────────────────────────────────
if page == "Query":
    # split into two columns: left for controls+table, right for map
    col_ctrl, col_map = st.columns([1,3], gap="small")

    with col_ctrl:
        st.markdown("### Lot/Plan Lookup")
        # input box
        ids_txt = st.text_area("IDs (one per line)", height=100,
                               placeholder="6RP702264\n5//DP123456")
        # style / KML folder
        with st.expander("⚙️ Style & KML", expanded=False):
            fx = st.color_picker("Fill color", "#ff6600")
            fo = st.slider("Fill opacity", 0, 100, 70)
            lx = st.color_picker("Outline color", "#2e2e2e")
            lw = st.slider("Line width (px)", 0.5, 6.0, 1.2, 0.1)
            folder = st.text_input("KML folder name", "Parcels")

        # Search button
        if st.button("🔍 Search", use_container_width=True) and ids_txt.strip():
            ids = [s.strip() for s in ids_txt.splitlines() if s.strip()]
            with st.spinner("Fetching parcels…"):
                recs, miss = fetch_parcels(ids)
            if miss:
                st.warning("Not found: " + ", ".join(miss))
            # build table rows
            rows = []
            for lp, rec in recs.items():
                props = rec["props"]
                ltype = props.get("lottype") or props.get("PURPOSE") or "n/a"
                area = abs(GEOD.geometry_area_perimeter(rec["geom"])[0]) / 1e4
                rows.append({"Lot/Plan":lp, "Lot Type":ltype, "Area (ha)":round(area,2)})
            st.session_state.update(
                parcels=recs,
                table=pd.DataFrame(rows),
                style=dict(fill=fx,op=fo,line=lx,w=lw,folder=folder)
            )
            st.success(f"{len(recs)} parcel{'s'*(len(recs)!=1)} loaded.")

        # results table + actions
        if "table" in st.session_state and not st.session_state["table"].empty:
            st.markdown("### Query Results")
            gdf = gpd.GeoDataFrame(
                st.session_state["table"],
                geometry=[r["geom"] for r in st.session_state["parcels"].values()],
                crs=4326
            )
            gob = GridOptionsBuilder.from_dataframe(gdf.drop(columns="geometry"))
            gob.configure_selection("multiple", use_checkbox=True)
            grid = AgGrid(
                gdf.drop(columns="geometry"),
                gridOptions=gob.build(),
                update_mode=GridUpdateMode.SELECTION_CHANGED,
                height=220
            )
            sel = grid["selected_rows"]
            ids_sel = [r["Lot/Plan"] for r in sel]

            # action buttons
            b1,b2,b3,b4 = st.columns(4)
            with b1:
                if st.button("🔍 Zoom", disabled=not ids_sel):
                    bb = gpd.GeoSeries(
                        [st.session_state["parcels"][i]["geom"] for i in ids_sel]
                    ).total_bounds
                    st.session_state["zoom_to"] = [[bb[1],bb[0]],[bb[3],bb[2]]]
            with b2:
                if st.button("💾 Export (KML)", disabled=not ids_sel):
                    s = st.session_state["style"]
                    fk,lk = kml_colour(s["fill"],s["op"]),kml_colour(s["line"],100)
                    k = simplekml.Kml()
                    for lp in ids_sel:
                        g = st.session_state["parcels"][lp]["geom"]
                        poly = k.newpolygon(name=lp,
                                          outerboundaryis=(g.exterior.coords if isinstance(g,Polygon)
                                                           else list(g.geoms)[0].exterior.coords))
                        poly.style.polystyle.color=fk; poly.style.linestyle.color=lk
                        poly.style.linestyle.width=float(s["w"])
                    st.download_button("Download KML",
                                       io.BytesIO(k.kml().encode()),
                                       "selection.kml","application/vnd.google-earth.kml+xml")
            with b3:
                if st.button("🗑 Remove", disabled=not ids_sel):
                    for lp in ids_sel:
                        st.session_state["parcels"].pop(lp,None)
                    st.session_state["table"] = st.session_state["table"][
                        ~st.session_state["table"]["Lot/Plan"].isin(ids_sel)
                    ]
            with b4:
                if st.button("📦 Export ALL", disabled=st.session_state["table"].empty):
                    s = st.session_state["style"]
                    fk,lk = kml_colour(s["fill"],s["op"]),kml_colour(s["line"],100)
                    k = simplekml.Kml(); fld = k.newfolder(name=s["folder"])
                    for lp,rec in st.session_state["parcels"].items():
                        g = rec["geom"]
                        polys = [g] if isinstance(g,Polygon) else list(g.geoms)
                        for i,p in enumerate(polys,1):
                            nm=f"{lp} ({i})" if len(polys)>1 else lp
                            poly=fld.newpolygon(name=nm,outerboundaryis=p.exterior.coords)
                            for ring in p.interiors: poly.innerboundaryis.append(ring.coords)
                            poly.style.polystyle.color=fk; poly.style.linestyle.color=lk
                            poly.style.linestyle.width=float(s["w"])
                    st.download_button("Download ALL KML",
                                       io.BytesIO(k.kml().encode()),
                                       "parcels.kml","application/vnd.google-earth.kml+xml")

    with col_map:
        # build and render the map full-screen
        m = folium.Map(location=[-25,145], zoom_start=5,
                       control_scale=True, width="100%", height="90vh")
        if "zoom_to" in st.session_state:
            m.fit_bounds(st.session_state.pop("zoom_to"))
        # basemap + overlays
        if cfg["basemaps"]:
            b=next(b for b in cfg["basemaps"] if b["name"]==st.session_state["basemap"])
            folium.TileLayer(b["url"],name=b["name"],attr=b["attr"],
                             overlay=False,control=True,show=True).add_to(m)
        for o in cfg["overlays"]:
            if st.session_state["ov_state"][o["name"]]:
                try:
                    if o["type"]=="wms":
                        folium.raster_layers.WmsTileLayer(
                            o["url"],layers=str(o["layers"]),transparent=True,
                            fmt=o.get("fmt","image/png"),version="1.1.1",
                            name=o["name"],attr=o["attr"]
                        ).add_to(m)
                    else:
                        folium.TileLayer(o["url"],name=o["name"],attr=o["attr"]).add_to(m)
                except:
                    pass
        # draw parcels
        if "parcels" in st.session_state:
            fg = folium.FeatureGroup(name="Parcels", show=True).add_to(m)
            for lp,rec in st.session_state["parcels"].items():
                geom,props=rec["geom"],rec["props"]
                folium.GeoJson(
                    {"type":"Feature","properties":{"name":lp},"geometry":mapping(geom)},
                    style_function=lambda f,s=st.session_state["style"]: {
                        "fillColor":s["fill"],"color":s["line"],
                        "weight":s["w"],"fillOpacity":s["op"]/100
                    }, tooltip=lp
                ).add_to(fg)
        st_folium(m, key="map", height=700, width="100%")

# ─── LAYERS PAGE (unchanged) ───────────────────────────
if page == "Layers":
    st.sidebar.header("Basemap")
    if cfg["basemaps"]:
        names=[b["name"] for b in cfg["basemaps"]]
        st.sidebar.radio("", names, index=names.index(st.session_state["basemap"]),
                         key="basemap")
    st.sidebar.header("Static overlays")
    for o in cfg["overlays"]:
        st.sidebar.checkbox(o["name"], key=("ov_"+o["name"]))

# ─── DOWNLOADS PAGE (unchanged) ────────────────────────
if page == "Downloads":
    st.sidebar.header("Export")
    if "parcels" in st.session_state and st.session_state["parcels"]:
        if st.sidebar.button("💾 Generate KML"):
            s=st.session_state["style"]; k= simplekml.Kml(); fld=k.newfolder(name=s["folder"])
            fk,lk = kml_colour(s["fill"],s["op"]),kml_colour(s["line"],100)
            for lp,rec in st.session_state["parcels"].items():
                g=rec["geom"]; polys=[g] if isinstance(g,Polygon) else list(g.geoms)
                for i,p in enumerate(polys,1):
                    nm=f"{lp} ({i})" if len(polys)>1 else lp
                    poly=fld.newpolygon(name=nm,outerboundaryis=p.exterior.coords)
                    for r in p.interiors: poly.innerboundaryis.append(r.coords)
                    poly.style.polystyle.color=fk; poly.style.linestyle.color=lk
                    poly.style.linestyle.width=float(s["w"])
            st.sidebar.download_button("Save KML",
                io.BytesIO(k.kml().encode()),"parcels.kml","application/vnd.google-earth.kml+xml")
    else:
        st.sidebar.info("Run a query first.")  
