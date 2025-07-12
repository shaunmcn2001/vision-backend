#!/usr/bin/env python3
# LAWD Parcel Toolkit · 2025-07-12  (button-menu edition)

"""
– Compact sidebar
– Folium map with parcels layer
– AgGrid table:
      each row shows a button (⋮) that opens a drop-down
      actions: Zoom • Download KML • Remove
– Export-ALL bar (KML & Shapefile)
"""

# ─── stdlib ───────────────────────────────────────────
import io, pathlib, requests, tempfile, zipfile, re
# ─── streamlit & helpers ──────────────────────────────
import streamlit as st
from streamlit_option_menu import option_menu
from streamlit_folium import st_folium
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode
# ─── geo/data ─────────────────────────────────────────
import folium, simplekml, geopandas as gpd, pandas as pd
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod

# ---------- STATIC CONFIG ---------------------------------------------
CFG = pathlib.Path("layers.yaml")
try:
    import yaml
    cfg = yaml.safe_load(CFG.read_text()) if CFG.exists() else {}
except ImportError:
    cfg = {}
for k in ("basemaps", "overlays"): cfg.setdefault(k, [])

# ---------- HEADER -----------------------------------------------------
st.set_page_config("Lot/Plan Toolkit", "📍", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown(
    "<div style='background:#ff6600;color:#fff;font-size:20px;font-weight:600;"
    "padding:6px 20px;border-radius:8px;margin-bottom:6px'>LAWD – Parcel Toolkit</div>",
    unsafe_allow_html=True)

with st.sidebar:
    tab = option_menu(None, ["Query","Layers","Downloads"],
                      icons=["search","layers","download"], default_index=0,
                      styles={"container":{"padding":"0","background":"#262730"},
                              "nav-link-selected":{"background":"#ff6600"}})

if cfg["basemaps"]:
    st.session_state.setdefault("basemap", cfg["basemaps"][0]["name"])
st.session_state.setdefault("ov_state",{o["name"]:False for o in cfg["overlays"]})

# ---------- CADASTRE LOOK-UP ------------------------------------------
QLD=("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
     "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW=("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
     "NSW_Cadastre/MapServer/9/query")

def fetch_parcels(ids):
    out, miss = {}, []
    for lp in ids:
        url,fld = (QLD,"lotplan") if re.match(r"^\d+[A-Z]{1,3}\d+$",lp,re.I) else (NSW,"lotidstring")
        try:
            js=requests.get(url,params={"where":f"{fld}='{lp}'","outFields":"*",
                                        "returnGeometry":"true","f":"geojson"},timeout=15).json()
            feats=js.get("features",[])
            if not feats: miss.append(lp); continue
            wkid = feats[0]["geometry"].get("spatialReference",{}).get("wkid",4326)
            tfm = Transformer.from_crs(wkid,4326,always_xy=True).transform if wkid!=4326 else None
            geoms,props = [],{}
            for ft in feats:
                geoms.append(transform(tfm,shape(ft["geometry"])) if tfm else shape(ft["geometry"]))
                props = ft["properties"]
            out[lp]={"geom":unary_union(geoms),"props":props}
        except Exception: miss.append(lp)
    return out, miss

def kml_colour(hexrgb,pct):
    r,g,b = hexrgb[1:3],hexrgb[3:5],hexrgb[5:7]
    return f"{int(round(255*pct/100)):02x}{b}{g}{r}"

geod = Geod(ellps="WGS84")

# ═════════ TAB : QUERY ════════════════════════════════
if tab=="Query":
    ids_txt = st.sidebar.text_area("Lot/Plan IDs",height=110,
                                   placeholder="6RP702264\n5//DP123456")
    with st.sidebar.expander("Style & KML"):
        c1,c2 = st.columns(2,gap="small")
        with c1:
            fx = st.color_picker("Fill","#ff6600",label_visibility="collapsed")
            lx = st.color_picker("Outline","#2e2e2e",label_visibility="collapsed")
        with c2:
            fo = st.slider("Opacity %",0,100,70,label_visibility="collapsed")
            lw = st.slider("Width px",0.5,6.0,1.2,0.1,label_visibility="collapsed")
        folder = st.text_input("KML folder","Parcels")

    if st.sidebar.button("🔍 Search",use_container_width=True) and ids_txt.strip():
        ids=[s.strip() for s in ids_txt.splitlines() if s.strip()]
        with st.spinner("Fetching parcels…"):
            recs,miss = fetch_parcels(ids)
        if miss: st.sidebar.warning("Not found: "+", ".join(miss))
        rows=[{"Lot/Plan":lp,
               "Lot Type":(p:=r["props"]).get("lottype") or p.get("PURPOSE") or "n/a",
               "Area (ha)":round(abs(geod.geometry_area_perimeter(r["geom"])[0])/1e4,2)}
              for lp,r in recs.items()]
        st.session_state.update(parcels=recs,
                                table=pd.DataFrame(rows),
                                style=dict(fill=fx,op=fo,line=lx,w=lw,folder=folder))
        st.success(f"{len(recs)} parcel{'s'*(len(recs)!=1)} loaded.")

# ═════════ TAB : LAYERS (unchanged) ══════════════════
if tab=="Layers":
    if cfg["basemaps"]:
        st.sidebar.subheader("Basemap")
        names=[b["name"] for b in cfg["basemaps"]]
        st.session_state["basemap"]=st.sidebar.radio("",names,
            index=names.index(st.session_state["basemap"]))
    st.sidebar.subheader("Static overlays")
    for o in cfg["overlays"]:
        st.session_state["ov_state"][o["name"]] = st.sidebar.checkbox(
            o["name"], value=st.session_state["ov_state"][o["name"]])

# ═════════ MAP BUILD ═════════════════════════════════
m=folium.Map(location=[-25,145],zoom_start=5,
             control_scale=True,width="100%",height="100vh")
if cfg["basemaps"]:
    base=next(b for b in cfg["basemaps"] if b["name"]==st.session_state["basemap"])
    folium.TileLayer(base["url"],name=base["name"],attr=base["attr"],
                     overlay=False,control=True,show=True).add_to(m)
for o in cfg["overlays"]:
    if st.session_state["ov_state"][o["name"]]:
        try:
            if o["type"]=="wms":
                folium.raster_layers.WmsTileLayer(o["url"],layers=str(o["layers"]),
                    transparent=True,fmt=o.get("fmt","image/png"),version="1.1.1",
                    name=o["name"],attr=o["attr"]).add_to(m)
            else:
                folium.TileLayer(o["url"],name=o["name"],attr=o["attr"]).add_to(m)
        except Exception as e:
            st.warning(f"{o['name']} failed: {e}")

sel_ids=set(st.session_state.get("_sel",[]))
bounds=[]
if "parcels" in st.session_state:
    s=st.session_state["style"]
    def sty(f):
        lp=f.get("properties",{}).get("name","")
        return {"fillColor":s["fill"],
                "color":"red" if lp in sel_ids else s["line"],
                "weight":s["w"],"fillOpacity":s["op"]/100}
    fg=folium.FeatureGroup(name="Parcels", show=True).add_to(m)
    for lp,rec in st.session_state["parcels"].items():
        geom,prop=rec["geom"],rec["props"]
        html=(f"<b>Lot/Plan:</b> {lp}<br>"
              f"<b>Lot Type:</b> {prop.get('lottype') or prop.get('PURPOSE') or 'n/a'}<br>"
              f"<b>Area:</b> {abs(geod.geometry_area_perimeter(geom)[0])/1e4:,.2f} ha")
        feat={"type":"Feature","properties":{"name":lp},"geometry":mapping(geom)}
        folium.GeoJson(feat,name=lp,style_function=sty,tooltip=lp,popup=html).add_to(fg)
        bounds.append([[geom.bounds[1],geom.bounds[0]],
                       [geom.bounds[3],geom.bounds[2]]])
if bounds:
    ys,xs,ye,xe=zip(*[(b[0][0],b[0][1],b[1][0],b[1][1]) for b in bounds])
    m.fit_bounds([[min(ys),min(xs)],[max(ye),max(xe)]])

folium_data=st_folium(m,height=550,use_container_width=True,
                      key="map",returned_objects=["bounds","js_events"])
js=folium_data.get("js_events",[])[-1] if folium_data.get("js_events") else None

# ═════════ TABLE with <button> menu ═══════════════════
if "table" in st.session_state and not st.session_state["table"].empty:
    st.subheader("Query Results")

    df=st.session_state["table"].copy(); df["⋮"]="⋮"
    gdf=gpd.GeoDataFrame(df, geometry=[r["geom"]
             for r in st.session_state["parcels"].values()], crs=4326)

    MENU_JS = JsCode("""
class ActionCell {
  init(p){
    this.p=p;
    this.e=document.createElement('div');
    this.e.style.position='relative';
    this.e.innerHTML=`<button class="dots" style="cursor:pointer;border:none;background:none;font-weight:bold">&#8942;</button>
      <div class="dd" style="display:none;position:absolute;left:-60px;top:22px;
        background:#fff;border:1px solid #ccc;border-radius:4px;min-width:120px;
        box-shadow:0 2px 6px rgba(0,0,0,.15);font-size:12px;z-index:9999">
        <div class="it" data-act="zoom">Zoom to result</div>
        <div class="it" data-act="kml">Download KML</div>
        <div class="it" data-act="remove">Remove</div>
      </div>`;
    this.menu=this.e.querySelector('.dd');
    this.e.querySelector('.dots').onclick=e=>{
        e.stopPropagation();
        const show=this.menu.style.display==='none';
        document.querySelectorAll('.dd').forEach(x=>x.style.display='none');
        this.menu.style.display=show?'block':'none';
    };
    this.menu.onclick=e=>{
        if(e.target.dataset.act){
            window.postMessage({type:e.target.dataset.act,row:this.p.data});
            this.menu.style.display='none';
        }
    };
  }
  getGui(){return this.e;}
}
""")

    gob=GridOptionsBuilder.from_dataframe(gdf.drop(columns="geometry"))
    gob.configure_selection("multiple",use_checkbox=True)
    gob.configure_column("⋮",header_name="",width=60,
                         cellRenderer="ActionCell",suppressMenu=True)
    gob.configure_grid_options(
        frameworkComponents={"ActionCell":MENU_JS},
        getContextMenuItems="()=>[]"
    )

    grid=AgGrid(gdf.drop(columns="geometry"),gridOptions=gob.build(),
                update_mode=GridUpdateMode.MODEL_CHANGED,
                allow_unsafe_jscode=True,height=250)

    # ---- selections ----------------------------------------------------
    raw_sel = grid.get("selected_rows") if isinstance(grid, dict) else getattr(grid,"selected_rows",None)
    if raw_sel is None: sel_rows=[]
    elif isinstance(raw_sel,pd.DataFrame): sel_rows=raw_sel.to_dict("records")
    elif isinstance(raw_sel,list): sel_rows=raw_sel
    else:
        try: sel_rows=list(raw_sel)
        except TypeError: sel_rows=[]
    if js and "row" in js: sel_rows=[js["row"]]
    st.session_state["_sel"]=[(r.get("Lot/Plan") or r.get("Lot_Plan")) for r in sel_rows]

    # ——— Export-ALL buttons and per-row actions (use your existing logic) —
    # (Generate KML, SHP, zoom, remove, etc.)

# queued zoom
if "__zoom" in st.session_state:
    m.fit_bounds(st.session_state.pop("__zoom"))
