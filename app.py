# app.py  –  LAWD Parcel Toolkit  (basemap always bottom, WMS by layer-name)

import io, re, json, yaml, pathlib, requests, streamlit as st
from collections import defaultdict
from streamlit_option_menu import option_menu
from streamlit_folium import st_folium
import folium, simplekml
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod

# ---------- Load static YAML -------------------------------------------------
CFG = pathlib.Path("layers.yaml")
cfg = yaml.safe_load(CFG.read_text()) if CFG.exists() else {}
for k in ("basemaps", "overlays", "databases"):
    cfg.setdefault(k, [])

# ---------- Streamlit layout -------------------------------------------------
st.set_page_config("Lot/Plan → KML", "📍", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown(
    "<div style='background:#ff6600;color:#fff;font-size:20px;font-weight:600;"
    "padding:6px 20px;border-radius:8px;margin-bottom:6px'>"
    "LAWD – Parcel Toolkit</div>", unsafe_allow_html=True)

with st.sidebar:
    tab = option_menu(
        None, ["Query", "Layers", "Downloads"],
        icons=["search", "layers", "download"],
        default_index=0,
        styles={
            "container": {"padding": "0", "background": "#262730"},
            "nav-link-selected": {"background": "#ff6600"},
        },
    )

# ---------- Session defaults -------------------------------------------------
if cfg["basemaps"]:
    st.session_state.setdefault("basemap", cfg["basemaps"][0]["name"])
st.session_state.setdefault("ov_state", {o["name"]: False for o in cfg["overlays"]})
st.session_state.setdefault("db_state", {d["name"]: False for d in cfg["databases"]})

# ---------- Cadastre fetchers ------------------------------------------------
QLD = (
    "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
    "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query"
)
NSW = (
    "https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
    "NSW_Cadastre/MapServer/9/query"
)
geod = Geod(ellps="WGS84")


def fetch_parcels(ids):
    grp, miss = defaultdict(list), []
    for lp in ids:
        url, fld = (
            (QLD, "lotplan")
            if re.match(r"^\d+[A-Z]{1,3}\d+$", lp, re.I)
            else (NSW, "lotidstring")
        )
        try:
            js = requests.get(
                url,
                params={
                    "where": f"{fld}='{lp}'",
                    "returnGeometry": "true",
                    "f": "geojson",
                },
                timeout=12,
            ).json()
            feats = js.get("features", [])
            if not feats:
                miss.append(lp)
                continue
            wkid = feats[0]["geometry"].get("spatialReference", {}).get("wkid", 4326)
            tfm = (
                Transformer.from_crs(wkid, 4326, always_xy=True).transform
                if wkid != 4326
                else None
            )
            for ft in feats:
                geom = shape(ft["geometry"])
                grp[lp].append(transform(tfm, geom) if tfm else geom)
        except Exception:
            miss.append(lp)
    return {lp: unary_union(gs) for lp, gs in grp.items()}, miss


def kml_colour(hex_rgb, pct):
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    a = int(round(255 * pct / 100))
    return f"{a:02x}{b}{g}{r}"


# ---------- TAB : Query ------------------------------------------------------
if tab == "Query":
    ids_txt = st.sidebar.text_area(
        "Lot/Plan IDs", height=140, placeholder="6RP702264\n5//DP123456"
    )
    fx = st.sidebar.color_picker("Fill", "#ff6600")
    fo = st.sidebar.slider("Fill opacity %", 0, 100, 70)
    lx = st.sidebar.color_picker("Outline", "#2e2e2e")
    lw = st.sidebar.slider("Outline width px", 0.5, 6.0, 1.2, 0.1)
    folder = st.sidebar.text_input("KML folder", "Parcels")

    if st.sidebar.button("🔍 Search") and ids_txt.strip():
        ids = [s.strip() for s in ids_txt.splitlines() if s.strip()]
        with st.spinner("Fetching parcels…"):
            geoms, miss = fetch_parcels(ids)
        if miss:
            st.sidebar.warning("Not found: " + ", ".join(miss))
        st.session_state["parcels"] = geoms
        st.session_state["style"] = dict(
            fill=fx, op=fo, line=lx, w=lw, folder=folder
        )
        st.sidebar.success(f"{len(geoms)} parcel(s) loaded.")


# ---------- TAB : Layers -----------------------------------------------------
if tab == "Layers":
    # Basemap radio
    if cfg["basemaps"]:
        st.sidebar.subheader("Basemap")
        names = [b["name"] for b in cfg["basemaps"]]
        st.session_state["basemap"] = st.sidebar.radio(
            "", names, index=names.index(st.session_state["basemap"])
        )

    # Overlay checkboxes
    st.sidebar.subheader("Static overlays")
    for o in cfg["overlays"]:
        st.session_state["ov_state"][o["name"]] = st.sidebar.checkbox(
            o["name"], value=st.session_state["ov_state"][o["name"]]
        )

    # Database checkboxes
    st.sidebar.subheader("Databases / GIS layers")
    for d in cfg["databases"]:
        st.session_state["db_state"][d["name"]] = st.sidebar.checkbox(
            d["name"], value=st.session_state["db_state"][d["name"]]
        )

    # Add GIS service (optional)
    with st.sidebar.expander("➕ Add GIS service"):
        n = st.text_input("Display name")
        u = st.text_input("URL …")
        t = st.selectbox("Type", ["wms", "tile", "geojson"])
        l = st.text_input("Layer name(s) (WMS only)")
        a = st.text_input("Attribution", "© Source")
        if st.button("Add to YAML") and n and u:
            cfg["databases"].append(
                {"name": n, "type": t, "url": u, "layers": l, "attr": a}
            )
            CFG.write_text(yaml.safe_dump(cfg, sort_keys=False))
            st.sidebar.success("Added. Reload the app to see it.")
            st.stop()

# ---------- Build Folium map --------------------------------------------------
m = folium.Map(
    location=[-25, 145],
    zoom_start=5,
    control_scale=True,
    width="100%",
    height="100vh",
)

# Basemap (always bottom layer)
if cfg["basemaps"]:
    b = next(bb for bb in cfg["basemaps"] if bb["name"] == st.session_state["basemap"])
    folium.TileLayer(
        b["url"],
        name=b["name"],
        attr=b["attr"],
        overlay=False,  # <-- keeps it below all overlays
        control=True,
        show=True,
    ).add_to(m)

# Overlays
for o in cfg["overlays"]:
    if not st.session_state["ov_state"][o["name"]]:
        continue
    try:
        if o["type"] == "wms":
            folium.raster_layers.WmsTileLayer(
                o["url"],
                layers=str(o["layers"]),
                transparent=True,
                fmt=o.get("fmt", "image/png"),
                name=o["name"],
                attr=o["attr"],
            ).add_to(m)
        else:
            folium.TileLayer(o["url"], name=o["name"], attr=o["attr"]).add_to(m)
    except Exception as e:
        st.warning(f"{o['name']} failed: {e}")

# Databases
bounds = []
for d in cfg["databases"]:
    if not st.session_state["db_state"][d["name"]]:
        continue
    try:
        if d["type"] == "wms":
            folium.raster_layers.WmsTileLayer(
                d["url"],
                layers=str(d["layers"]),
                transparent=True,
                fmt=d.get("fmt", "image/png"),
                name=d["name"],
                attr=d["attr"],
            ).add_to(m)
        elif d["type"] == "tile":
            folium.TileLayer(d["url"], name=d["name"], attr=d["attr"]).add_to(m)
        elif d["type"] == "geojson":
            gj = requests.get(d["url"], timeout=15).json()
            g = folium.GeoJson(gj, name=d["name"]).add_to(m)
            bounds.append(g.get_bounds())
    except Exception as e:
        st.warning(f"{d['name']} failed: {e}")

# Parcels
if "parcels" in st.session_state:
    s = st.session_state["style"]
    sty = lambda _: {
        "fillColor": s["fill"],
        "color": s["line"],
        "weight": s["w"],
        "fillOpacity": s["op"] / 100,
    }
    pg = folium.FeatureGroup(name="Parcels", show=True).add_to(m)
    for lp, g in st.session_state["parcels"].items():
        folium.GeoJson(mapping(g), style_function=sty, name=lp).add_child(
            folium.Popup(lp)
        ).add_to(pg)
        bounds.append([[g.bounds[1], g.bounds[0]], [g.bounds[3], g.bounds[2]]])

# Zoom to visible data
if bounds:
    xs, ys, xe, ye = zip(*[(b[0][1], b[0][0], b[1][1], b[1][0]) for b in bounds])
    m.fit_bounds([[min(ys), min(xs)], [max(ye), max(xe)]])

st_folium(m, height=700, use_container_width=True, key="main_map")

# ---------- TAB : Downloads ---------------------------------------------------
if tab == "Downloads":
    st.sidebar.subheader("Export")
    if "parcels" in st.session_state and st.session_state["parcels"]:
        if st.sidebar.button("💾 Generate KML"):
            s = st.session_state["style"]
            gs = st.session_state["parcels"]
            kml = simplekml.Kml()
            fld = kml.newfolder(name=s["folder"])
            fk, lk = kml_colour(s["fill"], s["op"]), kml_colour(s["line"], 100)
            for lp, g in gs.items():
                polys = [g] if isinstance(g, Polygon) else list(g.geoms)
                for i, p in enumerate(polys, 1):
                    area = abs(Geod(ellps="WGS84").geometry_area_perimeter(p)[0]) / 1e4
                    nm = f"{lp} ({i})" if len(polys) > 1 else lp
                    desc = f"Lot/Plan: {lp}<br>Area: {area:,.2f} ha"
                    pl = fld.newpolygon(
                        name=nm, description=desc, outerboundaryis=p.exterior.coords
                    )
                    for r in p.interiors:
                        pl.innerboundaryis.append(r.coords)
                    pl.style.polystyle.color = fk
                    pl.style.linestyle.color = lk
                    pl.style.linestyle.width = float(s["w"])
            st.sidebar.download_button(
                "Save KML",
                io.BytesIO(kml.kml().encode()).getvalue(),
                "parcels.kml",
                "application/vnd.google-earth.kml+xml",
            )
    else:
        st.sidebar.info("Load parcels in Query first.")
