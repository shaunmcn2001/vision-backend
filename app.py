import streamlit as st
import pydeck as pdk
import pandas as pd
import numpy as np
import geopandas as gpd
import simplekml
import requests
import json

# Note: Ensure required packages are installed:
# pip install streamlit-option-menu geopandas simplekml

# Set page config for wide layout and dark theme preferences
st.set_page_config(page_title="Cadastral Parcel Viewer", page_icon="🗺️", layout="wide", initial_sidebar_state="expanded")

# Load custom CSS for styling
with open("style.css") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# Initialize session state for results and map view
if "results_gdf" not in st.session_state:
    st.session_state["results_gdf"] = gpd.GeoDataFrame(columns=["State", "ID", "Section", "Area", "geometry"])
if "map_view" not in st.session_state:
    # Default view roughly over Australia
    st.session_state["map_view"] = pdk.ViewState(latitude=-23.5, longitude=133.0, zoom=4)

def compute_view_bounds(bounds):
    """Compute a reasonable map view (center lat/lon and zoom level) to fit given bounds."""
    minx, miny, maxx, maxy = bounds  # (minLon, minLat, maxLon, maxLat)
    center_lon = (minx + maxx) / 2
    center_lat = (miny + maxy) / 2
    # Determine the larger span of lat or lon in degrees
    lat_range = maxy - miny
    lon_range = maxx - minx
    max_range = max(lat_range, lon_range)
    # Rough estimate for zoom (smaller range -> closer zoom)
    if max_range <= 0:
        zoom = 15
    elif max_range < 0.0005:
        zoom = 18
    elif max_range < 0.001:
        zoom = 17
    elif max_range < 0.002:
        zoom = 16
    elif max_range < 0.005:
        zoom = 15
    elif max_range < 0.01:
        zoom = 14
    elif max_range < 0.02:
        zoom = 13
    elif max_range < 0.05:
        zoom = 12
    elif max_range < 0.1:
        zoom = 11
    elif max_range < 0.2:
        zoom = 10
    elif max_range < 0.5:
        zoom = 9
    elif max_range < 1:
        zoom = 8
    elif max_range < 2:
        zoom = 7
    elif max_range < 5:
        zoom = 6
    elif max_range < 10:
        zoom = 5
    elif max_range < 20:
        zoom = 4
    elif max_range < 40:
        zoom = 3
    elif max_range < 80:
        zoom = 2
    else:
        zoom = 1
    return center_lat, center_lon, zoom

# Sidebar with collapsible menu sections
from streamlit_option_menu import option_menu
with st.sidebar:
    selected = option_menu(
        menu_title=None,
        options=["Query", "Layers"],
        icons=["search", "layers"],
        menu_icon="list",
        default_index=0,
        orientation="vertical",
        styles={
            "container": {"background-color": "#2B3035"},
            "icon": {"color": "white", "font-size": "18px"},
            "nav-link": {"color": "white", "font-size": "16px", "text-align": "left", "--hover-color": "#505060"},
            "nav-link-selected": {"background-color": "#54525E"}
        }
    )
    if selected == "Query":
        st.subheader("Lot/Plan Query")
        lotplan_input = st.text_area("Enter Lot/Plan IDs (one per line):")
        search_btn = st.button("Search / Add")
        if search_btn:
            query_lines = [lp.strip() for lp in lotplan_input.splitlines() if lp.strip()]
            if not query_lines:
                st.warning("Please enter at least one Lot/Plan ID.")
            else:
                not_found = []
                new_results = []
                for query in query_lines:
                    if "/" not in query:
                        not_found.append(query)
                        continue
                    lot_str, plan_str = query.split("/", 1)
                    lot = lot_str.strip()
                    plan = plan_str.strip()
                    if lot == "" or plan == "":
                        continue
                    # Determine likely state by plan prefix
                    plan_upper = plan.upper()
                    lot_upper = lot.upper()
                    qld_prefixes = ("RP", "SP", "CP", "BUP")  # QLD plan prefixes
                    found = False
                    # Try QLD if prefix is QLD-type or if not DP
                    if plan_upper.startswith(qld_prefixes) or not plan_upper.startswith("DP"):
                        qld_url = "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query"
                        where_clause = f"UPPER(lotplan)='{lot_upper}/{plan_upper}'"
                        params = {"f": "geojson", "outFields": "lot,plan,lotplan,lot_area", "where": where_clause, "outSR": "4326"}
                        try:
                            resp = requests.get(qld_url, params=params, timeout=5)
                            data = resp.json()
                        except Exception:
                            data = {}
                        if data.get("features"):
                            gdf = gpd.GeoDataFrame.from_features(data, crs="EPSG:4326")
                            # Format QLD result
                            gdf.rename(columns={"lotplan": "ID", "lot_area": "Area"}, inplace=True)
                            gdf["State"] = "QLD"
                            gdf["Section"] = np.nan
                            gdf = gdf[["State", "ID", "Section", "Area", "geometry"]]
                            new_results.append(gdf)
                            found = True
                    # Try NSW if likely NSW prefix or QLD search was empty
                    if not found and (plan_upper.startswith("DP") or plan_upper.startswith("SP") or not plan_upper.startswith(qld_prefixes)):
                        nsw_url = "http://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Cadastre/MapServer/9/query"
                        where_clause = f"lotnumber='{lot_upper}' AND planlabel='{plan_upper}'"
                        params = {"f": "geojson", "outFields": "lotnumber,sectionnumber,planlabel,planlotarea", "where": where_clause, "outSR": "4326"}
                        try:
                            resp = requests.get(nsw_url, params=params, timeout=5)
                            data = resp.json()
                        except Exception:
                            data = {}
                        if data.get("features"):
                            gdf = gpd.GeoDataFrame.from_features(data, crs="EPSG:4326")
                            # Format NSW result
                            gdf["ID"] = gdf["lotnumber"].astype(str) + "/" + gdf["planlabel"]
                            gdf.rename(columns={"planlotarea": "Area", "sectionnumber": "Section"}, inplace=True)
                            gdf["State"] = "NSW"
                            # Replace empty section with NaN for clarity
                            if "Section" in gdf.columns:
                                gdf["Section"] = gdf["Section"].replace("", np.nan)
                            else:
                                gdf["Section"] = np.nan
                            gdf = gdf[["State", "ID", "Section", "Area", "geometry"]]
                            new_results.append(gdf)
                            found = True
                    if not found:
                        not_found.append(query)
                # Update results table and map view
                if new_results:
                    new_results_gdf = pd.concat(new_results, ignore_index=True)
                    st.session_state["results_gdf"] = pd.concat([st.session_state["results_gdf"], new_results_gdf], ignore_index=True)
                    # Clean up Area values (fill NaN and convert to int for display)
                    if "Area" in st.session_state["results_gdf"].columns:
                        st.session_state["results_gdf"]["Area"] = st.session_state["results_gdf"]["Area"].fillna(0).round(0).astype(int)
                    # Zoom map to show newly added parcels
                    bounds = new_results_gdf.total_bounds
                    if not np.any(np.isnan(bounds)):
                        lat, lon, zoom = compute_view_bounds(bounds)
                        st.session_state["map_view"] = pdk.ViewState(latitude=lat, longitude=lon, zoom=zoom)
                if not_found:
                    st.warning(f"Not found: {', '.join(not_found)}")
    elif selected == "Layers":
        st.subheader("Map Layers")
        qld_layer_on = st.checkbox("Show QLD Cadastre overlay (WMS)", value=False)
        nsw_layer_on = st.checkbox("Show NSW Cadastre overlay (WMS)", value=False)

# Build map layers for pydeck
layers_list = []
# QLD cadastral WMS overlay
if 'qld_layer_on' in locals() and qld_layer_on:
    qld_wms_layer = pdk.Layer(
        "_WMSLayer",
        data="https://spatial-gis.information.qld.gov.au/arcgis/services/PlanningCadastre/LandParcelPropertyFramework/MapServer/WMSServer",
        service_type="wms",
        layers=["Cadastral parcels"],
        opacity=0.8
    )
    layers_list.append(qld_wms_layer)
# NSW cadastral WMS overlay
if 'nsw_layer_on' in locals() and nsw_layer_on:
    nsw_wms_layer = pdk.Layer(
        "_WMSLayer",
        data="http://maps.six.nsw.gov.au/arcgis/services/public/NSW_Cadastre/MapServer/WMSServer",
        service_type="wms",
        layers=["Lot"],
        opacity=0.8
    )
    layers_list.append(nsw_wms_layer)
# Layer for queried parcel polygons (GeoJSON)
if len(st.session_state["results_gdf"]) > 0:
    geojson_data = json.loads(st.session_state["results_gdf"].to_json())
    result_layer = pdk.Layer(
        "GeoJsonLayer",
        geojson_data,
        id="parcels",
        pickable=True,
        stroked=True,
        filled=True,
        opacity=0.6,
        get_fill_color=[255, 0, 0, 100],   # semi-transparent red fill
        get_line_color=[255, 255, 0],     # yellow outline
        get_line_width=2
    )
    layers_list.append(result_layer)

# Render deck.gl map
deck = pdk.Deck(map_style="mapbox://styles/mapbox/dark-v10", initial_view_state=st.session_state["map_view"], layers=layers_list)
st.pydeck_chart(deck, use_container_width=True)

# Expandable results panel (collapsible section in main area)
if len(st.session_state["results_gdf"]) > 0:
    expander = st.expander("Results", expanded=True)
else:
    expander = st.expander("Results")
with expander:
    if len(st.session_state["results_gdf"]) == 0:
        st.write("No parcels loaded yet.")
    else:
        # Display results table
        res_df = st.session_state["results_gdf"].copy()
        res_df_display = res_df[["State", "ID", "Section", "Area"]].fillna("")
        st.dataframe(res_df_display, use_container_width=True)
        # Selection for export
        indices = res_df_display.index.tolist()
        selected_idx = st.multiselect(
            "Select parcels to export (leave empty to export all):",
            options=indices,
            format_func=lambda i: f"{res_df_display.loc[i,'State']} - {res_df_display.loc[i,'ID']}" + (f" (Sec {res_df_display.loc[i,'Section']})" if res_df_display.loc[i,'Section'] not in ["", np.nan] else "")
        )
        # Style controls for exports
        st.write("**Export Style:**")
        col1, col2, col3 = st.columns(3)
        with col1:
            fill_color = st.color_picker("Fill Color", "#FF0000")
        with col2:
            fill_opacity = st.slider("Fill Opacity (%)", 0, 100, 50)
        with col3:
            outline_width = st.number_input("Outline Width (px)", min_value=0, max_value=10, value=2)
        # Convert fill_color and opacity to KML color format (AABBGGRR)
        hex_color = fill_color.lstrip("#")
        if len(hex_color) == 6:
            r, g, b = hex_color[0:2], hex_color[2:4], hex_color[4:6]
        else:
            r, g, b = "FF", "00", "00"  # default to red if parsing fails
        alpha = format(int(fill_opacity/100 * 255), "02x")
        kml_fill_color = alpha + b + g + r
        kml_outline_color = "ff000000"  # opaque black
        # Determine which parcels to export
        export_gdf = st.session_state["results_gdf"] if len(selected_idx) == 0 else st.session_state["results_gdf"].iloc[selected_idx]
        export_gdf = export_gdf.copy().reset_index(drop=True)
        # Create KML using simplekml
        kml = simplekml.Kml()
        for idx, row in export_gdf.iterrows():
            geom = row.geometry
            name = f"{row['State']} {row['ID']}"
            if geom.geom_type == "Polygon":
                coords = [(x, y) for x, y in geom.exterior.coords]
                inner_coords = [list(hole.coords) for hole in geom.interiors] if geom.interiors else []
                pol = kml.newpolygon(name=name, outerboundaryis=coords, innerboundaryis=inner_coords)
                pol.style.polystyle.color = kml_fill_color
                if outline_width > 0:
                    pol.style.linestyle.color = kml_outline_color
                    pol.style.linestyle.width = outline_width
                    pol.style.polystyle.outline = 1
                else:
                    pol.style.polystyle.outline = 0
            elif geom.geom_type == "MultiPolygon":
                for poly in geom:
                    coords = [(x, y) for x, y in poly.exterior.coords]
                    inner_coords = [list(hole.coords) for hole in poly.interiors] if poly.interiors else []
                    pol = kml.newpolygon(name=name, outerboundaryis=coords, innerboundaryis=inner_coords)
                    pol.style.polystyle.color = kml_fill_color
                    if outline_width > 0:
                        pol.style.linestyle.color = kml_outline_color
                        pol.style.linestyle.width = outline_width
                        pol.style.polystyle.outline = 1
                    else:
                        pol.style.polystyle.outline = 0
            elif geom.geom_type == "Point":
                kml.newpoint(name=name, coords=[(geom.x, geom.y)])
            elif geom.geom_type in ["LineString", "LinearRing"]:
                kml.newlinestring(name=name, coords=[(x, y) for x, y in geom.coords])
        kml_str = kml.kml()
        # Create shapefile zip in memory
        shp_bytes = None
        try:
            import tempfile, os, zipfile, io
            tmpdir = tempfile.TemporaryDirectory()
            shp_path = os.path.join(tmpdir.name, "parcels.shp")
            export_gdf.to_file(shp_path, driver="ESRI Shapefile")
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w") as z:
                base_name = "parcels"
                for ext in [".shp", ".shx", ".dbf", ".cpg", ".prj"]:
                    file_path = os.path.join(tmpdir.name, base_name + ext)
                    if os.path.exists(file_path):
                        z.write(file_path, arcname=base_name + ext)
            zip_buf.seek(0)
            shp_bytes = zip_buf.getvalue()
        except Exception:
            shp_bytes = None
        # Download buttons for KML and Shapefile
        dcol1, dcol2 = st.columns(2)
        with dcol1:
            st.download_button("Download KML", data=kml_str.encode("utf-8"), file_name="parcels.kml", mime="application/vnd.google-earth.kml+xml")
        with dcol2:
            if shp_bytes:
                st.download_button("Download Shapefile (ZIP)", data=shp_bytes, file_name="parcels_shapefile.zip", mime="application/zip")
            else:
                st.download_button("Download Shapefile (ZIP)", data=b"", file_name="parcels_shapefile.zip", disabled=True)
