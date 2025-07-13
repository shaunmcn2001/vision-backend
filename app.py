import streamlit as st
st.set_page_config(page_title="Parcel Viewer", layout="wide")
# Hide default Streamlit elements and reduce padding
st.markdown("""
    <style>
    #MainMenu, header, footer {visibility: hidden;}
    [data-testid="stAppViewContainer"] .main .block-container {padding: 0;}
    </style>
    """, unsafe_allow_html=True)

# Ensure required libraries are installed and import them
try:
    import requests
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "requests"])
    import requests
try:
    import folium
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "folium"])
    import folium
try:
    import pandas as pd
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "pandas"])
    import pandas as pd
try:
    import shapefile
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "pyshp"])
    import shapefile
try:
    from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "streamlit-aggrid"])
    from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

# Helper function to convert hex color and opacity to KML color format (AABBGGRR)
def _hex_to_kml_color(hex_color: str, opacity: float) -> str:
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        hex_color = "FFFFFF"  # default to white if invalid
    r = hex_color[0:2]
    g = hex_color[2:4]
    b = hex_color[4:6]
    alpha = int(opacity * 255)
    return f"{alpha:02x}{b}{g}{r}"

# Helper function to generate KML content from features
def generate_kml(features: list, region: str, fill_hex: str, fill_opacity: float, outline_hex: str, outline_weight: int) -> str:
    fill_kml_color = _hex_to_kml_color(fill_hex, fill_opacity)
    outline_kml_color = _hex_to_kml_color(outline_hex, 1.0)
    # Begin KML document
    kml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        '<Document>'
    ]
    for feat in features:
        props = feat.get("properties", {})
        # Determine name for placemark
        if region == "QLD":
            lot = props.get("lot", "")
            plan = props.get("plan", "")
            placename = f"Lot {lot} Plan {plan}"
        else:  # NSW
            lot = props.get("lotnumber", "")
            sec = props.get("sectionnumber", "") or ""
            planlabel = props.get("planlabel", "")
            placename = f"Lot {lot} {('Sec '+sec+' ' if sec else '')}{planlabel}"
        kml_lines.append(f"<Placemark><name>{placename}</name>")
        # KML style
        kml_lines.append("<Style>")
        kml_lines.append(f"<LineStyle><color>{outline_kml_color}</color><width>{outline_weight}</width></LineStyle>")
        kml_lines.append(f"<PolyStyle><color>{fill_kml_color}</color></PolyStyle>")
        kml_lines.append("</Style>")
        # Geometry
        geom = feat.get("geometry", {})
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        # Normalize to list of polygons (each polygon may have inner rings)
        polygons = []
        if gtype == "Polygon":
            polygons.append(coords)
        elif gtype == "MultiPolygon":
            polygons.extend(coords)
        else:
            continue  # skip if not polygon type
        if len(polygons) > 1:
            kml_lines.append("<MultiGeometry>")
        for poly in polygons:
            if not poly: 
                continue
            outer = poly[0]
            # ensure closed ring
            if outer[0] != outer[-1]:
                outer = outer + [outer[0]]
            kml_lines.append("<Polygon><outerBoundaryIs><LinearRing><coordinates>")
            kml_lines.append(" ".join(f"{x},{y},0" for x, y in outer))
            kml_lines.append("</coordinates></LinearRing></outerBoundaryIs>")
            # inner holes
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

# Helper function to generate a zipped Shapefile (bytes) from features
def generate_shapefile(features: list, region: str) -> bytes:
    # Create a temporary shapefile using pyshp
    # Use an in-memory BytesIO by writing files to a temp directory and zipping them
    import os, io, zipfile
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
            sec_val = ""  # QLD has no section
            plan_val = props.get("plan", "") or ""
        else:  # NSW
            lot_val = props.get("lotnumber", "") or ""
            sec_val = props.get("sectionnumber", "") or ""
            plan_val = props.get("planlabel", "") or ""
        w.record(lot_val, sec_val, plan_val)
        geom = feat.get("geometry", {})
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        parts = []
        if gtype == "Polygon":
            # coords is [outer, hole1, hole2,...]
            for ring in coords:
                if ring and ring[0] != ring[-1]:
                    ring = ring + [ring[0]]
                parts.append(ring)
        elif gtype == "MultiPolygon":
            # coords is list of Polygons
            for poly in coords:
                for ring in poly:
                    if ring and ring[0] != ring[-1]:
                        ring = ring + [ring[0]]
                    parts.append(ring)
        if parts:
            w.poly(parts)
    w.close()
    # Write .prj file for WGS84
    prj_text = ('GEOGCS["WGS 84",DATUM["WGS_1984",'
                'SPHEROID["WGS 84",6378137,298.257223563],'
                'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0],'
                'UNIT["degree",0.0174532925199433],AUTHORITY["EPSG","4326"]]')
    with open(base_path + ".prj", "w") as prj:
        prj.write(prj_text)
    # Zip the files
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        for ext in (".shp", ".shx", ".dbf", ".prj"):
            file_path = base_path + ext
            if os.path.exists(file_path):
                z.write(file_path, arcname="parcels" + ext)
    # Cleanup temp files
    for ext in (".shp", ".shx", ".dbf", ".prj"):
        file_path = base_path + ext
        if os.path.exists(file_path):
            os.remove(file_path)
    os.rmdir(temp_dir)
    return zip_buffer.getvalue()

# Helper to compute bounding box (lat/lon) for a list of features
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
    # If no valid coords found, return default bounds
    if min_lat > max_lat or min_lon > max_lon:
        return [[-39, 137], [-9, 155]]
    return [[min_lat, min_lon], [max_lat, max_lon]]

# Set up layout columns: map (left) and sidebar controls (right)
col1, col2 = st.columns([3, 1], gap="small")

with col2:
    # Parcel search input form
    with st.form("search_form"):
        query_str = st.text_input("Parcel search", "", 
            help="Enter Lot/Plan (QLD) or Lot/Section/Plan (NSW). E.g., 6RP702264 or 5/1/1000 or 5//1000")
        submit = st.form_submit_button("Search")
    if submit:
        user_input = query_str.strip()
        if user_input == "":
            st.warning("Please enter a parcel identifier.")
        else:
            # Determine region by input format
            if "/" in user_input:
                region = "NSW"
                parts = user_input.split("/")
                if len(parts) == 3:
                    lot_str, sec_str, plan_str = parts[0].strip(), parts[1].strip(), parts[2].strip()
                elif len(parts) == 2:
                    lot_str, sec_str, plan_str = parts[0].strip(), "", parts[1].strip()
                else:
                    lot_str, sec_str, plan_str = "", "", ""
                # Extract numeric part of plan (remove any prefix like DP)
                plan_num = "".join(filter(str.isdigit, plan_str))
                if lot_str == "" or plan_num == "":
                    st.error("Invalid NSW format. Use Lot/Section/Plan (Section can be blank).")
                    st.session_state['features'] = []
                else:
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
                    if not feats:
                        st.warning("No parcels found for the given identifier.")
                    st.session_state['features'] = feats
                    st.session_state['region'] = region
            else:
                region = "QLD"
                inp = user_input.replace(" ", "").upper()
                import re
                match = re.match(r"^(\d+)([A-Z].+)$", inp)
                if not match:
                    st.error("Invalid QLD format. Use LotNumber followed by Plan (e.g. 6RP702264).")
                    st.session_state['features'] = []
                else:
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
                    if not feats:
                        st.warning("No parcels found for the given identifier.")
                    st.session_state['features'] = feats
                    st.session_state['region'] = region
    # If we have parcel features, show style controls, results table, and export options
    if st.session_state.get('features'):
        features = st.session_state['features']
        region = st.session_state.get('region', 'QLD')
        if features:  # only proceed if list is non-empty
            # Styling controls
            fill_color = st.color_picker("Fill color", "#FF0000", key="fill_color")
            outline_color = st.color_picker("Outline color", "#000000", key="outline_color")
            fill_opacity = st.slider("Fill opacity", 0.0, 1.0, 0.5, step=0.01, key="fill_opacity")
            outline_weight = st.slider("Outline weight", 1, 10, 2, key="outline_weight")
            # Results table with selectable rows
            # Prepare data for table display
            if region == "QLD":
                data = [{"Lot": f["properties"].get("lot"), 
                         "Plan": f["properties"].get("plan"), 
                         "Locality": f["properties"].get("locality")} for f in features]
                column_defs = ["Lot", "Plan", "Locality"]
            else:  # NSW
                data = []
                for f in features:
                    props = f["properties"]
                    lot = props.get("lotnumber"); sec = props.get("sectionnumber") or ""
                    planlabel = props.get("planlabel")
                    data.append({"Lot": lot, "Section": sec, "Plan": planlabel})
                column_defs = ["Lot", "Section", "Plan"]
            df = pd.DataFrame(data, columns=column_defs)
            gb = GridOptionsBuilder.from_dataframe(df)
            gb.configure_selection(selection_mode="multiple", use_checkbox=True)
            gb.configure_grid_options(domLayout='normal')  # fixed height scrollable table
            gridOptions = gb.build()
            grid_resp = AgGrid(df, gridOptions=gridOptions, height=250, update_mode=GridUpdateMode.SELECTION_CHANGED, theme="streamlit")
            sel_rows = grid_resp.get("selected_rows", [])
            for r in sel_rows:  # remove index if present
                r.pop("index", None)
            st.session_state['selected_rows'] = sel_rows
            # Zoom to selected button and export options
            zoom_clicked = st.button("Zoom to Selected")
            export_selected_only = st.checkbox("Export only selected parcels", value=False)
            # Determine selected features list based on selected rows
            selected_features = []
            if sel_rows:
                if region == "QLD":
                    for row in sel_rows:
                        for feat in features:
                            if feat["properties"].get("lot") == row["Lot"] and feat["properties"].get("plan") == row["Plan"]:
                                selected_features.append(feat); break
                else:  # NSW
                    for row in sel_rows:
                        for feat in features:
                            props = feat["properties"]
                            if props.get("lotnumber") == row["Lot"] and (props.get("sectionnumber") or "") == row["Section"] and props.get("planlabel") == row["Plan"]:
                                selected_features.append(feat); break
            # If no selection, use all for "selected" export
            if not selected_features:
                selected_features = features
            st.session_state['selected_features'] = selected_features
            # Prepare export files
            kml_all = generate_kml(features, region, fill_color, fill_opacity, outline_color, outline_weight)
            shp_all = generate_shapefile(features, region)
            kml_sel = generate_kml(selected_features, region, fill_color, fill_opacity, outline_color, outline_weight)
            shp_sel = generate_shapefile(selected_features, region)
            # Download buttons
            st.download_button("Download KML", data=(kml_sel if export_selected_only else kml_all), file_name="parcels.kml")
            st.download_button("Download SHP", data=(shp_sel if export_selected_only else shp_all), file_name="parcels.zip")

with col1:
    # Initialize folium map
    base_map = folium.Map(location=[-23.5, 143.0], zoom_start=5, tiles=None)
    # Add base map layers
    folium.TileLayer('OpenStreetMap', name='OpenStreetMap', control=True).add_to(base_map)
    folium.TileLayer('CartoDB positron', name='CartoDB Positron', control=True).add_to(base_map)
    folium.TileLayer('CartoDB dark_matter', name='CartoDB Dark', control=True).add_to(base_map)
    folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google', name='Google Satellite', control=True).add_to(base_map)
    # Add parcel features layer if available
    if st.session_state.get('features') and st.session_state['features']:
        features = st.session_state['features']
        # Use current style settings from session_state
        fill_color = st.session_state.get('fill_color', "#FF0000")
        outline_color = st.session_state.get('outline_color', "#000000")
        opacity = st.session_state.get('fill_opacity', 0.5)
        weight = st.session_state.get('outline_weight', 2)
        folium.GeoJson(
            data={"type": "FeatureCollection", "features": features},
            name="Parcels",
            style_function=lambda feat: {"fillColor": fill_color, "color": outline_color, "weight": weight, "fillOpacity": opacity}
        ).add_to(base_map)
        # Determine bounds for zoom
        if 'selected_features' in st.session_state and zoom_clicked and st.session_state['selected_features']:
            bounds = get_bounds(st.session_state['selected_features'])
        else:
            bounds = get_bounds(features)
        base_map.fit_bounds(bounds)
    else:
        # Default view covering QLD and NSW
        base_map.fit_bounds([[-39, 137], [-9, 155]])
    folium.LayerControl(collapsed=True).add_to(base_map)
    # Render map to HTML and embed
    map_html = base_map._repr_html_()
    st.components.v1.html(map_html, height=600, width=None, scrolling=False)
