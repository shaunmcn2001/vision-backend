import io
import os
import zipfile


def _hex_to_kml_color(hex_color: str, opacity: float) -> str:
    """Convert a hex color and opacity to a KML color string."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        hex_color = "FFFFFF"
    r = hex_color[0:2]
    g = hex_color[2:4]
    b = hex_color[4:6]
    alpha = int(opacity * 255)
    return f"{alpha:02x}{b}{g}{r}"


def generate_kml(
    features: list,
    region: str,
    fill_hex: str,
    fill_opacity: float,
    outline_hex: str,
    outline_weight: int,
    folder_name: str,
) -> str:
    """Generate a KML string for the provided features."""
    fill_kml_color = _hex_to_kml_color(fill_hex, fill_opacity)
    outline_kml_color = _hex_to_kml_color(outline_hex, 1.0)
    kml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        f'<Document><name>{folder_name}</name>',
        f'<Folder><name>{folder_name}</name>'
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
            placename = f"Lot {lot} {'Section ' + sec + ' ' if sec else ''}{planlabel}"

        extended_data = "<ExtendedData>"
        extended_data += (
            f"<Data name=\"qldglobe_place_name\"><value>{lot}"
            f"{plan if region == 'QLD' else planlabel}</value></Data>"
        )
        extended_data += "<Data name=\"_labelid\"><value>places-label-1752714297825-1</value></Data>"
        extended_data += "<Data name=\"_measureLabelsIds\"><value></value></Data>"
        extended_data += f"<Data name=\"Lot\"><value>{lot}</value></Data>"
        extended_data += f"<Data name=\"Plan\"><value>{plan if region == 'QLD' else planlabel}</value></Data>"
        extended_data += f"<Data name=\"Lot/plan\"><value>{lot}{plan if region == 'QLD' else planlabel}</value></Data>"
        extended_data += "<Data name=\"Lot area (m²)\"><value>5908410</value></Data>"
        extended_data += "<Data name=\"Excluded area (m²)\"><value>0</value></Data>"
        extended_data += "<Data name=\"Lot volume\"><value>0</value></Data>"
        extended_data += "<Data name=\"Surveyed\"><value>Y</value></Data>"
        extended_data += "<Data name=\"Tenure\"><value>Freehold</value></Data>"
        extended_data += "<Data name=\"Parcel type\"><value>Lot Type Parcel</value></Data>"
        extended_data += "<Data name=\"Coverage type\"><value>Base</value></Data>"
        extended_data += "<Data name=\"Accuracy\"><value>UPGRADE ADJUSTMENT - 5M</value></Data>"
        extended_data += "<Data name=\"st_area(shape)\"><value>0.0005218316994782257</value></Data>"
        extended_data += "<Data name=\"st_perimeter(shape)\"><value>0.0913818171562543</value></Data>"
        extended_data += "<Data name=\"coordinate-systems\"><value>GDA2020 lat/lng</value></Data>"
        current_date_time = "05:14 PM AEST on Thursday, July 17, 2025"
        extended_data += f"<Data name=\"Generated On\"><value>{current_date_time}</value></Data>"
        extended_data += "</ExtendedData>"

        kml_lines.append(f"<Placemark><name>{placename}</name>")
        kml_lines.append(extended_data)
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
    kml_lines.append("</Folder>")
    kml_lines.append("</Document></kml>")
    return "\n".join(kml_lines)


def generate_shapefile(features: list, region: str) -> bytes:
    """Generate a zipped shapefile for the provided features."""
    import shapefile
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
    prj_text = (
        'GEOGCS["WGS 84",DATUM["WGS_1984",'
        'SPHEROID["WGS 84",6378137,298.257223563],'
        'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0],'
        'UNIT["degree",0.0174532925199433],AUTHORITY["EPSG","4326"]]'
    )
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
    """Calculate bounds for the provided features."""
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
                    if y < min_lat:
                        min_lat = y
                    if y > max_lat:
                        max_lat = y
                    if x < min_lon:
                        min_lon = x
                    if x > max_lon:
                        max_lon = x
    if min_lat > max_lat or min_lon > max_lon:
        return [[-39, 137], [-9, 155]]
    return [[min_lat, min_lon], [max_lat, max_lon]]

__all__ = [
    "_hex_to_kml_color",
    "generate_kml",
    "generate_shapefile",
    "get_bounds",
]
