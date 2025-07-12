#!/usr/bin/env python3
# LAWD Parcel Toolkit · 2025-07-12 (styled pop-up menu)

# — stdlib
import io, pathlib, requests, tempfile, zipfile, re, time
# — streamlit stack
import streamlit as st
from streamlit_option_menu import option_menu
from streamlit_folium import st_folium
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode
# — geo/data
import folium, simplekml, geopandas as gpd, pandas as pd
from shapely.geometry import shape, mapping, Polygon
from shapely.ops import unary_union, transform
from pyproj import Transformer, Geod

# ------------ static cfg ------------
CFG = pathlib.Path("layers.yaml")
try:
    import yaml
    cfg = yaml.safe_load(CFG.read_text()) if CFG.exists() else {}
except ImportError:
    cfg = {}
for k in ("basemaps", "overlays"): cfg.setdefault(k, [])

# ------------ ui shell --------------
st.set_page_config("Lot/Plan Toolkit", "📍", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("<div style='background:#ff6600;color:#fff;font-size:20px;"
            "font-weight:600;padding:6px 20px;border-radius:8px;"
            "margin-bottom:6px'>LAWD – Parcel Toolkit</div>", unsafe_allow_html=True)
with st.sidebar:
    tab = option_menu(None, ["Query","Layers","Downloads"],
                      icons=["search","layers","download"], default_index=0,
                      styles={"container":{"padding":"0","background":"#262730"},
                              "nav-link-selected":{"background":"#ff6600"}})

if cfg["basemaps"]:
    st.session_state.setdefault("basemap", cfg["basemaps"][0]["name"])
st.session_state.setdefault("ov_state",{o["name"]:False for o in cfg["overlays"]})

# ------------ services --------------
QLD=("https://spatial-gis.information.qld.gov.au/arcgis/rest/services/"
     "PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query")
NSW=("https://maps.six.nsw.gov.au/arcgis/rest/services/public/"
     "NSW_Cadastre/MapServer/9/query")
def fetch(ids):
    out, miss = {}, []
    for lp in ids:
        url,fld = (QLD,"lotplan") if re.match(r"^\d+[A-Z]{1,3}\d+$",lp,re.I) else (NSW,"lotidstring")
        try:
            js=requests.get(url,params={"where":f"{fld}='{lp}'","outFields":"*",
                                        "returnGeometry":"true","f":"geojson"},timeout=12).json()
            feats=js.get("features",[]);  wkid=4326
            if not feats: miss.append(lp); continue
            wkid=feats[0]["geometry"].get("spatialReference",{}).get("wkid",4326)
            tfm=Transformer.from_crs(wkid,4326,always_xy=True).transform if wkid!=4326 else None
            geoms,props=[],{}
            for f in feats:
                geoms.append(transform(tfm,shape(f["geometry"])) if tfm else shape(f["geometry"]))
                props=f["properties"]
            out[lp]={"geom":unary_union(geoms),"props":props}
        except Exception: miss.append(lp)
    return out, miss
def kml_colour(h,p): r,g,b=h[1:3],h[3:5],h[5:7]; return f"{int(round(255*p/100)):02x}{b}{g}{r}"
GEOD = Geod(ellps="WGS84")

# ╔═ QUERY ═════════════════════════════════════════════
if tab=="Query":
    ids_txt=st.sidebar.text_area("Lot/Plan IDs",height=110,
                                 placeholder="6RP702264\n5//DP123456")
    with st.sidebar.expander("Style & KML"):
        c1,c2=st.columns(2);  fx=c1.color_picker("Fill","#ff6600",label_visibility="collapsed")
        lx=c1.color_picker("Outline","#2e2e2e",label_visibility="collapsed")
        fo=c2.slider("Opacity %",0,100,70,label_visibility="collapsed")
        lw=c2.slider("Width px",0.5,6.0,1.2,0.1,label_visibility="collapsed")
        folder=st.text_input("KML folder","Parcels")
    if st.sidebar.button("🔍 Search",use_container_width=True) and ids_txt.strip():
        ids=[s.strip() for s in ids_txt.splitlines() if s.strip()]
        with st.spinner("Fetching parcels…"): recs,miss=fetch(ids)
        if miss: st.sidebar.warning("Not found: "+", ".join(miss))
        rows=[{"Lot/Plan":lp,"Lot Type":(p:=r["props"]).get("lottype") or p.get("PURPOSE") or "n/a",
               "Area (ha)":round(abs(GEOD.geometry_area_perimeter(r["geom"])[0])/1e4,2)}
              for lp,r in recs.items()]
        st.session_state.update(parcels=recs,table=pd.DataFrame(rows),
                                style=dict(fill=fx,op=fo,line=lx,w=lw,folder=folder))
        st.success(f"{len(recs)} parcel{'s'*(len(recs)!=1)} loaded.")

# ╔═ LAYERS (unchanged) ════════════════════════════════
if tab=="Layers":
    if cfg["basemaps"]:
        st.sidebar.subheader("Basemap")
        names=[b["name"] for b in cfg["basemaps"]]
        st.session_state["basemap"]=st.sidebar.radio("",names,index=names.index(st.session_state["basemap"]))
    st.sidebar.subheader("Static overlays")
    for o in cfg["overlays"]:
        st.session_state["ov_state"][o["name"]] = st.sidebar.checkbox(o["name"],
            value=st.session_state["ov_state"][o["name"]])

# ╔═ MAP BUILD ═════════════════════════════════════════
m=folium.Map(location=[-25,145],zoom_start=5,
             control_scale=True,width="100%",height="100vh")
if cfg["basemaps"]:
    base=next(b for b in cfg["basemaps"] if b["name"]==st.session_state["basemap"])
    folium.TileLayer(base["url"],name=base["name"],attr=base["attr"],
                     overlay=False,control=True,show=True).add_to(m)
for o in cfg["overlays"]:
    if st.session_state["ov_state"][o["name"]]:
        try:
            if o["type"]=="wms":
                folium.raster_layers.WmsTileLayer(o["url"],layers=str(o["layers"]),
                    transparent=True,fmt=o.get("fmt","image/png"),version="1.1.1",
                    name=o["name"],attr=o["attr"]).add_to(m)
            else:
                folium.TileLayer(o["url"],name=o["name"],attr=o["attr"]).add_to(m)
        except Exception as e: st.warning(f"{o['name']} failed: {e}")

sel_ids=set(st.session_state.get("_sel",[])); bounds=[]
if "parcels" in st.session_state:
    s=st.session_state["style"]
    fg=folium.FeatureGroup(name="Parcels",show=True).add_to(m)
    for lp,rec in st.session_state["parcels"].items():
        geom,prop=rec["geom"],rec["props"]
        sty=lambda f,lp=lp:{"fillColor":s["fill"],
                            "color":"red" if lp in sel_ids else s["line"],
                            "weight":s["w"],"fillOpacity":s["op"]/100}
        html=(f"<b>Lot/Plan:</b> {lp}<br>"
              f"<b>Lot Type:</b> {prop.get('lottype') or prop.get('PURPOSE') or 'n/a'}<br>"
              f"<b>Area:</b> {abs(GEOD.geometry_area_perimeter(geom)[0])/1e4:,.2f} ha")
        folium.GeoJson({"type":"Feature","properties":{"name":lp},"geometry":mapping(geom)},
                       name=lp,style_function=sty,tooltip=lp,popup=html).add_to(fg)
        bounds.append([[geom.bounds[1],geom.bounds[0]],[geom.bounds[3],geom.bounds[2]]])
if bounds:
    ys,xs,ye,xe=zip(*[(b[0][0],b[0][1],b[1][0],b[1][1]) for b in bounds])
    m.fit_bounds([[min(ys),min(xs)],[max(ye),max(xe)]])

folium_data=st_folium(m,height=550,use_container_width=True,
                      key="map",returned_objects=["bounds","js_events"])
js=folium_data.get("js_events",[])[-1] if folium_data.get("js_events") else None

# ╔═ TABLE & MENU ══════════════════════════════════════
if "table" in st.session_state and not st.session_state["table"].empty:
    st.subheader("Query Results")

    df=st.session_state["table"].copy(); df["⋮"]=""
    gdf=gpd.GeoDataFrame(df,geometry=[r["geom"]
             for r in st.session_state["parcels"].values()],crs=4326)

    MENU_JS = JsCode("""
class ActionCell {
  init(p){
    this.p=p;
    const btn=document.createElement('button');
    btn.innerHTML='&#8942;';   // ⋮
    btn.style='cursor:pointer;border:none;background:none;font-size:16px;font-weight:bold';
    const menu=document.createElement('div');
    menu.className='lawd-menu';
    menu.style='display:none;position:fixed;background:#fff;border-radius:8px;'
      +'box-shadow:0 4px 12px rgba(0,0,0,.15);min-width:260px;font-size:13px;'
      +'padding:8px 0;z-index:10000;border:1px solid #e0e0e0';
    const rows=[
      ['⭐','save','Add to Saved Results','Adds result(s) to saved list.'],
      ['🔔','pulse','Pulse','Create a temporary highlight.'],
      ['🔍','zoom','Zoom to Result(s)','Zoom to the result(s) on the map.'],
      ['🛟','buffer','Buffer Result(s)','Buffer 200 m around result(s).'],
      ['📄','csv','Export to CSV','Export result(s) to a CSV file.'],
      ['📊','xlsx','Export to XLSX','Export result(s) to an XLSX file.'],
      ['🗺️','shp','Export to Shapefile','Export result(s) to a Shapefile.'],
      ['🗑️','remove','Remove Result(s)','Remove result(s) from set.']
    ];
    rows.forEach(r=>{
        const d=document.createElement('div');
        d.dataset.act=r[1];
        d.style='display:flex;gap:12px;padding:8px 16px;cursor:pointer;'
              +'align-items:flex-start';
        d.innerHTML=`<span style="font-size:18px;width:24px;text-align:center">${r[0]}</span>`
          +`<div style="flex:1"><div style="font-weight:600">${r[2]}</div>`
          +`<div style="font-size:11px;color:#555">${r[3]}</div></div>`;
        d.onmouseenter=_=>d.style.background='#f5f5f5';
        d.onmouseleave=_=>d.style.background='';
        menu.appendChild(d);
    });
    document.body.appendChild(menu);
    btn.onclick=e=>{
        e.stopPropagation();
        const r=btn.getBoundingClientRect();
        menu.style.left=(r.left-200)+'px';
        menu.style.top=(r.bottom+6)+'px';
        menu.style.display='block';
    };
    document.addEventListener('click',()=>menu.style.display='none');
    menu.onclick=e=>{
        if(e.target.closest('[data-act]')){
            const act=e.target.closest('[data-act]').dataset.act;
            window.postMessage({type:act,row:this.p.data});
            menu.style.display='none';
        }
    };
    this.eGui=btn;
  }
  getGui(){return this.eGui;}
}
""")

    gob=GridOptionsBuilder.from_dataframe(gdf.drop(columns="geometry"))
    gob.configure_selection("multiple",use_checkbox=True)
    gob.configure_column("⋮",header_name="",width=60,
                         cellRenderer="ActionCell",suppressMenu=True)
    gob.configure_grid_options(frameworkComponents={"ActionCell":MENU_JS},
                               getContextMenuItems="()=>[]")

    grid=AgGrid(gdf.drop(columns="geometry"), gridOptions=gob.build(),
                update_mode=GridUpdateMode.MODEL_CHANGED,
                allow_unsafe_jscode=True, height=250)

    # ---- extract selection &/or row from JS ---------------------------
    raw_sel = grid.get("selected_rows") if isinstance(grid, dict) \
              else getattr(grid,"selected_rows",None)
    if raw_sel is None: sel_rows=[]
    elif isinstance(raw_sel, pd.DataFrame): sel_rows=raw_sel.to_dict("records")
    elif isinstance(raw_sel, list): sel_rows=raw_sel
    else:
        try: sel_rows=list(raw_sel)
        except TypeError: sel_rows=[]
    if js and "row" in js: sel_rows=[js["row"]]
    st.session_state["_sel"]=[r.get("Lot/Plan") or r.get("Lot_Plan") for r in sel_rows]

    # ---- Export-ALL bar ----------------------------------------------
    with st.expander("Export ALL", expanded=True):
        c1,c2=st.columns(2)
        if c1.button("Generate KML"):
            s=st.session_state["style"]; fk=kml_colour(s["fill"],s["op"]); lk=kml_colour(s["line"],100)
            fld=simplekml.Kml().newfolder(name=s["folder"])
            for lp,rec in st.session_state["parcels"].items():
                geom=rec["geom"]; polys=[geom] if isinstance(geom,Polygon) else list(geom.geoms)
                for i,p in enumerate(polys,1):
                    poly=fld.newpolygon(name=f"{lp} ({i})" if len(polys)>1 else lp,
                                        outerboundaryis=p.exterior.coords)
                    for ring in p.interiors: poly.innerboundaryis.append(ring.coords)
                    poly.style.polystyle.color=fk; poly.style.linestyle.color=lk; poly.style.linestyle.width=float(s["w"])
            st.download_button("Download KML", io.BytesIO(fld.kml().encode()),
                               "parcels.kml","application/vnd.google-earth.kml+xml")
        if c2.button("Generate SHP"):
            tmp=tempfile.mkdtemp()
            gpd.GeoDataFrame(st.session_state["table"],geometry=[r["geom"]
                for r in st.session_state["parcels"].values()],crs=4326).to_file(tmp+"/all.shp")
            z=pathlib.Path(tmp,"all.zip")
            with zipfile.ZipFile(z,"w",zipfile.ZIP_DEFLATED) as zf:
                for f in pathlib.Path(tmp).glob("all.*"): zf.write(f,f.name)
            st.download_button("Download SHP",open(z,"rb"),
                               "parcels.zip","application/zip")

    # ---- per-row actions --------------------------------------------
    if js and js.get("type") and "row" in js:
        act,row = js["type"], js["row"]; lp=row.get("Lot/Plan") or row.get("Lot_Plan")
        if not lp: st.stop()
        if act=="zoom":
            g=st.session_state["parcels"][lp]["geom"]
            st.session_state["__zoom"]=[[g.bounds[1],g.bounds[0]],[g.bounds[3],g.bounds[2]]]
            st.experimental_rerun()
        elif act=="buffer":
            st.toast("Buffer action coming soon 🙂")
        elif act=="pulse":
            st.toast("Pulse highlight coming soon 🙂")
        elif act in {"csv","xlsx","shp"}:
            df_sel=pd.DataFrame([row])
            if act=="csv":
                st.download_button("Download CSV",df_sel.to_csv(index=False).encode(),
                                   f"{lp}.csv","text/csv")
            elif act=="xlsx":
                bio=io.BytesIO(); df_sel.to_excel(bio,index=False); bio.seek(0)
                st.download_button("Download XLSX",bio.getvalue(),f"{lp}.xlsx",
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                tmp=tempfile.mkdtemp()
                gpd.GeoDataFrame(df_sel,geometry=[st.session_state["parcels"][lp]["geom"]],
                                 crs=4326).to_file(tmp+"/sel.shp")
                z=pathlib.Path(tmp,"sel.zip")
                with zipfile.ZipFile(z,"w",zipfile.ZIP_DEFLATED) as zf:
                    for f in pathlib.Path(tmp).glob("sel.*"): zf.write(f,f.name)
                st.download_button("Download SHP",open(z,"rb"),f"{lp}.zip","application/zip")
        elif act=="kml":
            s=st.session_state["style"]; fk=kml_colour(s["fill"],s["op"]); lk=kml_colour(s["line"],100)
            geom=st.session_state["parcels"][lp]["geom"]
            poly=simplekml.Kml().newpolygon(name=lp,
                   outerboundaryis=(geom.exterior.coords if isinstance(geom,Polygon)
                                    else list(geom.geoms)[0].exterior.coords))
            poly.style.polystyle.color=fk; poly.style.linestyle.color=lk; poly.style.linestyle.width=float(s["w"])
            st.download_button("Download KML", io.BytesIO(poly.kml().encode()),
                               f"{lp}.kml","application/vnd.google-earth.kml+xml")
        elif act=="remove":
            st.session_state["parcels"].pop(lp,None)
            st.session_state["table"]=st.session_state["table"][st.session_state["table"]["Lot/Plan"]!=lp]
            st.experimental_rerun()

# queued zoom
if "__zoom" in st.session_state:
    m.fit_bounds(st.session_state.pop("__zoom"))
