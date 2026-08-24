import pytest

from engecad.core.coordinput import format_coordinate, parse_angle, parse_coordinate
from engecad.core.geometry import Vec2

LAST = Vec2(500000.0, 7400000.0)


def test_absolute_cartesian():
    p = parse_coordinate("500123.45,7412987.12")
    assert p.x == pytest.approx(500123.45)
    assert p.y == pytest.approx(7412987.12)


def test_semicolon_separator():
    assert parse_coordinate("10;20") == Vec2(10, 20)


def test_relative_cartesian():
    p = parse_coordinate("@10,0", LAST)
    assert p.x == pytest.approx(LAST.x + 10)
    assert p.y == pytest.approx(LAST.y)


def test_relative_negative():
    p = parse_coordinate("@-5,-5", LAST)
    assert p.x == pytest.approx(LAST.x - 5)
    assert p.y == pytest.approx(LAST.y - 5)


def test_polar_cad_angle_is_ccw_from_east():
    p = parse_coordinate("@10<90", LAST)
    assert p.x == pytest.approx(LAST.x, abs=1e-9)
    assert p.y == pytest.approx(LAST.y + 10)


def test_polar_cad_zero_is_east():
    p = parse_coordinate("@10<0", LAST)
    assert p.x == pytest.approx(LAST.x + 10)
    assert p.y == pytest.approx(LAST.y, abs=1e-9)


def test_azimuth_zero_is_north():
    p = parse_coordinate("@10<<0", LAST)
    assert p.x == pytest.approx(LAST.x, abs=1e-9)
    assert p.y == pytest.approx(LAST.y + 10)


def test_azimuth_90_is_east():
    p = parse_coordinate("@10<<90", LAST)
    assert p.x == pytest.approx(LAST.x + 10)
    assert p.y == pytest.approx(LAST.y, abs=1e-9)


def test_azimuth_180_is_south():
    p = parse_coordinate("@10<<180", LAST)
    assert p.y == pytest.approx(LAST.y - 10)


def test_azimuth_accepts_dms():
    p = parse_coordinate("@100<<45d30'00\"", LAST)
    q = parse_coordinate("@100<<45.5", LAST)
    assert p.x == pytest.approx(q.x)
    assert p.y == pytest.approx(q.y)


def test_relative_without_last_treats_origin():
    assert parse_coordinate("@10,0") == Vec2(10, 0)


def test_garbage_is_rejected():
    assert parse_coordinate("lixo") is None
    assert parse_coordinate("") is None
    assert parse_coordinate("@10<<") is None
    assert parse_coordinate("10") is None


def test_parse_angle_forms():
    assert parse_angle("45") == pytest.approx(45)
    assert parse_angle("45.5") == pytest.approx(45.5)
    assert parse_angle("45d30'") == pytest.approx(45.5)
    assert parse_angle("45°30'36\"") == pytest.approx(45.51)
    assert parse_angle("-30") == pytest.approx(-30)


def test_format_coordinate():
    assert format_coordinate(Vec2(1.23456, 2.0), 3) == "1.235, 2.000"
