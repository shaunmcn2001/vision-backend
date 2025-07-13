#!/usr/bin/env python3
import streamlit as st
import folium
from folium.plugins import MousePosition
from streamlit_folium import st_folium
import pandas as pd
import requests, io, tempfile, zipfile, os
import simplekml
import geopandas as gpd
from shapely.geometry import shape

# Page configuration and custom CSS
st.set_page_config(
    page_title="Parcel Toolkit",
    page_icon="📍",
    layout="wide",
    initial_sidebar_state="collapsed"
)
st.markdown("""
<style>
#MainMenu, footer, header { visibility: hidden !important; }
div.block-container { padding: 0 1rem !important; }
.leaflet-control-layers-list {
  background: rgba(40,40,40,0.8) !important;
  color: #fafafa !important;
}
</style>
""", unsafe_allow_html=True)

# Session state defaults
ss = st.session_state
ss.setdefault("features_qld", [])
ss.setdefault("features_nsw", [])
ss.setdefault("results_df", pd.DataFrame(columns=["Parcel ID", "State", "Locality", "Area (m²)"]))
ss.setdefault("style_fill", "#009FDF")
ss.setdefault("style_opacity", 40)    # opacity percentage
ss.setdefault("style_weight", 3)      # outline weight in px
ss.setdefault("zoom_bounds", None)    # for map zooming to selection

# Create tabs for layout
tab_search, tab_map = st.tabs(["Parcel Search", "Map View"])

# --- Parcel Search & Results Tab ---
with tab_search:
    st.subheader("Lot/Plan Search")
    lot_input = st.text_area(
        "Enter IDs (one per line)",
        height=140,
        placeholder="e.g.\n6RP702264\n5//15006\n5/1/1000"
    )
    if st.button("🔍 Search Parcels", use_container_width=True):
        # Reset previous search results
        ss.features_qld = []
        ss.features_nsw = []
        ss.results_df = pd.DataFrame(columns=["Parcel ID", "State", "Locality", "Area (m²)"])
        ss.zoom_bounds = None  # clear any previous zoom setting

        # Parse input lines into QLD and NSW IDs
        ids = [line.strip() for line in lot_input.splitlines() if line.strip()]
        qld_ids, nsw_ids = [], []
        for idv in ids:
            parts = idv.split("/")
            if len(parts) == 3:
                # Format: lot/section/plan (NSW input with section) -> use lot//plan (section omitted)
                lot_num = parts[0]
                plan_num = parts[2].upper()
                nsw_ids.append(f"{lot_num}//{plan_num}")
            elif len(parts) == 2:
                # Format: lot/plan
                lot_num = parts[0]
                plan_id = parts[1].upper()
                if plan_id.startswith(("DP", "SP", "PP")):
                    # If plan has NSW prefix, format as lot//PLAN
                    nsw_ids.append(f"{lot_num}//{plan_id}")
                else:
                    # Otherwise, assume QLD format (lot + plan without slash)
                    qld_ids.append(lot_num.upper() + plan_id)
            else:
                # Single token (e.g., 6RP702264) – treat as QLD lot-plan
                qld_ids.append(idv.upper())
        # Remove duplicates
        qld_ids = list(dict.fromkeys(qld_ids))
        nsw_ids = list(dict.fromkeys(nsw_ids))

        # Query QLD parcels (if any QLD IDs)
        if qld_ids:
            qld_url = ("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
                       "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
            where_clause = " OR ".join([f"lotplan='{i}'" for i in qld_ids])
            params = {
                "where": where_clause,
                "outFields": "lotplan,locality,lot_area",
                "f": "geojson",
                "outSR": "4326"
            }
            try:
                resp = requests.get(qld_url, params=params, timeout=12)
                data = resp.json()
                ss.features_qld = data.get("features", [])
            except Exception as e:
                st.error("QLD query failed.")

        # Query NSW parcels (if any NSW IDs)
        if nsw_ids:
            nsw_url = ("https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Cadastre/MapServer/9/query")
            where_clause = " OR ".join([f"lotidstring='{i}'" for i in nsw_ids])
            params = {
                "where": where_clause,
                "outFields": "lotidstring,planlotarea",
                "f": "geojson",
                "outSR": "4326"
            }
            try:
                resp = requests.get(nsw_url, params=params, timeout=12)
                data = resp.json()
                ss.features_nsw = data.get("features", [])
            except Exception as e:
                st.error("NSW query failed.")

        # Build results DataFrame
        records = []
        for feat in ss.features_qld:
            props = feat["properties"]
            records.append({
                "Parcel ID": props.get("lotplan", ""),
                "State": "QLD",
                "Locality": props.get("locality", ""),
                "Area (m²)": props.get("lot_area", None)
            })
        for feat in ss.features_nsw:
            props = feat["properties"]
            records.append({
                "Parcel ID": props.get("lotidstring", ""),
                "State": "NSW",
                "Locality": "",  # NSW data doesn't provide locality in this query
                "Area (m²)": props.get("planlotarea", None)
            })
        ss.results_df = pd.DataFrame(records)

    # Display style settings and results if any parcels were found
    df = ss.results_df
    if not df.empty:
        st.markdown("**Style Settings**")
        col1, col2, col3 = st.columns(3)
        ss.style_fill = col1.color_picker("Fill Color", ss.style_fill)
        ss.style_opacity = col2.slider("Opacity (%)", 0, 100, ss.style_opacity)
        ss.style_weight = col3.slider("Outline (px)", 1, 10, ss.style_weight)

        st.markdown("**Search Results**")
        # Set up AgGrid with pagination, filtering, and multi-select
        from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
        gb = GridOptionsBuilder.from_dataframe(df)
        gb.configure_pagination(enabled=True)
        gb.configure_selection(selection_mode="multiple", use_checkbox=True)
        gb.configure_default_column(filter=True, sortable=True)
        grid_response = AgGrid(
            df,
            gridOptions=gb.build(),
            update_mode=GridUpdateMode.MODEL_CHANGED,
            theme="streamlit"  # Use a valid theme (streamlit, alpine, balham, material)
        )
        selected_rows = grid_response["selected_rows"]

        # Action buttons for selected parcels
        b_zoom, b_kml_sel, b_shp_sel, b_kml_all, b_shp_all = st.columns(5)
        # 1. Zoom to Selected
        if b_zoom.button("Zoom to Selected"):
            all_lats, all_lons = [], []
            for row in selected_rows:
                pid = row["Parcel ID"]
                # Find matching feature in either QLD or NSW results
                for feat in ss.features_qld + ss.features_nsw:
                    props = feat["properties"]
                    key = props.get("lotplan", props.get("lotidstring"))
                    if key == pid:
                        geom = feat["geometry"]
                        # Get coordinates from Polygon or MultiPolygon
                        if geom["type"] == "Polygon":
                            coords = geom["coordinates"][0]
                        elif geom["type"] == "MultiPolygon":
                            coords = geom["coordinates"][0][0]  # first polygon of multipolygon
                        else:
                            coords = []  # other geometry types not expected here
                        # Collect all latitude and longitude values
                        for lon, lat in coords:
                            all_lons.append(lon)
                            all_lats.append(lat)
            # Set map bounds in session state if we have coordinates
            if all_lats and all_lons:
                sw_corner = [min(all_lats), min(all_lons)]
                ne_corner = [max(all_lats), max(all_lons)]
                ss.zoom_bounds = [sw_corner, ne_corner]
            else:
                ss.zoom_bounds = None

        # 2. Export Selected KML
        if b_kml_sel.button("Export Selected KML"):
            kml = simplekml.Kml()
            # Convert color to KML format (aabbggrr hex)
            alpha_hex = f"{int(ss.style_opacity/100 * 255):02x}"
            rgb_hex = ss.style_fill.lstrip("#")
            kml_color = alpha_hex + rgb_hex[4:6] + rgb_hex[2:4] + rgb_hex[0:2]
            for row in selected_rows:
                pid = row["Parcel ID"]
                for feat in ss.features_qld + ss.features_nsw:
                    props = feat["properties"]
                    key = props.get("lotplan", props.get("lotidstring"))
                    if key == pid:
                        geom = feat["geometry"]
                        poly = kml.newpolygon(name=pid)
                        if geom["type"] == "Polygon":
                            poly.outerboundaryis = geom["coordinates"][0]
                        elif geom["type"] == "MultiPolygon":
                            # For MultiPolygon, add all parts
                            for part in geom["coordinates"]:
                                inner_poly = kml.newpolygon(name=pid)
                                inner_poly.outerboundaryis = part[0]
                                inner_poly.style.polystyle.color = kml_color
                                inner_poly.style.linestyle.color = kml_color
                                inner_poly.style.linestyle.width = ss.style_weight
                        # Apply style to KML polygon
                        poly.style.polystyle.color = kml_color
                        poly.style.linestyle.color = kml_color
                        poly.style.linestyle.width = ss.style_weight
            kml_bytes = kml.kml().encode("utf-8")
            b_kml_sel.download_button("⬇️ KML", data=kml_bytes, file_name="selected_parcels.kml", mime="application/vnd.google-earth.kml+xml")

        # 3. Export Selected SHP (Shapefile in ZIP)
        if b_shp_sel.button("Export Selected SHP"):
            rows, geoms = [], []
            for row in selected_rows:
                pid = row["Parcel ID"]
                state = row["State"]
                for feat in ss.features_qld + ss.features_nsw:
                    props = feat["properties"]
                    key = props.get("lotplan", props.get("lotidstring"))
                    if key == pid:
                        rows.append({"Parcel ID": pid, "State": state})
                        geoms.append(shape(feat["geometry"]))
            if rows:
                gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
                # Helper to zip the shapefile components
                def gdf_to_shp_zip(gdf):
                    tmpdir = tempfile.mkdtemp()
                    shp_path = os.path.join(tmpdir, "parcels.shp")
                    gdf.to_file(shp_path, driver="ESRI Shapefile")
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w") as zf:
                        for fname in os.listdir(tmpdir):
                            zf.write(os.path.join(tmpdir, fname), arcname=fname)
                    return buf.getvalue()
                zip_data = gdf_to_shp_zip(gdf)
                b_shp_sel.download_button("⬇️ SHP", data=zip_data, file_name="selected_parcels.zip", mime="application/zip")

        # 4. Export All KML
        if b_kml_all.button("Export All KML"):
            kml = simplekml.Kml()
            alpha_hex = f"{int(ss.style_opacity/100 * 255):02x}"
            rgb_hex = ss.style_fill.lstrip("#")
            kml_color = alpha_hex + rgb_hex[4:6] + rgb_hex[2:4] + rgb_hex[0:2]
            for feat in ss.features_qld + ss.features_nsw:
                pid = feat["properties"].get("lotplan", feat["properties"].get("lotidstring", ""))
                geom = feat["geometry"]
                poly = kml.newpolygon(name=pid)
                if geom["type"] == "Polygon":
                    poly.outerboundaryis = geom["coordinates"][0]
                elif geom["type"] == "MultiPolygon":
                    for part in geom["coordinates"]:
                        inner_poly = kml.newpolygon(name=pid)
                        inner_poly.outerboundaryis = part[0]
                        inner_poly.style.polystyle.color = kml_color
                        inner_poly.style.linestyle.color = kml_color
                        inner_poly.style.linestyle.width = ss.style_weight
                poly.style.polystyle.color = kml_color
                poly.style.linestyle.color = kml_color
                poly.style.linestyle.width = ss.style_weight
            kml_bytes = kml.kml().encode("utf-8")
            b_kml_all.download_button("⬇️ KML", data=kml_bytes, file_name="all_parcels.kml", mime="application/vnd.google-earth.kml+xml")

        # 5. Export All SHP
        if b_shp_all.button("Export All SHP"):
            rows, geoms = [], []
            for feat in ss.features_qld + ss.features_nsw:
                pid = feat["properties"].get("lotplan", feat["properties"].get("lotidstring", ""))
                state = "QLD" if feat in ss.features_qld else "NSW"
                rows.append({"Parcel ID": pid, "State": state})
                geoms.append(shape(feat["geometry"]))
            if rows:
                gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
                def gdf_to_shp_zip(gdf):
                    tmpdir = tempfile.mkdtemp()
                    shp_path = os.path.join(tmpdir, "parcels.shp")
                    gdf.to_file(shp_path, driver="ESRI Shapefile")
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w") as zf:
                        for fname in os.listdir(tmpdir):
                            zf.write(os.path.join(tmpdir, fname), arcname=fname)
                    return buf.getvalue()
                zip_data = gdf_to_shp_zip(gdf)
                b_shp_all.download_button("⬇️ SHP", data=zip_data, file_name="all_parcels.zip", mime="application/zip")

# --- Map View Tab ---
with tab_map:
    # Initialize base map centered roughly over QLD/NSW
    m = folium.Map(location=[-25.0, 145.0], zoom_start=6, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="OSM", overlay=False).add_to(m)
    folium.TileLayer("CartoDB dark_matter", name="Carto Dark", overlay=False).add_to(m)
    folium.TileLayer(
        tiles="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
        attr="Google Satellite",
        name="Google Satellite",
        overlay=False
    ).add_to(m)
    # Add mouse position display and layer control toggler
    MousePosition().add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    # Add parcel GeoJSON features with the chosen style
    style_function = lambda feature: {
        "fillColor": ss.style_fill,
        "color": ss.style_fill,
        "fillOpacity": ss.style_opacity / 100.0,
        "weight": ss.style_weight
    }
    for feat in ss.features_qld + ss.features_nsw:
        folium.GeoJson(feat, style_function=style_function).add_to(m)

    # If a zoom boundary is set (from "Zoom to Selected"), apply it
    if ss.zoom_bounds:
        try:
            m.fit_bounds(ss.zoom_bounds)
        except Exception:
            pass
        ss.zoom_bounds = None  # reset after using

    # Render the Folium map in the Streamlit app
    st_folium(m, width="100%", height=700)
