#!/usr/bin/env python3
# LAWD Parcel Toolkit – sidebar query, table below map  · 2025-07-12

import io, re, yaml, pathlib, requests, tempfile, zipfile, uuid, time
from collections import defaultdict

import streamlit as st
from streamlit_folium import st_folium
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
import pandas as pd, geopandas as gpd, folium, simplekml
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod

# ─── static config ───────────────────────────────────────────────────────
CFG = pathlib.Path("layers.yaml")
cfg = yaml.safe_load(CFG.read_text()) if CFG.exists() else {}
for k in ("basemaps", "overlays"): cfg.setdefault(k, [])

# ─── page setup ──────────────────────────────────────────────────────────
st.set_page_config("Lot/Plan → KML", "📍", layout="wide")
geod = Geod(ellps="WGS84")

# ─── session defaults ────────────────────────────────────────────────────
if cfg["basemaps"]:
    st.session_state.setdefault("basemap", cfg["basemaps"][0]["name"])
st.session_state.setdefault("ov_state", {o["name"]: False for o in cfg["overlays"]})

# ─── cadastral endpoints & helper ───────────────────────────────────────
QLD = ("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
       "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW = ("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
       "NSW_Cadastre/MapServer/9/query")

def fetch_parcels(lps):
    out, miss = {}, []
    for lp in lps:
        url, fld = (QLD, "lotplan") if re.match(r"^\d+[A-Z]{1,3}\d+$", lp, re.I) else (NSW, "lotidstring")
        try:
            js = requests.get(url, params={"where":f"{fld}='{lp}'","outFields":"*",
                                           "returnGeometry":"true","f":"geojson"}, timeout=12).json()
            feats = js.get("features", [])
            if not feats: miss.append(lp); continue
            wkid = feats[0]["geometry"].get("spatialReference",{}).get("wkid",4326)
            tfm = Transformer.from_crs(wkid,4326,always_xy=True).transform if wkid!=4326 else None
            geoms, props = [], {}
            for ft in feats:
                g = shape(ft["geometry"]); geoms.append(transform(tfm,g) if tfm else g)
                props = ft["properties"]
            out[lp] = {"geom": unary_union(geoms), "props": props}
        except Exception: miss.append(lp)
    return out, miss

def kml_colour(hexrgb,p): r,g,b=hexrgb[1:3],hexrgb[3:5],hexrgb[5:7];a=int(round(255*p/100));return f"{a:02x}{b}{g}{r}"

# ─── sidebar query panel ────────────────────────────────────────────────
with st.sidebar:
    st.header("Query")
    ids_txt = st.text_area("Lot/Plan IDs", height=120, placeholder="6RP702264\n5//DP123456")
    fx = st.color_picker("Fill",  st.session_state.get("style",{}).get("fill","#ff6600"))
    lx = st.color_picker("Line",  st.session_state.get("style",{}).get("line","#2e2e2e"))
    fo = st.slider("Fill opacity %",0,100, st.session_state.get("style",{}).get("op",70))
    lw = st.slider("Line width px", 0.5,6.0, st.session_state.get("style",{}).get("w",1.2),0.1)
    if st.button("🔍 Search") and ids_txt.strip():
        ids=[s.strip() for s in re.split(r"[,\n;]",ids_txt) if s.strip()]
        with st.spinner("Fetching…"):
            recs,miss=fetch_parcels(ids)
        if miss: st.warning("Not found: "+", ".join(miss))
        rows=[]
        for lp,r in recs.items():
            props=r["props"];lt=props.get("lottype") or props.get("PURPOSE") or "n/a"
            area=abs(geod.geometry_area_perimeter(r["geom"])[0])/1e4
            rows.append({"Lot/Plan":lp,"Lot Type":lt,"Area (ha)":round(area,2)})
        st.session_state["parcels"]=recs
        st.session_state["table"]=pd.DataFrame(rows)
        st.session_state["style"]=dict(fill=fx,line=lx,op=fo,w=lw)

    st.header("Layers")
    if cfg["basemaps"]:
        names=[b["name"] for b in cfg["basemaps"]]
        st.session_state["basemap"]=st.radio("Basemap",names,index=names.index(st.session_state["basemap"]))
    for o in cfg["overlays"]:
        st.session_state["ov_state"][o["name"]]=st.checkbox(o["name"],
            value=st.session_state["ov_state"][o["name"]])

# ─── map ────────────────────────────────────────────────────────────────
m=folium.Map(location=[-25,145],zoom_start=5,control_scale=True,tiles=None,width="100%",height="70vh")

if cfg["basemaps"]:
    b=next(bb for bb in cfg["basemaps"] if bb["name"]==st.session_state["basemap"])
    folium.TileLayer(b["url"],name=b["name"],attr=b["attr"],overlay=False).add_to(m)

for o in cfg["overlays"]:
    if not st.session_state["ov_state"][o["name"]]: continue
    if o["type"]=="wms":
        folium.raster_layers.WmsTileLayer(o["url"],layers=str(o["layers"]),transparent=True,
            fmt=o.get("fmt","image/png"),version="1.1.1",
            name=o["name"],attr=o["attr"]).add_to(m)
    else:
        folium.TileLayer(o["url"],name=o["name"],attr=o["attr"]).add_to(m)

bounds=[]
if "parcels" in st.session_state:
    sty=st.session_state["style"];f=lambda _:{'fillColor':sty["fill"],'color':sty["line"],
        'weight':sty["w"],'fillOpacity':sty["op"]/100}
    pg=folium.FeatureGroup(name="Parcels",show=True).add_to(m)
    for lp,r in st.session_state["parcels"].items():
        g,p=r["geom"],r["props"]
        lt=p.get("lottype") or p.get("PURPOSE") or "n/a"
        area=abs(geod.geometry_area_perimeter(g)[0])/1e4
        html=(f"<b>Lot/Plan:</b> {lp}<br><b>Lot Type:</b> {lt}<br><b>Area:</b> {area:,.2f} ha")
        folium.GeoJson(mapping(g),style_function=f,tooltip=lp,popup=html).add_to(pg)
        bounds.append([[g.bounds[1],g.bounds[0]],[g.bounds[3],g.bounds[2]]])
if bounds:
    xs,ys,xe,ye=zip(*[(b[0][1],b[0][0],b[1][1],b[1][0]) for b in bounds])
    m.fit_bounds([[min(ys),min(xs)],[max(ye),max(xe)]])

st_data=st_folium(m,height=550,use_container_width=True,key="fol")

# ─── results table + Export-ALL bar ──────────────────────────────────────
if "table" in st.session_state and not st.session_state["table"].empty:
    st.subheader("Results")
    gdf=gpd.GeoDataFrame(st.session_state["table"],
        geometry=[r["geom"] for r in st.session_state["parcels"].values()],crs=4326)

    gob=GridOptionsBuilder.from_dataframe(gdf.drop(columns="geometry"))
    gob.configure_selection("multiple",use_checkbox=True)
    gob.configure_grid_options(getContextMenuItems="""
      function(p){return ['copy','separator',
        {name:'Zoom', action:()=>window.postMessage({type:'zoom'})},
        {name:'Pulse',action:()=>window.postMessage({type:'pulse'})},
        {name:'Buffer 200 m',action:()=>window.postMessage({type:'buffer'})},
        'separator',
        {name:'Export CSV',action:()=>window.postMessage({type:'csv'})},
        {name:'Export XLSX',action:()=>window.postMessage({type:'xlsx'})},
        {name:'Export Shapefile',action:()=>window.postMessage({type:'shp'})},
        'separator',
        {name:'Remove',action:()=>window.postMessage({type:'remove'})}
      ]; }""")

    grid=AgGrid(gdf.drop(columns="geometry"),gridOptions=gob.build(),
                update_mode=GridUpdateMode.MODEL_CHANGED,allow_unsafe_jscode=True,
                height=260)

    col1,col2,col3=st.columns(3)
    col1.download_button("Export ALL CSV",
        st.session_state["table"].to_csv(index=False).encode(),
        "parcels.csv","text/csv")
    bio=io.BytesIO(); st.session_state["table"].to_excel(bio,index=False); bio.seek(0)
    col2.download_button("Export ALL XLSX", bio.getvalue(),"parcels.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    tmp=tempfile.mkdtemp(); gdf.to_file(tmp+"/all.shp")
    zipf=pathlib.Path(tmp,"all.zip")
    with zipfile.ZipFile(zipf,"w",zipfile.ZIP_DEFLATED) as z:
        for f in pathlib.Path(tmp).glob("all.*"): z.write(f,f.name)
    col3.download_button("Export ALL SHP", open(zipf,"rb").read(),
                         "parcels.zip","application/zip")

    # — handle row-level actions (zoom, pulse, export selected, remove) —
    def handle(action, rows):
        if not rows: st.warning("No rows selected"); return
        ids=[r["Lot/Plan"] for r in rows]
        geoms=[st.session_state["parcels"][i]["geom"] for i in ids]
        if action=="zoom":
            bb=gpd.GeoSeries(geoms).total_bounds
            xs,ys,xe,ye=bb; st.session_state["__zoom_bounds"]=[[ys,xs],[ye,xe]]
        elif action=="pulse":
            st.session_state["__pulse"]=[mapping(g) for g in geoms]
        elif action=="buffer":
            buf=gpd.GeoSeries(geoms,crs=4326).to_crs(3857).buffer(200).to_crs(4326)
            st.session_state["__buffer"]=buf.__geo_interface__
        elif action in {"csv","xlsx","shp"}:
            df=st.session_state["table"][st.session_state["table"]["Lot/Plan"].isin(ids)]
            if action=="csv":
                st.download_button("Download CSV", df.to_csv(index=False).encode(),
                    "selected.csv","text/csv",key=str(uuid.uuid4()))
            elif action=="xlsx":
                bio2=io.BytesIO(); df.to_excel(bio2,index=False); bio2.seek(0)
                st.download_button("Download XLSX", bio2.getvalue(),
                    "selected.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=str(uuid.dj
