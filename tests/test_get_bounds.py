import kml_utils as kml


def test_get_bounds_polygon(qld_feature):
    expected = [[-28.1, 150.0], [-28.0, 150.1]]
    bounds = kml.get_bounds([qld_feature])
    assert bounds == expected


def test_get_bounds_multipolygon(multipolygon_feature):
    expected = [[-33.9, 150.2], [-33.7, 150.5]]
    bounds = kml.get_bounds([multipolygon_feature])
    assert bounds == expected
