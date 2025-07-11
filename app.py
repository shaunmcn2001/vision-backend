import io, re, yaml, pathlib, requests, streamlit as st
from collections import defaultdict
from streamlit_option_menu import option_menu
from streamlit_folium import st_folium
import folium, simplekml
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod

# ─── paths & helpers for registry ──────────────────────────────
REG_PATH = pathlib.Path("layers.yaml")

@st.cache_data
def load_registry():
    return yaml.safe_load(REG_PATH.read_text())

def save_registry(cfg):
    REG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))
    st.cache_data.clear()

layers_cfg = load_registry()

# ─── Streamlit page theme & banner ─────────────────────────────
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

# ─── Constants (Cadastre endpoints) ───────────────────────────
QLD_URL = ("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
           "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW_URL = ("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
           "NSW_Cadastre/MapServer/9/query")

geod = Geod(ellps="WGS84")

# ─── Parcel fetch/merge helper ─────────────────────────────────
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
            if not feats: missing.append(lp); continue

            wkid = js.get("spatialReference", {}).get("wkid") \
                   or feats[0]["geometry"].get("spatialReference", {}).get("wkid", 4326)
            tfm = (Transformer.from_crs(wkid, 4326, always_xy=True).transform
                   if wkid!=4326 else None)

            for feat in feats:
                g = shape(feat["geometry"])
                grouped[lp].append(transform(tfm, g) if tfm else g)

        except Exception:
            missing.append(lp)

    return {lp: unary_union(gs) for lp, gs in grouped.items()}, missing

def kml_colour(hex_rgb, pct):
    r,g,b = hex_rgb[1:3],hex_rgb[3:5],hex_rgb[5:7]
    a = int(round(255*pct/100))
    return f"{a:02x}{b}{g}{r}"

# ─── sidebar icon menu ────────────────────────────────────────
with st.sidebar:
    tab = option_menu(
        None, ["Query","Layers","Downloads"],
        icons=["search","layers","download"],
        default_index=0,
        styles={"container":{"padding":"0","background-color":"#262730"},
                "nav-link":{"font-size":"14px","margin":"0"},
                "nav-link-selected":{"background-color":"#ff6600"}})

# ─── Session defaults ─────────────────────────────────────────
st.session_state.setdefault("basemap", layers_cfg["basemaps"][0]["name"])
st.session_state.setdefault("overlay_state",
                            {ov["name"]: False for ov in layers_cfg["overlays"]})

# ─── Tab: Query ───────────────────────────────────────────────
if tab == "Query":
    st.sidebar.subheader("Lot/Plan search")
    lot_text = st.sidebar.text_area("IDs", height=140,
                                    placeholder="6RP702264\n5//DP123456")
    fill_hex = st.sidebar.color_picker("Fill colour","#ff6600")
    fill_op  = st.sidebar.number_input("Fill opacity %",0,100,70)
    line_hex = st.sidebar.color_picker("Outline colour","#2e2e2e")
    line_w   = st.sidebar.number_input("Outline width px",0.5,6.0,1.2,step=0.1)
    folder   = st.sidebar.text_input("Folder name in KML","Parcels")
    if st.sidebar.button("🔍 Search", use_container_width=True) and lot_text.strip():
        ids = [i.strip() for i in lot_text.splitlines() if i.strip()]
        with st.spinner("Fetching parcels…"):
            geoms, missing = fetch_geoms(ids)
        if missing: st.sidebar.warning("Not found: "+", ".join(missing))
        st.session_state["geoms"] = geoms
        st.session_state["style"] = dict(fill=fill_hex, op=fill_op,
                                         line=line_hex, w=line_w, folder=folder)
        st.sidebar.info(f"Loaded {len(geoms)} parcel{'s' if len(geoms)!=1 else ''}.")

# ─── Tab: Layers ──────────────────────────────────────────────
if tab == "Layers":
    st.sidebar.subheader("Basemap")
    b_names = [b["name"] for b in layers_cfg["basemaps"]]
    st.session_state["basemap"] = st.sidebar.radio(
        label="", options=b_names,
        index=b_names.index(st.session_state["basemap"]))

    st.sidebar.subheader("Overlays")
    for ov in layers_cfg["overlays"]:
        current = st.session_state["overlay_state"][ov["name"]]
        st.session_state["overlay_state"][ov["name"]] = st.sidebar.checkbox(
            ov["name"], value=current)

    # --- Add-Layer form (optional) ---
    with st.sidebar.expander("➕  Add new overlay"):
        new_name  = st.text_input("Name")
        new_type  = st.selectbox("Type", ["wms","tile","geojson"])
        new_url   = st.text_input("URL")
        extra     = st.text_input("Layers (WMS) or Attribution")
        if st.button("Add layer") and new_name and new_url:
            entry = {"name": new_name, "type": new_type, "url": new_url}
            if new_type == "wms":
                entry["layers"] = extra or "0"
                entry["fmt"] = "image/png"
            entry["attr"] = extra if new_type != "wms" else "© Custom"
            layers_cfg["overlays"].append(entry)
            save_registry(layers_cfg)
            st.success("Layer saved – reload to use it.")

# ─── Build map every run ─────────────────────────────────────
m = folium.Map(location=[-25,145], zoom_start=5,
               control_scale=True, width="100%", height="100vh")

# selected basemap
b_cfg = next(b for b in layers_cfg["basemaps"]
             if b["name"] == st.session_state["basemap"])
folium.TileLayer(b_cfg["url"], name=b_cfg["name"],
                 attr=b_cfg["attr"]).add_to(m)

# overlays
overlay_bounds = []
for ov in layers_cfg["overlays"]:
    if not st.session_state["overlay_state"].get(ov["name"]):
        continue
    if ov["type"] == "wms":
        try:
            folium.raster_layers.WmsTileLayer(
                url=ov["url"], layers=str(ov["layers"]),
                fmt=ov.get("fmt","image/png"), transparent=True,
                name=ov["name"], attr=ov["attr"]).add_to(m)
        except Exception as e:
            st.warning(f"Could not load WMS: {ov['name']} – {e}")
    elif ov["type"] == "tile":
        folium.TileLayer(ov["url"], name=ov["name"],
                         attr=ov["attr"]).add_to(m)
    elif ov["type"] == "geojson":
        try:
            gj = folium.GeoJson(ov["url"], name=ov["name"])
            gj.add_to(m)
            overlay_bounds.append(gj.get_bounds())
        except Exception as e:
            st.warning(f"GeoJSON failed: {ov['name']} – {e}")

# parcels
parcel_group = folium.FeatureGroup(name="Parcels", show=True).add_to(m)
parcel_bounds = []
if "geoms" in st.session_state:
    style = st.session_state.get("style", {})
    sty = lambda _:{'fillColor': style.get('fill','#ff6600'),
                    'color': style.get('line','#2e2e2e'),
                    'weight': style.get('w',1.2),
                    'fillOpacity': style.get('op',70)/100}
    for lp, g in st.session_state["geoms"].items():
        folium.GeoJson(mapping(g), name=lp,
                       style_function=sty).add_child(folium.Popup(lp)).add_to(parcel_group)
        parcel_bounds.append(g.bounds)

# auto-zoom: parcels first, else overlay
if parcel_bounds:
    minx=min(b[0] for b in parcel_bounds); miny=min(b[1] for b in parcel_bounds)
    maxx=max(b[2] for b in parcel_bounds); maxy=max(b[3] for b in parcel_bounds)
    m.fit_bounds([[miny,minx],[maxy,maxx]])
elif overlay_bounds:
    m.fit_bounds(overlay_bounds[0])

st_folium(m, height=700, use_container_width=True, key="main_map")

# ─── Tab: Downloads ─────────────────────────────────────────
if tab == "Downloads":
    st.sidebar.subheader("Export")
    if "geoms" in st.session_state and st.session_state["geoms"]:
        if st.sidebar.button("💾 Generate KML", use_container_width=True):
            s = st.session_state["style"]; geoms = st.session_state["geoms"]
            kml = simplekml.Kml(); root = kml.newfolder(name=s["folder"])
            fk, lk = kml_colour(s["fill"], s["op"]), kml_colour(s["line"], 100)

            for lp, geom in geoms.items():
                polys = [geom] if isinstance(geom, Polygon) else list(geom.geoms)
                for idx, poly in enumerate(polys, 1):
                    area_ha = abs(geod.geometry_area_perimeter(poly)[0]) / 1e4
                    name = f"{lp} ({idx})" if len(polys) > 1 else lp
                    desc = f"Lot/Plan: {lp}<br>Area: {area_ha:,.2f} ha"
                    p = root.newpolygon(name=name, description=desc,
                                        outerboundaryis=list(poly.exterior.coords))
                    for ring in poly.interiors:
                        p.innerboundaryis.append(list(ring.coords))
                    p.style.polystyle.color = fk
                    p.style.linestyle.color = lk
                    p.style.linestyle.width = float(s["w"])

            st.sidebar.download_button("Save KML",
                io.BytesIO(kml.kml().encode()).getvalue(),
                "parcels.kml","application/vnd.google-earth.kml+xml",
                use_container_width=True)
    else:
        st.sidebar.info("Load parcels in the Query tab first.")
