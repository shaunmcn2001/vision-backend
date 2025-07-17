diff --git a/app.py b/app.py
index 0081604d6372a949eab54a40c506ffdda61b0423..e96674fcba95583c86ade4451acc9ec9a6161968 100644
--- a/app.py
+++ b/app.py
@@ -1,82 +1,44 @@
 import streamlit as st
 
 st.set_page_config(page_title="Parcel Viewer", layout="wide")
 
-st.markdown(
-    """
-    <style>
-    #MainMenu, header, footer {visibility: hidden;}
-    [data-testid="stAppViewContainer"] .main .block-container {padding: 0;}
-    .loading-icon {
-        position: absolute;
-        top: 50%;
-        left: 50%;
-        transform: translate(-50%, -50%);
-        width: 40px;
-        height: 40px;
-        border: 4px solid #f3f3f3;
-        border-top: 4px solid #00ff00;
-        border-radius: 50%;
-        animation: spin 1s linear infinite;
-        z-index: 1000;
-        display: none;
-    }
-    @keyframes spin {
-        0% { transform: translate(-50%, -50%) rotate(0deg); }
-        100% { transform: translate(-50%, -50%) rotate(360deg); }
-    }
-    .loading-active .loading-icon {
-        display: block;
-    }
-    .map-container {
-        height: 100vh;
-        width: 100%;
-        overflow: auto;
-    }
-    .map-container iframe {
-        height: 100%;
-        width: 100%;
-        border: none;
-    }
-    </style>
-    """,
-    unsafe_allow_html=True,
-)
+with open("style.css") as f:
+    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
 
 import requests, folium, pandas as pd, re
 from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode
 
 from kml_utils import (
     _hex_to_kml_color,
     generate_kml,
     generate_shapefile,
     get_bounds,
 )
 
 # Sidebar and map layout
-map_col, sidebar_col = st.columns([4, 1], gap="small")
+map_col, sidebar_col = st.columns([5, 1], gap="small")
 
 with sidebar_col:
     st.markdown("<div class='loading-icon'></div>", unsafe_allow_html=True)
     with st.expander("Search Parcels", expanded=True):
         with st.form("search_form"):
             bulk_query = st.text_area(
                 "Parcel search (bulk):",
                 "",
                 help="Enter Lot/Plan (QLD) or Lot/Section/Plan (NSW) one per line.",
             )
             submit = st.form_submit_button("Search")
     if submit:
         st.session_state["loading"] = True
         st.markdown(
             "<script>document.querySelector('.stApp').classList.add('loading-active');</script>",
             unsafe_allow_html=True,
         )
         inputs = [line.strip() for line in bulk_query.splitlines() if line.strip()]
         all_feats = []
         all_regions = []
         for user_input in inputs:
             if "/" in user_input:
                 region = "NSW"
                 parts = user_input.split("/")
                 if len(parts) == 3:
