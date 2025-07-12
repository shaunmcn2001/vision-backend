# app.py  –  LAWD Parcel Toolkit (private Azure container, blob-scope URLs)

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

# ─── YAML (basemaps, overlays, databases) ───────────────────────────
REG_PATH = pathlib.Path("layers.yaml")
def load_static():
    cfg = yaml.safe_load(REG_PATH.read_text())
    cfg.setdefault("basemaps",  [])
    cfg.setdefault("overlays",  [])
    cfg.setdefault("databases", [])
    return cfg
def save_static(cfg): REG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))
static_cfg = load_static()

# ─── Azure manifest helpers ─────────────────────────────────────────
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

# ─── overlay helper ─────────────────────────────────────────────────
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

# ─── upload vector → Azure (attach blob-scope token) ────────────────
def upload_vector_blob(uploaded_file):
    acct = st.secrets["AZ_ACCOUNT"]; cont = st.secrets["AZ_CONTAINER"]
    sas  = st.secrets.get("AZ_SAS", "")
    tmp  = tempfile.mkdtemp(); raw = pathlib.Path(tmp)/uploaded_file.name
    raw.write_bytes(uploaded_file.read())

    # read / unzip / reprojection
    if raw.suffix.lower()==".zip":
        with zipfile.ZipFile(raw) as z: z.extractall(tmp)
        shp = next(pathlib.Path(tmp).glob("*.shp"))
        gdf = gpd.read_file(shp)
    else:
        gdf = gpd.read_file(raw)
    if gdf.crs and gdf.crs.to_epsg()!=4326: gdf=gdf.to_crs(4326)

    uid = uuid.uuid4().hex
    geo = pathlib.Path(tmp)/f"{uid}.geojson"
    gdf.to_file(geo, driver="GeoJSON")

    bc = BlobClient(account_url=f"https://{acct}.blob.core.windows.net",
                    container_name=cont, blob_name=geo.name,
                    credential=sas or None)
    bc.upload_blob(geo.read_bytes(), overwrite=True,
                   content_type="application/geo+json")

    # SAS created at container scope (sr=c). For blob GET append sr=b.
    token = sas.replace("sr=c", "sr=b") if sas else ""
    url   = f"{bc.url}?{token}" if token else bc.url
    return url, uid

# ─── Streamlit page setup ───────────────────────────────────────────
st.set_page_config(page_title="Lot/Plan → KML", page_icon="📍",
                   layout="wide", initial_sidebar_state="collapsed")
st.markdown("<div style='background:#ff6600;color:white;font-size:20px;"
            "font-weight:600;padding:6px 20px;border-radius:8px;margin-bottom:6px'>"
            "LAWD – Parcel Toolkit</div>", unsafe_allow_html=True)
st.markdown("<style>div[data-testid='stSidebar']{width:320px}"
            "#main_map iframe{border-radius:12px;box-shadow:0 4px 14px rgba(0,0,0,.25)}"
            "</style>", unsafe_allow_html=True)

with st.sidebar:
    tab=option_menu(None,["Query","Layers","Downloads"],
                    icons=["search","layers","download"],
                    default_index=0,
                    styles={"container":{"padding":"0","background":"#262730"},
                            "nav-link-selected":{"background":"#ff6600"}})

if static_cfg["basemaps"]:
    st.session_state.setdefault("basemap",static_cfg["basemaps"][0]["name"])
st.session_state.setdefault("overlay_state",{o["name"]:False for o in static_cfg["overlays"]})
st.session_state.setdefault("db_state"     ,{d["name"]:False for d in static_cfg["databases"]})
st.session_state.setdefault("dyn_state"    ,{d["id"]:False for d in dynamic_cfg})

# ─── cadastre helpers ───────────────────────────────────────────────
QLD=("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
     "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW=("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
     "NSW_Cadastre/MapServer/9/query")
geod=Geod(ellps="WGS84")
def fetch(ids):
    grp,miss=defaultdict(list),[]
    for lp in ids:
        url,fld=(QLD,"lotplan") if re.match(r"^\d+[A-Z]{1,3}\d+$",lp,re.I) else (NSW,"lotidstring")
        try:
            js=requests.get(url,params={"where":f"{fld}='{lp}'","returnGeometry":"true","f":"geojson"},timeout=12).json()
            feats=js.get("features",[]); 
            if not feats: miss.append(lp); continue
            wkid=feats[0]["geometry"].get("spatialReference",{}).get("wkid",4326)
            tfm=Transformer.from_crs(wkid,4326,always_xy=True).transform if wkid!=4326 else None
            for ft in feats: grp[lp].append(transform(tfm,shape(ft["geometry"])) if tfm else shape(ft["geometry"]))
        except Exception: miss.append(lp)
    return {lp:unary_union(gs) for lp,gs in grp.items()}, miss
def kml_colour(h,p): r,g,b=h[1:3],h[3:5],h[5:7]; a=int(round(255*p/100)); return f"{a:02x}{b}{g}{r}"

# ─── TAB: Query ─────────────────────────────────────────────────────
if tab=="Query":
    ids_txt=st.sidebar.text_area("IDs",height=140,placeholder="6RP702264\n5//DP123456")
    fx=st.sidebar.color_picker("Fill","#ff6600"); fo=st.sidebar.number_input("Fill opacity %",0,100,70)
    lx=st.sidebar.color_picker("Outline","#2e2e2e"); lw=st.sidebar.number_input("Outline width px",.5,6.,1.2,.1)
    folder=st.sidebar.text_input("Folder name","Parcels")
    if st.sidebar.button("🔍 Search") and ids_txt.strip():
        ids=[i.strip() for i in ids_txt.splitlines() if i.strip()]
        with st.spinner("Fetching parcels…"): geoms,miss=fetch(ids)
        if miss: st.sidebar.warning("Not found: "+", ".join(miss))
        st.session_state["geoms"]=geoms; st.session_state["style"]=dict(fill=fx,op=fo,line=lx,w=lw,folder=folder)
        st.sidebar.info(f"Loaded {len(geoms)} parcel{'s'*(len(geoms)!=1)}.")

# ─── TAB: Layers ────────────────────────────────────────────────────
if tab=="Layers":
    if static_cfg["basemaps"]:
        st.sidebar.subheader("Basemap")
        bnames=[b["name"] for b in static_cfg["basemaps"]]
        st.session_state["basemap"]=st.sidebar.radio("",bnames,index=bnames.index(st.session_state["basemap"]))

    st.sidebar.subheader("Static overlays")
    for ov in static_cfg["overlays"]:
        st.session_state["overlay_state"][ov["name"]]=st.sidebar.checkbox(
            ov["name"],value=st.session_state["overlay_state"][ov["name"]])

    st.sidebar.subheader("Databases")
    for db in static_cfg["...
