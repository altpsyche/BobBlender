"""Tests for the pure-Python solar model (blender/extensions/bob_blender_tools/core/solar.py).

Run: uv run --with pytest --project tools pytest tools/tests -q

solar.py lives on the Blender side but imports only the standard library, so it
is loaded here by path and checked against physical invariants: the sun sits
where geography says it should. No third-party golden is needed, the geometry is
the reference (subsolar latitude at solstice, south at noon, below the horizon at
night).
"""

import importlib.util
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).parents[2]
_spec = importlib.util.spec_from_file_location(
    "core_solar",
    REPO_ROOT / "blender" / "extensions" / "bob_blender_tools" / "core" / "solar.py",
)
solar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(solar)


def test_equator_equinox_noon_overhead():
    # Near the March equinox the subsolar point is on the equator, so at solar
    # noon on the Greenwich meridian the sun is nearly overhead.
    r = solar.sun_position(0.0, 0.0, 2026, 3, 20, 12.0, utc_offset=0.0)
    assert r["elevation"] > 85.0


def test_summer_solstice_noon_elevation():
    # Lat 45 N at the June solstice: elevation ~ 90 - 45 + 23.44 = 68.4.
    r = solar.sun_position(45.0, 0.0, 2026, 6, 21, 12.0, utc_offset=0.0)
    assert 65.0 < r["elevation"] < 71.0
    assert abs(r["azimuth"] - 180.0) < 6.0  # due south at noon in the north


def test_winter_solstice_noon_elevation():
    # Lat 45 N at the December solstice: elevation ~ 90 - 45 - 23.44 = 21.6.
    r = solar.sun_position(45.0, 0.0, 2026, 12, 21, 12.0, utc_offset=0.0)
    assert 18.0 < r["elevation"] < 25.0
    assert abs(r["azimuth"] - 180.0) < 6.0


def test_declination_tracks_season():
    summer = solar.sun_position(0.0, 0.0, 2026, 6, 21, 12.0)["declination"]
    winter = solar.sun_position(0.0, 0.0, 2026, 12, 21, 12.0)["declination"]
    assert 23.0 < summer < 23.9   # tropic of cancer
    assert -23.9 < winter < -23.0  # tropic of capricorn


def test_night_is_below_horizon():
    r = solar.sun_position(45.0, 0.0, 2026, 6, 21, 0.0, utc_offset=0.0)
    assert r["elevation"] < 0.0


def test_noon_higher_than_night():
    noon = solar.sun_position(45.0, 0.0, 2026, 6, 21, 12.0)["elevation"]
    night = solar.sun_position(45.0, 0.0, 2026, 6, 21, 0.0)["elevation"]
    assert noon > night


def test_azimuth_in_range():
    for h in range(0, 24):
        r = solar.sun_position(45.0, 0.0, 2026, 6, 21, float(h))
        assert 0.0 <= r["azimuth"] <= 360.0


def test_morning_east_evening_west():
    # An hour after sunrise the sun is in the east; an hour before sunset, west.
    morning = solar.sun_position(45.0, 0.0, 2026, 6, 21, 7.0)
    evening = solar.sun_position(45.0, 0.0, 2026, 6, 21, 18.0)
    assert 45.0 < morning["azimuth"] < 135.0    # eastern half
    assert 225.0 < evening["azimuth"] < 315.0   # western half


def test_longitude_shifts_solar_noon():
    # 15 degrees of longitude is an hour of solar time. West of the timezone
    # meridian, solar noon falls later, so peak elevation shifts with longitude.
    east = solar.sun_position(45.0, 15.0, 2026, 6, 21, 12.0, utc_offset=0.0)
    west = solar.sun_position(45.0, -15.0, 2026, 6, 21, 12.0, utc_offset=0.0)
    # At the same clock time, the eastern site is already past noon (sun west),
    # the western site is before noon (sun east).
    assert east["azimuth"] > 180.0
    assert west["azimuth"] < 180.0
