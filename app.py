import io, re, requests, streamlit as st
from streamlit_folium import st_folium
import folium, simplekml
from shapely.geometry import shape, mapping
from shapely.ops import unary_union, transform
from pyproj import Transformer

# ---------- Cadastre endpoints ----------
QLD_URL = ("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
           "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW_URL = ("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
           "NSW_Cadastre/MapServer/9/query")

def fetch_geom(lotplan:str):
    is_qld = bool(re.match(r"^\d+[A-Z]{1,3}\d+$", lotplan,re.I))
    url,fld = (QLD_URL,"lotplan") if is_qld else (NSW_URL,"lotidstring")
    js = requests.get(url, params={"where":f"{fld}='{lotplan}'",
                                   "returnGeometry":"true","f":"geojson"},timeout=10).json()
    parts=[shape(f["geometry"]) for f in js.get("features",[])]
    if not parts: return None
    wkid = js["spatialReference"]["wkid"] if "spatialReference" in js else 4326
    if wkid!=4326:
        parts=[transform(Transformer.from_crs(wkid,4326,always_xy=True).transform,p) for p in parts]
    return unary_union(parts)

def kml_colour(hex_rgb:str,pct:int):   # '#rrggbb' + opacity ➜ 'aabbggrr'
    r,g,b = hex_rgb[1:3],hex_rgb[3:5],hex_rgb[5:7]
    a=int(round(255*pct/100))
    return f"{a:02x}{b}{g}{r}"

# ---------- Streamlit layout ----------
st.set_page_config(page_title="Lot/Plan → KML", layout="wide")

with st.sidebar:
    st.title("≡ Controls")
    lot_text = st.text_area("Lot/Plan IDs", height=150,
                            placeholder="6RP702264\n5//DP123456")
    basemap = st.selectbox("Basemap",
        {"Esri Imagery":"ESRI_IMG","Esri Topo":"ESRI_TOPO","OpenStreetMap":"OSM"})
    fill_hex  = st.color_picker("Fill colour","#ff6600")
    fill_op   = st.number_input("Fill opacity %",0,100,70)
    line_hex  = st.color_picker("Outline colour","#2e2e2e")
    line_w    = st.number_input("Outline width px",0.5,6.0,1.2,step=0.1)
    folder    = st.text_input("Folder name in KML","Parcels")
    run       = st.button("🔍 Search lots", use_container_width=True)

# ---------- Fetch parcels ----------
if run and lot_text.strip():
    ids=[i.strip() for i in lot_text.splitlines() if i.strip()]
    geoms,missing={},[]
    with st.spinner("Fetching…"):
        for lp in ids:
            g=fetch_geom(lp)
            (geoms if g else missing.append(lp)) and (geoms.update({lp:g}) if g else None)
    if missing: st.sidebar.warning("Not found: "+", ".join(missing))
    st.session_state["geoms"]=geoms
    st.session_state["style"]=dict(fill=fill_hex,op=fill_op,line=line_hex,
                                   w=line_w, folder=folder, basemap=basemap)

# ---------- Make map ----------
m = folium.Map(location=[-25,145], zoom_start=5, control_scale=True)

# 1) HTTPS OSM fallback
folium.TileLayer(
    tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    attr="© OpenStreetMap", name="OSM", control=False
).add_to(m)

# 2) Optional Esri overlays
bm = st.session_state.get("style",{}).get("basemap","ESRI_IMG")
if bm=="ESRI_IMG":
    folium.TileLayer(
        tiles=("https://services.arcgisonline.com/ArcGIS/rest/services/"
               "World_Imagery/MapServer/tile/{z}/{y}/{x}"),
        attr="© Esri", name="Esri Imagery").add_to(m)
elif bm=="ESRI_TOPO":
    folium.TileLayer(
        tiles=("https://services.arcgisonline.com/ArcGIS/rest/services/"
               "World_Topo_Map/MapServer/tile/{z}/{y}/{x}"),
        attr="© Esri", name="Esri Topo").add_to(m)

# 3) Parcels
if "geoms" in st.session_state and st.session_state["geoms"]:
    s = st.session_state["style"]
    style = lambda _:{'fillColor':s['fill'],'color':s['line'],
                      'weight':s['w'],'fillOpacity':s['op']/100}
    for lp,g in st.session_state["geoms"].items():
        folium.GeoJson(mapping(g),style_function=style).add_child(
            folium.Popup(lp)).add_to(m)

st_folium(m, height=700, use_container_width=True)

# ---------- KML download ----------
if ("geoms" in st.session_state and st.session_state["geoms"]
    and st.sidebar.button("📥 Download KML", use_container_width=True)):
    s=st.session_state["style"]; kml=simplekml.Kml(); fld=kml.newfolder(name=s["folder"])
    fk,lk=kml_colour(s["fill"],s["op"]),kml_colour(s["line"],100)
    for lp,g in st.session_state["geoms"].items():
        p=fld.newpolygon(name=lp, outerboundaryis=mapping(g)["coordinates"][0])
        p.style.polystyle.color=fk; p.style.linestyle.color=lk; p.style.linestyle.width=float(s["w"])
    st.sidebar.download_button("Save KML",
        io.BytesIO(kml.kml().encode()).getvalue(),"parcels.kml",
        "application/vnd.google-earth.kml+xml",use_container_width=True)
