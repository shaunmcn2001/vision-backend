# app.py  –  LAWD Parcel Toolkit
# ------------------------------------------
import io, re, yaml, pathlib, uuid, tempfile, zipfile, requests, streamlit as st
from collections import defaultdict
from streamlit_option_menu import option_menu
from streamlit_folium import st_folium
import folium, simplekml, geopandas as gpd
import fiona                                # ← direct import for KML driver
from azure.storage.blob import BlobClient
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod

# enable KML read–write via Fiona
fiona.drvsupport.supported_drivers["KML"] = "rw"

# ---------- YAML registry helpers ---------------------------------
REG_PATH = pathlib.Path("layers.yaml")
@st.cache_data
def load_registry():
    return yaml.safe_load(REG_PATH.read_text())
def save_registry(cfg):
    REG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))
    st.cache_data.clear()
layers_cfg = load_registry()

# ---------- nice glass-dark overlay -------------------------------
def show_overlay(msg: str, pct: int | None):
    """Create/update/remove a full-page overlay with a progress bar.
       pct=None removes it."""
    key = "upload_overlay"
    if pct is None:
        st.session_state.pop(key, None)
        return
    st.markdown(f"""
    <style>
    .overlay {{
      position:fixed; inset:0; z-index:9999;
      backdrop-filter: blur(4px) brightness(.35);
      display:flex; flex-direction:column;
      justify-content:center; align-items:center;
    }}
    .bar {{width:60%;max-width:400px;height:10px;
           background:#555;border-radius:6px;overflow:hidden;
           box-shadow:0 0 10px #000 inset}}
    .bar>div {{height:100%;width:{pct}% ;
               background:linear-gradient(90deg,#ff6600 0%,#ffaa00 100%);
               transition:width .25s}}
    .txt {{color:#fff;font-weight:600;margin-top:18px}}
    </style>
    <div class='overlay'>
       <div class='bar'><div></div></div>
       <div class='txt'>{msg}</div>
    </div>""", unsafe_allow_html=True, key=key)

# ---------- Azure upload helper -----------------------------------
def upload_to_azure(uploaded_file) -> str:
    acct = st.secrets["AZ_ACCOUNT"]
    cont = st.secrets["AZ_CONTAINER"]
    sas  = st.secrets.get("AZ_SAS", "")

    tmp = tempfile.mkdtemp()
    raw = pathlib.Path(tmp) / uploaded_file.name
    raw.write_bytes(uploaded_file.read())

    if raw.suffix.lower() == ".zip":
        with zipfile.ZipFile(raw) as z: z.extractall(tmp)
        shp = next(pathlib.Path(tmp).glob("*.shp"))
        gdf = gpd.read_file(shp)
    else:
        gdf = gpd.read_file(raw)

    geo = pathlib.Path(tmp) / (uuid.uuid4().hex + ".geojson")
    gdf.to_file(geo, driver="GeoJSON")

    bc = BlobClient(
        account_url=f"https://{acct}.blob.core.windows.net",
        container_name=cont,
        blob_name=geo.name,
        credential=sas or None)
    bc.upload_blob(geo.read_bytes(), overwrite=True,
                   content_type="application/geo+json")
    return bc.url if sas == "" else f"{bc.url}?{sas}"

# ---------- Streamlit page / theme --------------------------------
st.set_page_config(page_title="Lot/Plan → KML", page_icon="📍",
                   layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<div style='background:#ff6600;color:white;font-size:20px;font-weight:600;
            padding:6px 20px;border-radius:8px;margin-bottom:6px'>
  LAWD – Parcel Toolkit
</div>""", unsafe_allow_html=True)

st.markdown("""
<style>
div[data-testid='stSidebar']{width:320px}
#main_map iframe{border-radius:12px;box-shadow:0 4px 14px rgba(0,0,0,.25)}
</style>""", unsafe_allow_html=True)

# ---------- cadastre endpoints / helpers --------------------------
QLD_URL = ("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
           "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW_URL = ("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
           "NSW_Cadastre/MapServer/9/query")
geod = Geod(ellps="WGS84")

def fetch_geoms(lotplans):
    grouped, missing = defaultdict(list), []
    is_qld = lambda lp: bool(re.match(r"^\d+[A-Z]{1,3}\d+$", lp, re.I))
    for lp in lotplans:
        url, fld = (QLD_URL,"lotplan") if is_qld(lp) else (NSW_URL,"lotidstring")
        try:
            js = requests.get(url, params={"where":f"{fld}='{lp}'",
                                           "returnGeometry":"true","f":"geojson"},
                              timeout=12).json()
            feats = js.get("features", [])
            if not feats:
                missing.append(lp); continue
            wkid = js.get("spatialReference",{}).get("wkid") \
                   or feats[0]["geometry"].get("spatialReference",{}).get("wkid",4326)
            tfm = (Transformer.from_crs(wkid,4326,always_xy=True).transform
                   if wkid!=4326 else None)
            for ft in feats:
                geom = shape(ft["geometry"])
                grouped[lp].append(transform(tfm, geom) if tfm else geom)
        except Exception:
            missing.append(lp)
    return {lp: unary_union(gs) for lp,gs in grouped.items()}, missing

def kml_colour(hex_rgb,pct):
    r,g,b = hex_rgb[1:3],hex_rgb[3:5],hex_rgb[5:7]
    a = int(round(255*pct/100))
    return f"{a:02x}{b}{g}{r}"

# ---------- sidebar nav -------------------------------------------
with st.sidebar:
    tab = option_menu(None,["Query","Layers","Downloads"],
                      icons=["search","layers","download"],
                      default_index=0,
                      styles={"container":{"padding":"0","background":"#262730"},
                              "nav-link-selected":{"background":"#ff6600"}})

st.session_state.setdefault("basemap", layers_cfg["basemaps"][0]["name"])
st.session_state.setdefault("overlay_state",
    {ov["name"]: False for ov in layers_cfg["overlays"]})

# ---------- TAB: QUERY --------------------------------------------
if tab=="Query":
    st.sidebar.subheader("Lot/Plan search")
    ids_text = st.sidebar.text_area("IDs",height=140,
                                    placeholder="6RP702264\n5//DP123456")
    fill_hex = st.sidebar.color_picker("Fill colour","#ff6600")
    fill_op  = st.sidebar.number_input("Fill opacity %",0,100,70)
    line_hex = st.sidebar.color_picker("Outline colour","#2e2e2e")
    line_w   = st.sidebar.number_input("Outline width px",.5,6.,1.2,.1)
    folder   = st.sidebar.text_input("Folder name in KML","Parcels")
    if st.sidebar.button("🔍 Search",use_container_width=True) and ids_text.strip():
        ids=[i.strip() for i in ids_text.splitlines() if i.strip()]
        with st.spinner("Fetching parcels…"):
            geoms, miss = fetch_geoms(ids)
        if miss:
            st.sidebar.warning("Not found: " + ", ".join(miss))
        st.session_state["geoms"]=geoms
        st.session_state["style"]=dict(fill=fill_hex,op=fill_op,
                                       line=line_hex,w=line_w,folder=folder)
        st.sidebar.info(f"Loaded {len(geoms)} parcel"
                        f"{'' if len(geoms)==1 else 's'}.")

# ---------- TAB: LAYERS -------------------------------------------
if tab=="Layers":
    st.sidebar.subheader("Basemap")
    bnames=[b["name"] for b in layers_cfg["basemaps"]]
    st.session_state["basemap"]=st.sidebar.radio(
        "",bnames,index=bnames.index(st.session_state["basemap"]))
    st.sidebar.subheader("Overlays")
    for ov in layers_cfg["overlays"]:
        cur=st.session_state["overlay_state"].get(ov["name"],False)
        st.session_state["overlay_state"][ov["name"]] = st.sidebar.checkbox(
            ov["name"], value=cur)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### ⬆ Upload local vector")
    up_file = st.sidebar.file_uploader(
        "GeoJSON / KML / KMZ / Shapefile ZIP",
        type=["geojson","json","kml","kmz","zip"])
    up_name = st.sidebar.text_input("Display name")

    if st.sidebar.button("Save layer") and up_file and up_name:
        try:
            show_overlay("Uploading…", 5)
            url = upload_to_azure(up_file)

            show_overlay("Registering layer…", 70)
            cfg = load_registry()
            cfg["overlays"].append({"name":up_name,"type":"geojson",
                                    "url":url,"attr":"© Uploaded"})
            save_registry(cfg)
            st.session_state["overlay_state"][up_name] = True
            show_overlay(None, None)
            st.sidebar.success("Layer uploaded & registered.")
        except Exception as e:
            show_overlay(None, None)
            st.sidebar.error(f"Upload failed: {e}")

# ---------- build map (unchanged) ---------------------------------
m = folium.Map(location=[-25,145], zoom_start=5,
               control_scale=True, width="100%", height="100vh")
b_cfg = next(b for b in layers_cfg["basemaps"]
             if b["name"]==st.session_state["basemap"])
folium.TileLayer(b_cfg["url"], name=b_cfg["name"],
                 attr=b_cfg["attr"]).add_to(m)

overlay_bounds=[]
for ov in layers_cfg["overlays"]:
    if not st.session_state["overlay_state"].get(ov["name"]): continue
    try:
        if ov["type"]=="wms":
            folium.raster_layers.WmsTileLayer(
                ov["url"], layers=str(ov["layers"]),
                fmt=ov.get("fmt","image/png"), transparent=True,
                name=ov["name"], attr=ov["attr"]).add_to(m)
        elif ov["type"]=="tile":
            folium.TileLayer(ov["url"], name=ov["name"],
                             attr=ov["attr"]).add_to(m)
        elif ov["type"]=="geojson":
            gj = folium.GeoJson(ov["url"], name=ov["name"]).add_to(m)
            overlay_bounds.append(gj.get_bounds())
    except Exception as e:
        st.warning(f"{ov['name']} failed: {e}")

parcel_bounds=[]
parcel_group = folium.FeatureGroup(name="Parcels", show=True).add_to(m)
if "geoms" in st.session_state:
    s=st.session_state["style"]
    sty = lambda _:{'fillColor':s['fill'],'color':s['line'],
                    'weight':s['w'],'fillOpacity':s['op']/100}
    for lp,g in st.session_state["geoms"].items():
        folium.GeoJson(mapping(g), style_function=sty, name=lp
                      ).add_child(folium.Popup(lp)).add_to(parcel_group)
        parcel_bounds.append(g.bounds)

if parcel_bounds:
    minx=min(b[0] for b in parcel_bounds); miny=min(b[1] for b in parcel_bounds)
    maxx=max(b[2] for b in parcel_bounds); maxy=max(b[3] for b in parcel_bounds)
    m.fit_bounds([[miny,minx],[maxy,maxx]])
elif overlay_bounds:
    m.fit_bounds(overlay_bounds[0])

st_folium(m, height=700, use_container_width=True, key="main_map")

# ---------- TAB: DOWNLOADS (unchanged) ----------------------------
if tab=="Downloads":
    st.sidebar.subheader("Export")
    if "geoms" in st.session_state and st.session_state["geoms"]:
        if st.sidebar.button("💾 Generate KML", use_container_width=True):
            s=st.session_state["style"]; geoms=st.session_state["geoms"]
            kml=simplekml.Kml(); root=kml.newfolder(name=s["folder"])
            fk,lk=kml_colour(s["fill"],s["op"]),kml_colour(s["line"],100)
            for lp,geom in geoms.items():
                polys=[geom] if isinstance(geom,Polygon) else list(geom.geoms)
                for idx,poly in enumerate(polys,1):
                    area_ha=abs(geod.geometry_area_perimeter(poly)[0])/1e4
                    name=f"{lp} ({idx})" if len(polys)>1 else lp
                    desc=f"Lot/Plan: {lp}<br>Area: {area_ha:,.2f} ha"
                    p=root.newpolygon(name=name,description=desc,
                                      outerboundaryis=list(poly.exterior.coords))
                    for ring in poly.interiors:
                        p.innerboundaryis.append(list(ring.coords))
                    p.style.polystyle.color=fk; p.style.linestyle.color=lk
                    p.style.linestyle.width=float(s["w"])
            st.sidebar.download_button("Save KML",
                io.BytesIO(kml.kml().encode()).getvalue(),
                "parcels.kml",
                "application/vnd.google-earth.kml+xml",
                use_container_width=True)
    else:
        st.sidebar.info("Load parcels in the Query tab first.")
