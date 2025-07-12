#!/usr/bin/env python3
# LAWD Parcel Toolkit – simplified export: SHP + KML only

import io, re, yaml, pathlib, requests, tempfile, zipfile
import streamlit as st
from streamlit_folium import st_folium
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
import pandas as pd, geopandas as gpd, folium, simplekml
from shapely.geometry import shape, mapping
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod

CFG = pathlib.Path("layers.yaml")
cfg = yaml.safe_load(CFG.read_text()) if CFG.exists() else {}
for k in ("basemaps", "overlays"): cfg.setdefault(k, [])

st.set_page_config("Lot/Plan → KML", "📍", layout="wide")
geod = Geod(ellps="WGS84")

if cfg["basemaps"]:
    st.session_state.setdefault("basemap", cfg["basemaps"][0]["name"])
st.session_state.setdefault("ov_state", {o["name"]: False for o in cfg["overlays"]})

QLD = "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query"
NSW = "https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Cadastre/MapServer/9/query"

def fetch_parcels(lps):
    out, miss = {}, []
    for lp in lps:
        url, fld = (QLD, "lotplan") if re.match(r"^\\d+[A-Z]{1,3}\\d+$", lp, re.I) else (NSW, "lotidstring")
        try:
            js = requests.get(url, params={"where": f"{fld}='{lp}'", "outFields": "*", "returnGeometry": "true", "f": "geojson"}, timeout=12).json()
            feats = js.get("features", [])
            if not feats: miss.append(lp); continue
            wkid = feats[0]["geometry"].get("spatialReference", {}).get("wkid", 4326)
            tfm = Transformer.from_crs(wkid, 4326, always_xy=True).transform if wkid != 4326 else None
            geoms, props = [], {}
            for ft in feats:
                g = shape(ft["geometry"]); geoms.append(transform(tfm, g) if tfm else g)
                props = ft["properties"]
            out[lp] = {"geom": unary_union(geoms), "props": props}
        except Exception: miss.append(lp)
    return out, miss

def kml_colour(hexrgb, p):
    r, g, b = hexrgb[1:3], hexrgb[3:5], hexrgb[5:7]
    a = int(round(255 * p / 100))
    return f\"{a:02x}{b}{g}{r}\"

# ─── Sidebar ───
with st.sidebar:
    st.header("Query")
    ids_txt = st.text_area("Lot/Plan IDs", height=120, placeholder="6RP702264\\n5//DP123456")
    fx = st.color_picker("Fill", st.session_state.get("style", {}).get("fill", "#ff6600"))
    lx = st.color_picker("Line", st.session_state.get("style", {}).get("line", "#2e2e2e"))
    fo = st.slider("Fill opacity %", 0, 100, st.session_state.get("style", {}).get("op", 70))
    lw = st.slider("Line width px", 0.5, 6.0, st.session_state.get("style", {}).get("w", 1.2), 0.1)
    if st.button("🔍 Search") and ids_txt.strip():
        ids = [s.strip() for s in re.split(r"[,\\n;]", ids_txt) if s.strip()]
        with st.spinner("Fetching…"):
            recs, miss = fetch_parcels(ids)
        if miss: st.warning("Not found: " + ", ".join(miss))
        rows = []
        for lp, r in recs.items():
            props = r["props"]
            lt = props.get("lottype") or props.get("PURPOSE") or "n/a"
            area = abs(geod.geometry_area_perimeter(r["geom"])[0]) / 1e4
            rows.append({"Lot/Plan": lp, "Lot Type": lt, "Area (ha)": round(area, 2)})
        st.session_state["parcels"] = recs
        st.session_state["table"] = pd.DataFrame(rows)
        st.session_state["style"] = dict(fill=fx, line=lx, op=fo, w=lw)

    st.header("Layers")
    if cfg["basemaps"]:
        names = [b["name"] for b in cfg["basemaps"]]
        st.session_state["basemap"] = st.radio("Basemap", names, index=names.index(st.session_state["basemap"]))
    for o in cfg["overlays"]:
        st.session_state["ov_state"][o["name"]] = st.checkbox(o["name"], value=st.session_state["ov_state"][o["name"]])

# ─── Map ───
m = folium.Map(location=[-25, 145], zoom_start=5, control_scale=True, tiles=None, width="100%", height="70vh")

if cfg["basemaps"]:
    b = next(bb for bb in cfg["basemaps"] if bb["name"] == st.session_state["basemap"])
    folium.TileLayer(b["url"], name=b["name"], attr=b["attr"], overlay=False).add_to(m)

for o in cfg["overlays"]:
    if st.session_state["ov_state"][o["name"]]:
        folium.TileLayer(o["url"], name=o["name"], attr=o["attr"]).add_to(m)

bounds = []
if "parcels" in st.session_state:
    sty = st.session_state["style"]
    f = lambda _: {'fillColor': sty["fill"], 'color': sty["line"], 'weight': sty["w"], 'fillOpacity': sty["op"] / 100}
    pg = folium.FeatureGroup(name="Parcels", show=True).add_to(m)
    for lp, r in st.session_state["parcels"].items():
        g, p = r["geom"], r["props"]
        lt = p.get("lottype") or p.get("PURPOSE") or "n/a"
        area = abs(geod.geometry_area_perimeter(g)[0]) / 1e4
        html = f"<b>Lot/Plan:</b> {lp}<br><b>Lot Type:</b> {lt}<br><b>Area:</b> {area:,.2f} ha"
        folium.GeoJson(mapping(g), style_function=f, tooltip=lp, popup=html).add_to(pg)
        bounds.append([[g.bounds[1], g.bounds[0]], [g.bounds[3], g.bounds[2]]])
if bounds:
    xs, ys, xe, ye = zip(*[(b[0][1], b[0][0], b[1][1], b[1][0]) for b in bounds])
    m.fit_bounds([[min(ys), min(xs)], [max(ye), max(xe)]])

st_folium(m, height=550, use_container_width=True, key="fol")

# ─── Table & Export ───
if "table" in st.session_state and not st.session_state["table"].empty:
    st.subheader("Results")
    gdf = gpd.GeoDataFrame(st.session_state["table"], geometry=[r["geom"] for r in st.session_state["parcels"].values()], crs=4326)
    AgGrid(gdf.drop(columns="geometry"), update_mode=GridUpdateMode.NO_UPDATE, height=260)

    col1, col2 = st.columns(2)

    # Export SHP
    tmp = tempfile.mkdtemp()
    gdf.to_file(tmp + "/all.shp")
    zipf = pathlib.Path(tmp, "all.zip")
    with zipfile.ZipFile(zipf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in pathlib.Path(tmp).glob("all.*"): z.write(f, f.name)
    col1.download_button("Export ALL SHP", open(zipf, "rb").read(), "parcels.zip", "application/zip")

    # Export KML
    kml = simplekml.Kml()
    for lp, r in st.session_state["parcels"].items():
        g = r["geom"]
        k = kml.newpolygon(name=lp, outerboundaryis=list(g.exterior.coords))
        k.style.polystyle.color = kml_colour(sty["fill"], sty["op"])
        k.style.linestyle.color = kml_colour(sty["line"], 100)
        k.style.linestyle.width = sty["w"]
    bio = io.BytesIO(); kml.save(bio); bio.seek(0)
    col2.download_button("Export ALL KML", bio.read(), "parcels.kml", "application/vnd.google-earth.kml+xml")
