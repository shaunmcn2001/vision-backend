import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

@pytest.fixture
def qld_feature():
    return {
        "type": "Feature",
        "properties": {"lot": "1", "plan": "RP12345"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[150.0, -28.0], [150.1, -28.0], [150.1, -28.1], [150.0, -28.1], [150.0, -28.0]]],
        },
    }


@pytest.fixture
def nsw_feature():
    return {
        "type": "Feature",
        "properties": {"lotnumber": "2", "sectionnumber": "1", "planlabel": "DP67890"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[150.2, -33.8], [150.3, -33.8], [150.3, -33.9], [150.2, -33.9], [150.2, -33.8]]],
        },
    }


@pytest.fixture
def multipolygon_feature():
    return {
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [
                [[[150.2, -33.8], [150.3, -33.8], [150.3, -33.9], [150.2, -33.9], [150.2, -33.8]]],
                [[[150.4, -33.7], [150.5, -33.7], [150.5, -33.8], [150.4, -33.8], [150.4, -33.7]]],
            ],
        },
    }
