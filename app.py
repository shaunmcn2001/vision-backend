# app.py  –  LAWD Parcel Toolkit  (private Azure container)

import io, re, json, yaml, pathlib, uuid, tempfile, zipfile, requests, streamlit as st
from collections import defaultdict
from streamlit_option_menu import option_menu
from streamlit_folium import st_folium
import folium, simplekml, geopandas as gpd, fiona
from azure.storage.blob import BlobClient
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod

# enable KML driver
fiona.drvsupport.supported_drivers["KML"] = "rw"

# ───────────────────────── Config from YAML ─────────────────────────
REG_PATH = pathlib.Path("layers.yaml")
def load_static():
    cfg = yaml.safe_load(REG_PATH.read_text())
    cfg.setdefault("basemaps",  [])
    cfg.setdefault("overlays",  [])
    cfg.setdefault("databases", [])
    return cfg
def save_static(cfg): REG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))
static_cfg = load_static()

# ───────────────────────── Azure helpers ────────────────────────────
def index_blob():
    acct = st.secrets["AZ_ACCOUNT"]; cont = st.secrets["AZ_CONTAINER"]
    sas  = st.secrets.get("AZ_SAS", "")
    return BlobClient(account_url=f"https://{acct}.blob.core.windows.net",
                      container_name=cont, blob_name="index.json",
                      credential=sas or None)

@st.cache_data
def load_dynamic():
    try:
        return json.loads(index_blob().download_blob().readall().decode())
    except Exception:
        return []
def save_dynamic(lst):
    index_blob().upload_blob(json.dumps(lst).encode(), overwrite=True,
                             content_type="application/json")
    st.cache_data.clear()
dynamic_cfg = load_dynamic()

# ─────────────────────── Overlay helper ─────────────────────────────
def show_overlay(msg: str, pct: int | None):
    ph = st.session_state.get("_ov")
    if pct is None:
        if ph: ph.empty(); st.session_state["_ov"] = None
        return
    html=f"""<style>
    .ov{{position:fixed;inset:0;z-index:9999;backdrop-filter:blur(4px)brightness(.35);
         display:flex;flex-direction:column;justify-content:center;align-items:center}}
    .bar{{width:60%;max-width:400px;height:10px;background:#555;border-radius:6px;
         overflow:hidden;box-shadow:0 0 10px #000 inset}}
    .bar>div{{width:{pct}% ;height:100%;
         background:linear-gradient(90deg,#ff6600 0%,#ffaa00 100%);
         transition:width .25s}}
    .txt{{color:#fff;font-weight:600;margin-top:18px}}
    </style>
    <div class='ov'><div class='bar'><div></div></div><div class='txt'>{msg}</div></div>"""
    if not ph: ph=st.empty(); st.session_state["_ov"]=ph
    ph.markdown(html, unsafe_allow_html=True)

# ─── Upload vector → Azure (attach blob-scope SAS) ──────────────────
def upload_vector_blob(uploaded_file):
    acct = st.secrets["AZ_ACCOUNT"]; cont = st.secrets["AZ_CONTAINER"]
    sas  = st.secrets.get("AZ_SAS", "")
    tmp  = tempfile.mkdtemp(); raw = pathlib.Path(tmp)/uploaded_file.name
    raw.write_bytes(uploaded_file.read())

    # read/unzip/reproject
    if raw.suffix.lower()==".zip":
        with zipfile.ZipFile(raw) as z: z.extractall(tmp)
        shp = next(pathlib.Path(tmp).glob("*.shp"))
        gdf = gpd.read_file(shp)
    else:
        gdf = gpd.read_file(raw)
    if gdf.crs and gdf.crs.to_epsg()!=4326:
        gdf = gdf.to_crs(4326)

    uid = uuid.uuid4().hex
    geo = pathlib.Path(tmp)/f"{uid}.geojson"
    gdf.to_file(geo, driver="GeoJSON")

    bc = BlobClient(account_url=f"https://{acct}.blob.core.windows.net",
                    container_name=cont, blob_name=geo.name,
                    credential=sas or None)
    bc.upload_blob(geo.read_bytes(), overwrite=True,
                   content_type="application/geo+json")

    # convert sr=c → sr=b for blob GET
    token = sas.replace("sr=c", "sr=b") if sas else ""
    url = f"{bc.url}?{token}" if token else bc.url
    return url, uid

# ───────────────────── Streamlit page setup ─────────────────────────
st.set_page_config(page_title="Lot/Plan → KML", page_icon="📍",
                   layout="wide", initial_sidebar_state="collapsed")
st.markdown("<div style='background:#ff6600;color:white;font-size:20px;"
            "font-weight:600;padding:6px 20px;border-radius:8px;margin-bottom:6px'>"
            "LAWD – Parcel Toolkit</div>", unsafe_allow_html=True)
st.markdown("<style>div[data-testid='stSidebar']{width:320px}"
            "#main_map iframe{border-radius:12px;box-shadow:0 4px 14px rgba(0,0,0,.25)}"
            "</style>", unsafe_allow_html=True)

with st.sidebar:
    tab = option_menu(None, ["Query","Layers","Downloads"],
                      icons=["search","layers","download"],
                      default_index=0,
                      styles={"container":{"padding":"0","background":"#262730"},
                              "nav-link-selected":{"background":"#ff6600"}})

# default state
if static_cfg["basemaps"]:
    st.session_state.setdefault("basemap", static_cfg["basemaps"][0]["name"])
st.session_state.setdefault("overlay_state",
    {ov["name"]: False for ov in static_cfg["overlays"]})
st.session_state.setdefault("db_state",
    {db["name"]: False for db in static_cfg["databases"]})
st.session_state.setdefault("dyn_state",
    {d["id"]: False for d in dynamic_cfg})

# ───────────────────── Cadastre helpers ────────────────────────────
QLD=("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
     "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW=("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
     "NSW_Cadastre/MapServer/9/query")
geod = Geod(ellps="WGS84")
def fetch(ids):
    grp, miss = defaultdict(list), []
    for lp in ids:
        url,fld=(QLD,"lotplan") if re.match(r"^\d+[A-Z]{1,3}\d+$",lp,re.I) else (NSW,"lotidstring")
        try:
            js=requests.get(url,params={"where":f"{fld}='{lp}'","returnGeometry":"true","f":"geojson"},timeout=12).json()
            feats=js.get("features",[]); 
            if not feats: miss.append(lp); continue
            wkid=feats[0]["geometry"].get("spatialReference",{}).get("wkid",4326)
            tfm=Transformer.from_crs(wkid,4326,always_xy=True).transform if wkid!=4326 else None
            for ft in feats:
                geom=shape(ft["geometry"]); grp[lp].append(transform(tfm,geom) if tfm else geom)
        except Exception:
            miss.append(lp)
    return {lp:unary_union(gs) for lp,gs in grp.items()}, miss
def kml_colour(h,p):
    r,g,b=h[1:3],h[3:5],h[5:7]; a=int(round(255*p/100)); return f"{a:02x}{b}{g}{r}"

# ───────────────────── TAB: Query ──────────────────────────────────
if tab=="Query":
    ids_txt=st.sidebar.text_area("IDs",height=140,placeholder="6RP702264\n5//DP123456")
    fx=st.sidebar.color_picker("Fill colour","#ff6600")
    fo=st.sidebar.number_input("Fill opacity %",0,100,70)
    lx=st.sidebar.color_picker("Outline colour","#2e2e2e")
    lw=st.sidebar.number_input("Outline width px",.5,6.,1.2,.1)
    folder=st.sidebar.text_input("Folder name","Parcels")

    if st.sidebar.button("🔍 Search") and ids_txt.strip():
        ids=[i.strip() for i in ids_txt.splitlines() if i.strip()]
        with st.spinner("Fetching parcels…"): geoms,miss=fetch(ids)
        if miss: st.sidebar.warning("Not found: "+", ".join(miss))
        st.session_state["geoms"]=geoms
        st.session_state["style"]=dict(fill=fx,op=fo,line=lx,w=lw,folder=folder)
        st.sidebar.info(f"Loaded {len(geoms)} parcel{'s'*(len(geoms)!=1)}.")

# ───────────────────── TAB: Layers ─────────────────────────────────
if tab=="Layers":
    # basemap
    if static_cfg["basemaps"]:
        st.sidebar.subheader("Basemap")
        names=[b["name"] for b in static_cfg["basemaps"]]
        st.session_state["basemap"]=st.sidebar.radio("",names,
            index=names.index(st.session_state["basemap"]))

    # overlays
    st.sidebar.subheader("Static overlays")
    for ov in static_cfg["overlays"]:
        st.session_state["overlay_state"][ov["name"]]=st.sidebar.checkbox(
            ov["name"],value=st.session_state["overlay_state"][ov["name"]])

    # databases
    st.sidebar.subheader("Databases")
    for db in static_cfg["databases"]:
        st.session_state["db_state"][db["name"]]=st.sidebar.checkbox(
            db["name"],value=st.session_state["db_state"][db["name"]])

    with st.sidebar.expander("➕ Add server layer"):
        n=st.text_input("Name"); u=st.text_input("URL …")
        t=st.selectbox("Type",["wms","tile"])
        l=st.text_input("Layers (WMS only)"); a=st.text_input("Attribution","© Source")
        if st.button("Add to database") and n and u:
            static_cfg["databases"].append({"name":n,"type":t,"url":u,"layers":l,"attr":a})
            save_static(static_cfg); st.rerun()

    # uploads
    st.sidebar.subheader("My uploads")
    loaded=[d for d in dynamic_cfg if st.session_state["dyn_state"].get(d["id"])]
    avail =[d for d in dynamic_cfg if not st.session_state["dyn_state"].get(d["id"])]

    if loaded:
        for d in loaded:
            st.session_state["dyn_state"][d["id"]]=st.sidebar.checkbox(
                d["name"],value=True,key=f"dyn_{d['id']}")
    else:
        st.sidebar.info("No uploads loaded.")

    if avail:
        sel=st.sidebar.selectbox("Add saved layer",[d["name"] for d in avail])
        if st.sidebar.button("Load layer"):
            d=next(a for a in avail if a["name"]==sel)
            st.session_state["dyn_state"][d["id"]]=True; st.rerun()
    else: st.sidebar.caption("All saved layers are loaded.")

    # new upload
    st.sidebar.markdown("---")
    st.sidebar.markdown("### ⬆ Upload local vector")
    up_file=st.sidebar.file_uploader("GeoJSON / KML / KMZ / Shapefile ZIP",
                                     type=["geojson","json","kml","kmz","zip"])
    up_name=st.sidebar.text_input("Display name")
    if st.sidebar.button("Save layer") and up_file and up_name:
        try:
            show_overlay("Uploading…",10)
            url,uid=upload_vector_blob(up_file)
            show_overlay("Updating list…",80)
            dynamic_cfg.append({"id":uid,"name":up_name,"url":url})
            save_dynamic(dynamic_cfg)
            st.session_state["dyn_state"][uid]=True
            show_overlay(None,None); st.sidebar.success("Layer uploaded & loaded."); st.rerun()
        except Exception as e:
            show_overlay(None,None); st.sidebar.error(f"Upload failed: {e}")

# ───────────────────── Build Folium map ────────────────────────────
m=folium.Map(location=[-25,145],zoom_start=5,control_scale=True,
             width="100%",height="100vh")
if static_cfg["basemaps"]:
    b=next(bb for bb in static_cfg["basemaps"] if bb["name"]==st.session_state["basemap"])
    folium.TileLayer(b["url"],name=b["name"],attr=b["attr"]).add_to(m)

bounds=[]

for ov in static_cfg["overlays"]:
    if st.session_state["overlay_state"][ov["name"]]:
        try:
            if ov["type"]=="wms":
                folium.raster_layers.WmsTileLayer(
                    ov["url"],layers=str(ov["layers"]),transparent=True,
                    fmt=ov.get("fmt","image/png"),name=ov["name"],attr=ov["attr"]).add_to(m)
            else:
                folium.TileLayer(ov["url"],name=ov["name"],attr=ov["attr"]).add_to(m)
        except Exception as e:
            st.warning(f"{ov['name']} failed: {e}")

for db in static_cfg["databases"]:
    if st.session_state["db_state"][db["name"]]:
        try:
            if db["type"]=="wms":
                folium.raster_layers.WmsTileLayer(
                    db["url"],layers=str(db["layers"]),transparent=True,
                    fmt=db.get("fmt","image/png"),name=db["name"],attr=db["attr"]).add_to(m)
            else:
                folium.TileLayer(db["url"],name=db["name"],attr=db["attr"]).add_to(m)
        except Exception as e:
            st.warning(f"{db['name']} failed: {e}")

for d in dynamic_cfg:
    if st.session_state["dyn_state"].get(d["id"]):
        try:
            gj=folium.GeoJson(d["url"],name=d["name"]).add_to(m)
            bounds.append(gj.get_bounds())
        except Exception as e:
            st.warning(f"{d['name']} failed: {e}")

parcel_bounds=[]
pg=folium.FeatureGroup(name="Parcels",show=True).add_to(m)
if "geoms" in st.session_state:
    s=st.session_state["style"]
    sty=lambda _:{'fillColor':s['fill'],'color':s['line'],
                  'weight':s['w'],'fillOpacity':s['op']/100}
    for lp,g in st.session_state["geoms"].items():
        folium.GeoJson(mapping(g),style_function=sty,name=lp
                      ).add_child(folium.Popup(lp)).add_to(pg)
        parcel_bounds.append(g.bounds)

if parcel_bounds:
    minx=min(b[0] for b in parcel_bounds); miny=min(b[1] for b in parcel_bounds)
    maxx=max(b[2] for b in parcel_bounds); maxy=max(b[3] for b in parcel_bounds)
    m.fit_bounds([[miny,minx],[maxy,maxx]])
elif bounds:
    m.fit_bounds(bounds[0])

st_folium(m,height=700,use_container_width=True,key="main_map")

# ───────────────────── TAB: Downloads ──────────────────────────────
if tab=="Downloads":
    st.sidebar.subheader("Export")
    if "geoms" in st.session_state and st.session_state["geoms"]:
        if st.sidebar.button("💾 Generate KML"):
            s=st.session_state["style"]; gs=st.session_state["geoms"]
            kml=simplekml.Kml(); folder=kml.newfolder(name=s["folder"])
            fk,lk=kml_colour(s['fill'],s['op']),kml_colour(s['line'],100)
            for lp,g in gs.items():
                polys=[g] if isinstance(g,Polygon) else list(g.geoms)
                for i,p in enumerate(polys,1):
                    area=abs(geod.geometry_area_perimeter(p)[0])/1e4
                    nm=f"{lp} ({i})" if len(polys)>1 else lp
                    desc=f"Lot/Plan: {lp}<br>Area: {area:,.2f} ha"
                    pl=folder.newpolygon(name=nm,description=desc,
                                         outerboundaryis=p.exterior.coords)
                    for ring in p.interiors: pl.innerboundaryis.append(ring.coords)
                    pl.style.polystyle.color=fk; pl.style.linestyle.color=lk; pl.style.linestyle.width=float(s['w'])
            st.sidebar.download_button("Save KML",
                io.BytesIO(kml.kml().encode()).getvalue(),"parcels.kml",
                "application/vnd.google-earth.kml+xml")
    else:
        st.sidebar.info("Load parcels in Query first.")
