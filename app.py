import streamlit as st
import pydeck as pdk
from streamlit_option_menu import option_menu

# Set wide layout and dark theme in page config
st.set_page_config(page_title="Parcel Toolkit", layout="wide", initial_sidebar_state="collapsed")

# Inject CSS to hide Streamlit branding and apply base dark background
st.markdown("""
    <style>
    /* Hide Streamlit menu and footer for clean UI */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    /* Optional: remove Streamlit header if needed */
    header {visibility: hidden;}
    /* Dark background for main app */
    .stApp, .stApp > main, .block-container {
        background-color: #1e1e1e !important;
        color: #FFFFFF !important;
    }
    /* Reduce padding around the main container to maximize space */
    .block-container {
        padding: 1rem 2rem;
    }
    </style>
    """, unsafe_allow_html=True)

# Define the accent color (for active menu highlight, etc.)
ACCENT_COLOR = "#f48020"  # Orange accent for highlights

# Initialize session state for collapse toggle
if "sidebar_collapsed" not in st.session_state:
    st.session_state.sidebar_collapsed = False

# Create a collapsible vertical menu for the side panels
# Using streamlit-option-menu for a vertical sidebar menu with icons
menu_options = ["Query", "Layers", "Downloads"]
menu_icons   = ["search", "layers", "download"]  # Bootstrap icon names
default_ix = 0  # default selected index

# If the sidebar is collapsed, we will render an icon-only menu (no text)
if st.session_state.sidebar_collapsed:
    # Use narrow columns: just menu and map (no content panel)
    col_menu, col_map = st.columns([0.08, 0.92])  # 8% width for icon bar
else:
    # Use three columns: menu, content panel, and map
    col_menu, col_panel, col_map = st.columns([0.1, 0.25, 0.65])

# Build the side menu inside col_menu
with col_menu:
    # Collapse/expand button at top of menu
    collapse_label = "«" if not st.session_state.sidebar_collapsed else "»"
    if st.button(collapse_label, help="Collapse/Expand side panel"):
        st.session_state.sidebar_collapsed = not st.session_state.sidebar_collapsed
        st.experimental_rerun()  # re-run to update layout immediately

    # Determine styling based on collapsed or expanded state
    if st.session_state.sidebar_collapsed:
        # Icon-only menu (hide labels by setting font-size 0 for text)
        selected_option = option_menu(
            None, menu_options, 
            icons=menu_icons, menu_icon="list", 
            default_index=default_ix,
            orientation="vertical",
            styles={
                "container": {"background-color": "#2b2b2b", "padding": "0"},
                "icon": {"color": "#fff", "font-size": "24px"},  # larger icons
                "nav-link": {"text-align": "center", "font-size": "0px", "padding": "6px 0"},
                "nav-link-selected": {"background-color": ACCENT_COLOR}  # highlight icon bg
            }
        )
    else:
        # Expanded menu with text labels
        selected_option = option_menu(
            None, menu_options,
            icons=menu_icons, menu_icon="list", 
            default_index=default_ix,
            orientation="vertical",
            styles={
                "container": {"background-color": "#2b2b2b", "padding": "0"},
                "icon": {"color": "#fff", "font-size": "18px"},
                "nav-link": {"color": "#fff", "text-align": "left", "padding": "10px 5px",
                             "font-size": "15px", "--hover-color": "#383838"},
                "nav-link-selected": {"background-color": ACCENT_COLOR, "color": "#fff"}
            }
        )

# Populate the content of the selected panel (when not collapsed)
if not st.session_state.sidebar_collapsed:
    with col_panel:
        st.markdown(f"**{selected_option} Panel**")  # Panel title (for demo purposes)
        if selected_option == "Query":
            st.subheader("Lot/Plan Lookup")
            # Input for searching (multi-line text area as per example)
            lot_input = st.text_area("Enter Lot/Plan IDs:", 
                                      placeholder="e.g. 6RP702264\n5/DP123456")
            format_choice = st.selectbox("Output format", ["Style & KML", "GeoJSON", "CSV"])
            if st.button("🔍 Search"):
                # Perform search (placeholder logic)
                results = [{"Lot/Plan": "6/RP702264", "Address": "123 Example St"}]  # dummy result
                st.session_state.search_results = results
            # Display results table if available
            if "search_results" in st.session_state:
                st.write(f"**Results:** {len(st.session_state.search_results)} records found")
                st.dataframe(st.session_state.search_results)  # show results in table
        elif selected_option == "Layers":
            st.subheader("Basemap")
            base = st.radio("", ["OpenStreetMap", "Esri Imagery"], index=0)
            st.text(" ")  # small spacer
            st.subheader("Overlays")
            layer1 = st.checkbox("Parcel Boundaries", True)
            layer2 = st.checkbox("Postal Zones", False)
            # (In a real app, toggling these would show/hide layers on the map)
        elif selected_option == "Downloads":
            st.subheader("Export Results")
            if st.session_state.get("search_results"):
                st.write("Choose export format:")
                # Export format buttons
                exp1, exp2, exp3 = st.columns(3)
                exp1.button("📄 CSV")
                exp2.button("📊 Excel")
                exp3.button("🗺️ Shapefile")
                st.write("Other Actions:")
                st.button("⭐ Save Results") 
                st.button("🗑️ Clear Results")
            else:
                st.info("No results to export. Perform a search first.")

# Always-visible map section
with col_map:
    # Use PyDeck for an always-visible map (fills remaining space)
    # Initial view centered on an example location
    view_state = pdk.ViewState(latitude=-27.47, longitude=153.02, zoom=10)
    # Example: no data layer (just base map)
    base_map = pdk.Deck(map_style=None, initial_view_state=view_state)  # map_style=None uses theme-based tiles
    st.pydeck_chart(base_map, use_container_width=True, height=600)
