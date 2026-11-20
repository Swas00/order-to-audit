"""Distance between two GPS points (haversine, metres)."""

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000
FAR_METERS = 300  # a check-in further than this from the shop's recorded location is flagged


def distance_m(lat1, lon1, lat2, lon2) -> int:
    lat1, lon1, lat2, lon2 = (radians(float(x)) for x in (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return round(2 * EARTH_RADIUS_M * asin(sqrt(a)))
