import streamlit as st
st.set_page_config(page_title="Parcel Viewer", layout="wide")

st.markdown("""
    <style>
    #MainMenu, header, footer {visibility: hidden;}
    [data-testid="stAppViewContainer"] .main .block-container {padding: 0;}
    </style>
    """, unsafe_allow_html=True)

import requests, folium, pandas as pd
import shapefile, os, io, zipfile, re
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

def _hex_to_kml_color(hex_color: str, opacity: float) -> str:
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        hex_color = "FFFFFF"
    r = hex_color[0:2]
    g = hex_color[2:4]
    b = hex_color[4:6]
    alpha = int(opacity * 255)
    return f"{alpha:02x}{b}{g}{r}"

def generate_kml(features: list, region: str, fill_hex: str, fill_opacity: float, outline_hex: str, outline_weight: int) -> str:
    fill_kml_color = _hex_to_kml_color(fill_hex, fill_opacity)
    outline_kml_color = _hex_to_kml_color(outline_hex, 1.0)
    kml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        '<Document>'
    ]
    for feat in features:
        props = feat.get("properties", {})
        if region == "QLD":
            lot = props.get("lot", "")
            plan = props.get("plan", "")
            placename = f"Lot {lot} Plan {plan}"
        else:
            lot = props.get("lotnumber", "")
            sec = props.get("sectionnumber", "") or ""
            planlabel = props.get("planlabel", "")
            placename = f"Lot {lot} {('Sec '+sec+' ' if sec else '')}{planlabel}"
        
        # Create popup content with all properties
        description = "<table border='1' style='border-collapse: collapse;'>"
        description += "<tr><th style='padding: 5px; border: 1px solid black;'>Property</th><th style='padding: 5px; border: 1px solid black;'>Value</th></tr>"
        for key, value in props.items():
            if value:  # Only include non-empty values
                description += f"<tr><td style='padding: 5px; border: 1px solid black;'>{key}</td><td style='padding: 5px; border: 1px solid black;'>{value}</td></tr>"
        description += "</table>"

        kml_lines.append(f"<Placemark><name>{placename}</name>")
        kml_lines.append(f"<description><![CDATA[{description}]]></description>")
        kml_lines.append("<Style>")
        kml_lines.append(f"<LineStyle><color>{outline_kml_color}</color><width>{outline_weight}</width></LineStyle>")
        kml_lines.append(f"<PolyStyle><color>{fill_kml_color}</color></PolyStyle>")
        kml_lines.append("</Style>")
        geom = feat.get("geometry", {})
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        polygons = []
        if gtype == "Polygon":
            polygons.append(coords)
        elif gtype == "MultiPolygon":
            polygons.extend(coords)
        else:
            continue
        if len(polygons) > 1:
            kml_lines.append("<MultiGeometry>")
        for poly in polygons:
            if not poly: 
                continue
            outer = poly[0]
            if outer[0] != outer[-1]:
                outer = outer + [outer[0]]
            kml_lines.append("<Polygon><outerBoundaryIs><LinearRing><coordinates>")
            kml_lines.append(" ".join(f"{x},{y},0" for x, y in outer))
            kml_lines.append("</coordinates></LinearRing></outerBoundaryIs>")
            for hole in poly[1:]:
                if hole and hole[0] != hole[-1]:
                    hole = hole + [hole[0]]
                kml_lines.append("<innerBoundaryIs><LinearRing><coordinates>")
                kml_lines.append(" ".join(f"{x},{y},0" for x, y in hole))
                kml_lines.append("</coordinates></LinearRing></innerBoundaryIs>")
            kml_lines.append("</Polygon>")
        if len(polygons) > 1:
            kml_lines.append("</MultiGeometry>")
        kml_lines.append("</Placemark>")
    kml_lines.append("</Document></kml>")
    return "\n".join(kml_lines)

def generate_shapefile(features: list, region: str) -> bytes:
    temp_dir = "temp_shp_export"
    os.makedirs(temp_dir, exist_ok=True)
    base_path = os.path.join(temp_dir, "parcels")
    w = shapefile.Writer(base_path)
    w.field("LOT", "C", size=10)
    w.field("SEC", "C", size=10)
    w.field("PLAN", "C", size=15)
    w.autoBalance = 1
    for feat in features:
        props = feat.get("properties", {})
        if region == "QLD":
            lot_val = props.get("lot", "") or ""
            sec_val = ""
            plan_val = props.get("plan", "") or ""
        else:
            lot_val = props.get("lotnumber", "") or ""
            sec_val = props.get("sectionnumber", "") or ""
            plan_val = props.get("planlabel", "") or ""
        w.record(lot_val, sec_val, plan_val)
        geom = feat.get("geometry", {})
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        parts = []
        if gtype == "Polygon":
            for ring in coords:
                if ring and ring[0] != ring[-1]:
                    ring = ring + [ring[0]]
                parts.append(ring)
        elif gtype == "MultiPolygon":
            for poly in coords:
                for ring in poly:
                    if ring and ring[0] != ring[-1]:
                        ring = ring + [ring[0]]
                    parts.append(ring)
        if parts:
            w.poly(parts)
    w.close()
    prj_text = ('GEOGCS["WGS 84",DATUM["WGS_1984",'
                'SPHEROID["WGS 84",6378137,298.257223563],'
                'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0],'
                'UNIT["degree",0.0174532925199433],AUTHORITY["EPSG","4326"]]')
    with open(base_path + ".prj", "w") as prj:
        prj.write(prj_text)
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        for ext in (".shp", ".shx", ".dbf", ".prj"):
            file_path = base_path + ext
            if os.path.exists(file_path):
                z.write(file_path, arcname="parcels" + ext)
    for ext in (".shp", ".shx", ".dbf", ".prj"):
        file_path = base_path + ext
        if os.path.exists(file_path):
            os.remove(file_path)
    os.rmdir(temp_dir)
    return zip_buffer.getvalue()

def get_bounds(features: list):
    min_lat, max_lat = 90.0, -90.0
    min_lon, max_lon = 180.0, -180.0
    for feat in features:
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates")
        gtype = geom.get("type")
        if not coords:
            continue
        if gtype == "Polygon":
            poly_list = [coords]
        elif gtype == "MultiPolygon":
            poly_list = coords
        else:
            continue
        for poly in poly_list:
            for ring in poly:
                for x, y in ring:
                    if y < min_lat: min_lat = y
                    if y > max_lat: max_lat = y
                    if x < min_lon: min_lon = x
                    if x > max_lon: max_lon = x
    if min_lat > max_lat or min_lon > max_lon:
        return [[-39, 137], [-9, 155]]
    return [[min_lat, min_lon], [max_lat, max_lon]]

col1, col2 = st.columns([3, 1], gap="small")

with col2:
    with st.form("search_form"):
        bulk_query = st.text_area(
            "Parcel search (bulk):",
            "",
            help="Enter Lot/Plan (QLD) or Lot/Section/Plan (NSW) one per line."
        )
        submit = st.form_submit_button("Search")
    if submit:
        inputs = [line.strip() for line in bulk_query.splitlines() if line.strip()]
        all_feats = []
        all_regions = []
        for user_input in inputs:
            if "/" in user_input:
                region = "NSW"
                parts = user_input.split("/")
                if len(parts) == 3:
                    lot_str, sec_str, plan_str = parts[0].strip(), parts[1].strip(), parts[2].strip()
                elif len(parts) == 2:
                    lot_str, sec_str, plan_str = parts[0].strip(), "", parts[1].strip()
                else:
                    lot_str, sec_str, plan_str = "", "", ""
                if sec_str == "" and "//" in user_input:
                    lot_str, plan_str = user_input.split("//")
                    sec_str = ""
                plan_num = "".join(filter(str.isdigit, plan_str))
                if lot_str == "" or plan_num == "":
                    continue
                where_clauses = [f"lotnumber='{lot_str}'"]
                if sec_str:
                    where_clauses.append(f"sectionnumber='{sec_str}'")
                else:
                    where_clauses.append("(sectionnumber IS NULL OR sectionnumber = '')")
                where_clauses.append(f"plannumber={plan_num}")
                where = " AND ".join(where_clauses)
                url = "https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Cadastre/MapServer/9/query"
                params = {"where": where, "outFields": "lotnumber,sectionnumber,planlabel", "outSR": "4326", "f": "geoJSON"}
                try:
                    res = requests.get(url, params=params, timeout=10)
                    data = res.json()
                except Exception as e:
                    data = {}
                feats = data.get("features", []) or []
                for feat in feats:
                    all_feats.append(feat)
                    all_regions.append("NSW")
            else:
                region = "QLD"
                inp = user_input.replace(" ", "").upper()
                match = re.match(r"^(\d+)([A-Z].+)$", inp)
                if not match:
                    continue
                lot_str = match.group(1)
                plan_str = match.group(2)
                url = "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query"
                params = {"where": f"lot='{lot_str}' AND plan='{plan_str}'", "outFields": "lot,plan,lotplan,locality", "outSR": "4326", "f": "geoJSON"}
                try:
                    res = requests.get(url, params=params, timeout=10)
                    data = res.json()
                except Exception as e:
                    data = {}
                feats = data.get("features", []) or []
                for feat in feats:
                    all_feats.append(feat)
                    all_regions.append("QLD")
        st.session_state['features'] = all_feats
        st.session_state['regions'] = all_regions

    if st.session_state.get('features'):
        features = st.session_state['features']
        regions = st.session_state.get('regions', [])
        export_region = "QLD" if "QLD" in regions else ("NSW" if "NSW" in regions else "QLD")
        fill_color = st.color_picker("Fill color", "#FF0000", key="fill_color")
        outline_color = st.color_picker("Outline color", "#000000", key="outline_color")
        fill_opacity = st.slider("Fill opacity", 0.0, 1.0, 0.5, step=0.01, key="fill_opacity")
        outline_weight = st.slider("Outline weight", 1, 10, 2, key="outline_weight")
        data = []
        for i, feat in enumerate(features):
            props = feat.get("properties", {})
            if regions[i] == "QLD":
                data.append({"Region": "QLD", "Lot": props.get("lot"), "Plan": props.get("plan"), "Locality": props.get("locality", "")})
            else:
                data.append({"Region": "NSW", "Lot": props.get("lotnumber"), "Section": props.get("sectionnumber", ""), "Plan": props.get("planlabel", "")})
        df = pd.DataFrame(data)
        gb = GridOptionsBuilder.from_dataframe(df)
        gb.configure_selection(selection_mode="multiple", use_checkbox=True)
        gridOptions = gb.build()
        grid_resp = AgGrid(df, gridOptions=gridOptions, height=250, update_mode=GridUpdateMode.SELECTION_CHANGED, theme="streamlit")
        sel_rows = grid_resp.get("selected_rows", [])
        selected_features = []
        for sel in sel_rows:
            for i, feat in enumerate(features):
                props = feat.get("properties", {})
                if sel["Region"] == "QLD":
                    if props.get("lot") == sel["Lot"] and props.get("plan") == sel["Plan"]:
                        selected_features.append(feat)
                        break
                else:
                    if (props.get("lotnumber") == sel["Lot"] and
                        (props.get("sectionnumber", "") == sel.get("Section", "")) and
                        props.get("planlabel") == sel["Plan"]):
                        selected_features.append(feat)
                        break
        st.download_button("Download KML", data=generate_kml(selected_features or features, export_region, fill_color, fill_opacity, outline_color, outline_weight), file_name="parcels.kml")
        st.download_button("Download SHP", data=generate_shapefile(selected_features or features, export_region), file_name="parcels.zip")

with col1:
    base_map = folium.Map(location=[-23.5, 143.0], zoom_start=5, tiles=None)
    folium.TileLayer('OpenStreetMap', name='OpenStreetMap', control=True).add_to(base_map)
    folium.TileLayer('CartoDB positron', name='CartoDB Positron', control=True).add_to(base_map)
    folium.TileLayer('CartoDB dark_matter', name='CartoDB Dark', control=True).add_to(base_map)
    folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google', name='Google Satellite', control=True).add_to(base_map)
    if st.session_state.get('features') and st.session_state['features']:
        features = st.session_state['features']
        fill_color = st.session_state.get('fill_color', "#FF0000")
        outline_color = st.session_state.get('outline_color', "#000000")
        opacity = st.session_state.get('fill_opacity', 0.5)
        weight = st.session_state.get('outline_weight', 2)
        folium.GeoJson(
            data={"type": "FeatureCollection", "features": features},
            name="Parcels",
            style_function=lambda feat: {"fillColor": fill_color, "color": outline_color, "weight": weight, "fillOpacity": opacity}
        ).add_to(base_map)
        bounds = get_bounds(features)
        base_map.fit_bounds(bounds)
    else:
        base_map.fit_bounds([[-39, 137], [-9, 155]])
    folium.LayerControl(collapsed=True).add_to(base_map)
    map_html = base_map._repr_html_()
    st.components.v1.html(map_html, height=600, width=None, scrolling=False)
