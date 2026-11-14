"""Split a big origins x destinations job into the fewest requests that respect provider limits."""
from dataclasses import dataclass
from math import ceil

from .pricing_calc import load_pricing


@dataclass
class SplitPlan:
    provider: str
    block_origins: int
    block_destinations: int
    requests: int
    elements: int


def _caps(provider: str) -> tuple[int, int, int]:
    """(max_origins, max_destinations, max_elements) per request."""
    lim = load_pricing()["limits"]
    if provider == "Google Maps Platform":
        g = lim["google.distance_matrix"]
        return g["max_origins"], g["max_destinations"], g["max_elements"]
    if provider == "Ola Maps":
        n = lim["ola.distance_matrix"]["max_pairs"]
        return n, n, n
    if provider == "Mapbox":
        m = lim["mapbox.matrix"]
        return m["max_coordinates"], m["max_coordinates"], m["max_elements"]
    raise ValueError(f"no verified matrix limits for {provider}")


def split_matrix(provider: str, origins: int, destinations: int) -> SplitPlan:
    mo, md, me = _caps(provider)
    best = None
    for a in range(1, min(mo, origins) + 1):
        b = min(md, destinations, me // a)
        if b < 1:
            continue
        n = ceil(origins / a) * ceil(destinations / b)
        if best is None or n < best[0]:
            best = (n, a, b)
    n, a, b = best
    return SplitPlan(provider, a, b, n, origins * destinations)
