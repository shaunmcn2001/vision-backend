import io
import zipfile

import kml_utils as kml


def test_generate_shapefile_zip_contents(qld_feature):
    shp_bytes = kml.generate_shapefile([qld_feature], "QLD")
    with zipfile.ZipFile(io.BytesIO(shp_bytes)) as z:
        names = set(z.namelist())
    expected = {"parcels.shp", "parcels.shx", "parcels.dbf", "parcels.prj"}
    assert expected.issubset(names)
