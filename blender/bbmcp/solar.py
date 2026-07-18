"""Solar position from geographic time and place. Pure standard library, no bpy.

The NOAA solar-position equations (the same math Blender's Sun Position add-on
uses), so a Time of Day plus latitude, longitude, and date give the sun's
elevation and azimuth. Kept dependency-free and side-effect-free so it is
unit-testable on any Python and travels cleanly to a BobBlenderFirmament split.

Conventions: latitude north positive, longitude east positive, utc_offset east
positive (hours). Elevation is degrees above the horizon; azimuth is degrees
clockwise from true north (0 north, 90 east, 180 south, 270 west).
"""

import math


def _julian_day(year, month, day, ut_hours):
    """Julian Day for a calendar date at a given universal time (fractional)."""
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day + b - 1524.5 + ut_hours / 24.0
    )


def _refraction(elevation_deg):
    """Approximate atmospheric refraction (degrees) lifting the apparent sun.

    Bennett's formula. Small near the zenith, largest at the horizon where it is
    about half a degree. Below the horizon it is left off.
    """
    if elevation_deg > 85.0:
        return 0.0
    if elevation_deg <= -1.0:
        return 0.0
    e = math.radians(elevation_deg)
    if elevation_deg > 5.0:
        r = 58.1 / math.tan(e) - 0.07 / math.tan(e) ** 3 + 0.000086 / math.tan(e) ** 5
    elif elevation_deg > -0.575:
        r = 1735.0 + elevation_deg * (
            -518.2 + elevation_deg * (103.4 + elevation_deg * (-12.79 + elevation_deg * 0.711))
        )
    else:
        r = -20.774 / math.tan(e)
    return r / 3600.0  # arc-seconds to degrees


def sun_position(latitude, longitude, year, month, day, hour, utc_offset=0.0,
                 refraction=True):
    """Sun elevation and azimuth (degrees) for a place, date, and local time.

    hour is local clock time in decimal hours (13.5 is 13:30). utc_offset is the
    place's offset from UTC in hours, east positive. Returns a dict with
    elevation, azimuth, declination, and equation_of_time (minutes), the last two
    exposed for testing and for a day-length readout later.
    """
    ut = hour - utc_offset
    jd = _julian_day(year, month, day, ut)
    t = (jd - 2451545.0) / 36525.0  # Julian centuries since J2000.0

    # Sun's geometric mean longitude and anomaly, its orbit eccentricity.
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    m_rad = math.radians(m)

    # Equation of center to the true longitude, then the apparent longitude.
    c = (
        math.sin(m_rad) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * m_rad) * (0.019993 - 0.000101 * t)
        + math.sin(3 * m_rad) * 0.000289
    )
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))

    # Obliquity of the ecliptic, corrected, then the declination.
    eps0 = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    eps = eps0 + 0.00256 * math.cos(math.radians(omega))
    eps_rad = math.radians(eps)
    app_long_rad = math.radians(app_long)
    declination = math.degrees(math.asin(math.sin(eps_rad) * math.sin(app_long_rad)))

    # Equation of time (minutes): the sun's offset from clock mean time.
    y = math.tan(eps_rad / 2.0) ** 2
    l0_rad = math.radians(l0)
    eot = 4.0 * math.degrees(
        y * math.sin(2 * l0_rad)
        - 2 * e * math.sin(m_rad)
        + 4 * e * y * math.sin(m_rad) * math.cos(2 * l0_rad)
        - 0.5 * y * y * math.sin(4 * l0_rad)
        - 1.25 * e * e * math.sin(2 * m_rad)
    )

    # True solar time to the hour angle.
    tst = (hour * 60.0 + eot + 4.0 * longitude - 60.0 * utc_offset) % 1440.0
    ha = tst / 4.0 - 180.0 if tst / 4.0 >= 0 else tst / 4.0 + 180.0

    lat_rad = math.radians(latitude)
    decl_rad = math.radians(declination)
    ha_rad = math.radians(ha)

    cos_zenith = (
        math.sin(lat_rad) * math.sin(decl_rad)
        + math.cos(lat_rad) * math.cos(decl_rad) * math.cos(ha_rad)
    )
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith = math.degrees(math.acos(cos_zenith))
    elevation = 90.0 - zenith

    # Azimuth clockwise from north.
    sin_zenith = math.sin(math.radians(zenith))
    if abs(sin_zenith) < 1e-9:
        azimuth = 180.0  # sun at the zenith, azimuth undefined; pick south
    else:
        cos_az = (math.sin(lat_rad) * math.cos(math.radians(zenith)) - math.sin(decl_rad)) / (
            math.cos(lat_rad) * sin_zenith
        )
        cos_az = max(-1.0, min(1.0, cos_az))
        az_core = math.degrees(math.acos(cos_az))
        azimuth = (az_core + 180.0) % 360.0 if ha > 0 else (540.0 - az_core) % 360.0

    if refraction:
        elevation += _refraction(elevation)

    return {
        "elevation": elevation,
        "azimuth": azimuth,
        "declination": declination,
        "equation_of_time": eot,
    }
