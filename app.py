import io, re, requests, streamlit as st
from collections import defaultdict
from streamlit_option_menu import option_menu
from streamlit_folium import st_folium
import folium, simplekml
from folium.plugins import GroupedLayerControl
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod

# ─── Page & base theme ──────────────────────────────────────────────
st.set_page_config(page_title="Lot/Plan → KML",
                   page_icon="📍",
                   layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""
<div style='background:#ff6600;color:white;font-size:20px;font-weight:600;
            padding:6px 20px;border-radius:8px;margin-bottom:6px;'>
  LAWD – Parcel Toolkit
</div>""", unsafe_allow_html=True)

st.markdown("""
<style>
div[data-testid='stSidebar']{width:320px;}
#main_map iframe{border-radius:12px;box-shadow:0 4px 14px rgba(0,0,0,0.25);}
</style>""", unsafe_allow_html=True)

# ─── Data sources ───────────────────────────────────────────────────
QLD_URL = ("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
           "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW_URL = ("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
           "NSW_Cadastre/MapServer/9/query")
FLOOD_WMS = ("https://qrospatial.information.qld.gov.au/services/opendata/"
             "qra/FloodHazards/MapServer/WMSServer")

geod = Geod(ellps="WGS84")

# ─── Helper: fetch & merge parcels ──────────────────────────────────
def fetch_geoms(lotplans):
    grouped, missing = defaultdict(list), []
    is_qld = lambda lp: bool(re.match(r"^\d+[A-Z]{1,3}\d+$", lp, re.I))

    for lp in lotplans:
        url, fld = (QLD_URL, "lotplan") if is_qld(lp) else (NSW_URL, "lotidstring")
        try:
            js = requests.get(url, params={
                "where": f"{fld}='{lp}'",
                "returnGeometry": "true", "f": "geojson"},
                timeout=12).json()

            feats = js.get("features", [])
            if not feats:
                missing.append(lp); continue

            wkid = js.get("spatialReference", {}).get("wkid") \
                   or feats[0]["geometry"].get("spatialReference", {}).get("wkid", 4326)
            tfm = (Transformer.from_crs(wkid, 4326, always_xy=True).transform
                   if wkid!=4326 else None)

            for feat in feats:
                g = shape(feat["geometry"])
                grouped[lp].append(transform(tfm, g) if tfm else g)

        except Exception:
            missing.append(lp)

    merged = {lp: unary_union(gs) for lp, gs in grouped.items()}
    return merged, missing

def kml_colour(hex_rgb, pct):
    r,g,b = hex_rgb[1:3],hex_rgb[3:5],hex_rgb[5:7]
    a = int(round(255*pct/100))
    return f"{a:02x}{b}{g}{r}"

# ─── Sidebar icon menu ──────────────────────────────────────────────
with st.sidebar:
    choice = option_menu(
        None,
        ["Query","Layers","Downloads"],
        icons=["search","layers","download"],
        default_index=0,
        styles={
            "container":{"padding":"0!important","background-color":"#262730"},
            "icon":{"color":"white","font-size":"20px"},
            "nav-link":{"font-size":"14px","margin":"0"},
            "nav-link-selected":{"background-color":"#ff6600"},
        },
    )

# ─── Tab: Query ─────────────────────────────────────────────────────
if choice == "Query":
    st.sidebar.subheader("Lot/Plan search")
    lot_text = st.sidebar.text_area("IDs", height=140,
                                    placeholder="6RP702264\n5//DP123456")
    fill_hex = st.sidebar.color_picker("Fill colour","#ff6600")
    fill_op  = st.sidebar.number_input("Fill opacity %",0,100,70)
    line_hex = st.sidebar.color_picker("Outline colour","#2e2e2e")
    line_w   = st.sidebar.number_input("Outline width px",0.5,6.0,1.2,step=0.1)
    folder   = st.sidebar.text_input("Folder name in KML","Parcels")
    run_btn  = st.sidebar.button("🔍 Search",use_container_width=True)

    if run_btn and lot_text.strip():
        ids = [i.strip() for i in lot_text.splitlines() if i.strip()]
        with st.spinner("Fetching parcels…"):
            geoms, missing = fetch_geoms(ids)

        if missing: st.sidebar.warning("Not found: "+", ".join(missing))
        st.sidebar.info(f"Loaded {len(geoms)} parcel{'s' if len(geoms)!=1 else ''}.")
        st.session_state["geoms"] = geoms
        st.session_state["style"] = dict(fill=fill_hex, op=fill_op,
                                         line=line_hex, w=line_w, folder=folder)

# ─── Tab: Layers ────────────────────────────────────────────────────
if choice == "Layers":
    st.sidebar.subheader("Toggle overlays")
    show_flood = st.sidebar.checkbox("QLD Flood Hazard",
                                     value=st.session_state.get("show_flood", False))
    st.session_state["show_flood"] = show_flood

# ─── Build map (runs every draw) ────────────────────────────────────
m = folium.Map(location=[-25,145], zoom_start=5,
               control_scale=True, width="100%", height="100vh")

# Basemaps
folium.TileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
                 name="OpenStreetMap", attr="© OpenStreetMap").add_to(m)
folium.TileLayer("https://services.arcgisonline.com/arcgis/rest/services/"
                 "World_Imagery/MapServer/tile/{z}/{y}/{x}",
                 name="Esri Imagery", attr="© Esri").add_to(m)
folium.TileLayer("https://services.arcgisonline.com/arcgis/rest/services/"
                 "World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
                 name="Esri Topo", attr="© Esri").add_to(m)

# Optional flood layer
overlay_names = ["Parcels"]                     # will feed GroupedLayerControl
if st.session_state.get("show_flood"):
    folium.raster_layers.WmsTileLayer(
        url=FLOOD_WMS, layers="0",
        name="Flood Hazard", transparent=True,
        fmt="image/png", attr="© QRA").add_to(m)
    overlay_names.append("Flood Hazard")

# Parcels
parcel_group = folium.FeatureGroup(name="Parcels", show=True).add_to(m)
bounds=[]
if "geoms" in st.session_state and st.session_state["geoms"]:
    s=st.session_state["style"]
    sty=lambda _:{'fillColor':s['fill'],'color':s['line'],
                  'weight':s['w'],'fillOpacity':s['op']/100}
    for lp,g in st.session_state["geoms"].items():
        folium.GeoJson(mapping(g),name=lp,
                       style_function=sty).add_child(folium.Popup(lp)).add_to(parcel_group)
        bounds.append(g.bounds)
    if bounds:
        minx=min(b[0] for b in bounds); miny=min(b[1] for b in bounds)
        maxx=max(b[2] for b in bounds); maxy=max(b[3] for b in bounds)
        m.fit_bounds([[miny,minx],[maxy,maxx]])

folium.LayerControl(position="topright", collapsed=False).add_to(m)

st_folium(m, height=700, use_container_width=True, key="main_map")

# ─── Tab: Downloads ────────────────────────────────────────────────
if choice == "Downloads":
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
                    p.style.polystyle.color=fk
                    p.style.linestyle.color=lk
                    p.style.linestyle.width=float(s["w"])

            st.sidebar.download_button("Save KML",
                io.BytesIO(kml.kml().encode()).getvalue(),
                "parcels.kml","application/vnd.google-earth.kml+xml",
                use_container_width=True)
    else:
        st.sidebar.info("Load parcels in the Query tab first.")
