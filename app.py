import io, re, requests
import streamlit as st
from streamlit_folium import st_folium
import folium
from folium.plugins import MeasureControl
from shapely.geometry import shape, mapping
from shapely.ops import unary_union, transform
from pyproj import Transformer
import simplekml

# ─── ArcGIS parcel layers ─────────────────────────────
QLD_URL = ("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
           "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW_URL = ("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
           "NSW_Cadastre/MapServer/9/query")

def fetch_merged_geom(lotplan:str):
    is_qld = bool(re.match(r"^\d+[A-Z]{1,3}\d+$", lotplan, re.I))
    url,fld = (QLD_URL,"lotplan") if is_qld else (NSW_URL,"lotidstring")
    js = requests.get(url, params={"where":f"{fld}='{lotplan}'",
                                   "returnGeometry":"true","f":"geojson"},timeout=15).json()
    feats = js.get("features", [])
    if not feats: return None
    parts=[]
    for f in feats:
        g = shape(f["geometry"])
        wkid = f["geometry"].get("spatialReference",{}).get("wkid",4326)
        if wkid!=4326:
            g=transform(Transformer.from_crs(wkid,4326,always_xy=True).transform,g)
        parts.append(g)
    return unary_union(parts)

def kml_colour(hex_rgb:str, pct:int):
    r,g,b = hex_rgb[1:3],hex_rgb[3:5],hex_rgb[5:7]
    a = int(round(255*pct/100))
    return f"{a:02x}{b}{g}{r}"

# ─── Streamlit page ───────────────────────────────────
st.set_page_config(page_title="Lot/Plan → KML", layout="wide")
col_map,col_ctrl = st.columns([3,1],gap="large")

# ─── controls (right) ────────────────────────────────
with col_ctrl:
    st.markdown("### Parcel search")
    lot_text   = st.text_area("Lot/Plan IDs", height=150,
                              placeholder="6RP702264\n5//DP123456")
    poly_hex   = st.color_picker("Fill colour","#ff6600")
    poly_op    = st.number_input("Fill opacity %",0,100,70)
    line_hex   = st.color_picker("Outline colour","#2e2e2e")
    line_w     = st.number_input("Outline width px",0.1,10.0,1.2,step=0.1)
    folder     = st.text_input("Folder name","Parcels")
    do_search  = st.button("🔍 Search lots", use_container_width=True)

# ─── fetch geometries ────────────────────────────────
if do_search and lot_text.strip():
    ids=[i.strip() for i in lot_text.splitlines() if i.strip()]
    geoms, missing={},[]
    with st.spinner("Fetching parcels…"):
        for lp in ids:
            g=fetch_merged_geom(lp)
            (geoms if g else missing.append(lp)) and (geoms.update({lp:g}) if g else None)
    if missing: st.warning("No parcel found for: "+", ".join(missing))
    st.session_state["lot_geoms"]=geoms
    st.session_state["style"]=dict(fill=poly_hex,op=poly_op,
                                   line=line_hex,w=line_w,folder=folder or "Parcels")

# ─── map (left) ───────────────────────────────────────
with col_map:
    st.markdown("### Preview (drag / scroll wheel)")
    m = folium.Map(location=[-25,145], zoom_start=5, control_scale=True)
    # Basemaps
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    folium.TileLayer(
        tiles=("https://services.arcgisonline.com/ArcGIS/rest/services/"
               "World_Imagery/MapServer/tile/{z}/{y}/{x}"),
        attr="© Esri",
        name="Esri Imagery",
    ).add_to(m)
    folium.TileLayer(
        tiles=("https://services.arcgisonline.com/ArcGIS/rest/services/"
               "World_Topo_Map/MapServer/tile/{z}/{y}/{x}"),
        attr="© Esri",
        name="Esri Topo",
    ).add_to(m)

    # Measure & Esri geocoder plugins
    m.add_child(MeasureControl(position="topleft", primary_length_unit='kilometers',
                               primary_area_unit='hectares'))
    folium.plugins.Geocoder(
        collapsed=False, add_marker=True, position="topleft",
        provider="esri"
    ).add_to(m)

    # Add polygons if present
    if "lot_geoms" in st.session_state and st.session_state["lot_geoms"]:
        s = st.session_state["style"]
        style_fn = lambda _:{'fillColor':s['fill'],'color':s['line'],
                             'weight':s['w'],'fillOpacity':s['op']/100}
        for lp,g in st.session_state["lot_geoms"].items():
            gj = folium.GeoJson(mapping(g), style_function=style_fn, name=lp)
            gj.add_child(folium.Popup(lp))
            gj.add_to(m)

    folium.LayerControl(position="topright").add_to(m)
    st_folium(m, height=560, use_container_width=True)

# ─── KML download ────────────────────────────────────
if ("lot_geoms" in st.session_state and st.session_state["lot_geoms"]
    and col_ctrl.button("📥 Download KML", use_container_width=True)):
    s = st.session_state["style"]
    kml=simplekml.Kml(); fld=kml.newfolder(name=s["folder"])
    fillk=kml_colour(s["fill"],s["op"]); linek=kml_colour(s["line"],100)
    for lp,g in st.session_state["lot_geoms"].items():
        p=fld.newpolygon(name=lp, outerboundaryis=mapping(g)["coordinates"][0])
        p.style.polystyle.color=fillk
        p.style.linestyle.color=linek; p.style.linestyle.width=float(s["w"])
    st.download_button("Save KML",
        io.BytesIO(kml.kml().encode()).getvalue(),"parcels.kml",
        "application/vnd.google-earth.kml+xml",use_container_width=True)
