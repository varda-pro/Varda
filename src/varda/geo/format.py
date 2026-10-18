"""A position as text that names its body and conventions, for readouts."""

from __future__ import annotations

from varda.geo.describe import CrsDescription


def formatLonLat(lon: float, lat: float, description: CrsDescription) -> str:
    """``lat 18.40312° N  lon 77.45120° E  (Mars, planetocentric)``: the
    angles as the CRS gives them, then the body and latitude type. Longitudes
    of a 0..360 CRS show in [0, 360) with " (0–360)"; the longitude is
    signed, followed by the CRS's direction (E, or W when it counts west)."""
    if description.longitudeDomain == "0..360":
        lon %= 360.0
    hemisphere = "N" if lat >= 0 else "S"
    direction = "W" if description.longitudeDirection == "west" else "E"
    domain = " (0–360)" if description.longitudeDomain == "0..360" else ""
    body = description.body or "unknown body"
    naming = ", ".join(filter(None, (body, description.latitudeType)))
    return (
        f"lat {abs(lat):.5f}° {hemisphere}  "
        f"lon {lon + 0.0:.5f}° {direction}{domain}  ({naming})"
    )


def formatMapXY(x: float, y: float, description: CrsDescription) -> str:
    """``xy 1234.50, -987.00 m``: map coordinates in the CRS's units."""
    return f"xy {x:.2f}, {y:.2f} {description.units}"
