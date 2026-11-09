from lbs_rag.tools.request_splitter import split_matrix
from lbs_rag.tools.validators import validate_matrix, validate_pair, validate_waypoints


def test_swapped_latlng_for_india():
    assert validate_pair("77.59,12.97", "Google Maps Platform")      # lng,lat given to a lat,lng API
    assert validate_pair("12.97,77.59", "Google Maps Platform") == []


def test_mapbox_expects_lng_lat():
    assert validate_pair("77.59,12.97", "Mapbox") == []
    assert validate_pair("12.97,77.59", "Mapbox")


def test_out_of_range():
    assert validate_pair("95,10", "Google Maps Platform")
    assert validate_pair("abc", "Mapbox")


def test_google_matrix_limits():
    assert validate_matrix("Google Maps Platform", 10, 10) == []
    assert validate_matrix("Google Maps Platform", 26, 1)
    assert validate_matrix("Google Maps Platform", 20, 20)          # 400 elements > 100


def test_ola_matrix_pairs():
    assert validate_matrix("Ola Maps", 50, 1) == []
    assert validate_matrix("Ola Maps", 10, 10)                      # 100 pairs > 50


def test_waypoints():
    assert validate_waypoints("Ola Maps", "Directions Basic", 11)
    assert validate_waypoints("Ola Maps", "Directions", 25) == []
    assert validate_waypoints("Google Maps Platform", "Directions", 26)


def test_splitter():
    assert split_matrix("Ola Maps", 50, 1).requests == 1
    p = split_matrix("Google Maps Platform", 100, 100)
    assert p.requests == 100 and p.block_origins * p.block_destinations <= 100   # 10,000 elements / 100
    assert split_matrix("Ola Maps", 100, 100).requests == 200                    # 10,000 pairs / 50
