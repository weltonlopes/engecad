import math

import pytest

from engecad.core.geometry import (
    BBox,
    Vec2,
    azimuth,
    closest_point_on_segment,
    distance_to_segment,
    format_dms,
    line_intersection,
    polygon_area,
    polyline_length,
)


def test_vec_algebra():
    a, b = Vec2(3, 4), Vec2(1, 2)
    assert (a + b) == Vec2(4, 6)
    assert (a - b) == Vec2(2, 2)
    assert a.length == pytest.approx(5.0)
    assert a.dot(b) == pytest.approx(11.0)
    assert a.normalized().length == pytest.approx(1.0)


def test_vec_of_accepts_tuple_and_vec():
    assert Vec2.of((1, 2)) == Vec2(1, 2)
    assert Vec2.of(Vec2(1, 2)) == Vec2(1, 2)


def test_rotated_around_origin():
    p = Vec2(10, 0).rotated(math.pi / 2)
    assert p.x == pytest.approx(0, abs=1e-9)
    assert p.y == pytest.approx(10)


def test_polar():
    p = Vec2.polar(Vec2(100, 100), math.radians(90), 50)
    assert p.x == pytest.approx(100)
    assert p.y == pytest.approx(150)


def test_line_intersection_crossing():
    ip = line_intersection(Vec2(0, 0), Vec2(10, 10), Vec2(0, 10), Vec2(10, 0))
    assert ip == Vec2(5, 5)


def test_line_intersection_parallel_is_none():
    assert line_intersection(Vec2(0, 0), Vec2(10, 0), Vec2(0, 5), Vec2(10, 5)) is None


def test_line_intersection_outside_segment_is_none():
    assert line_intersection(Vec2(0, 0), Vec2(1, 0), Vec2(5, -5), Vec2(5, 5)) is None
    # como retas infinitas, existe
    assert line_intersection(
        Vec2(0, 0), Vec2(1, 0), Vec2(5, -5), Vec2(5, 5), as_segments=False
    ) == Vec2(5, 0)


def test_closest_point_clamps_to_segment():
    assert closest_point_on_segment(Vec2(-10, 5), Vec2(0, 0), Vec2(10, 0)) == Vec2(0, 0)
    assert closest_point_on_segment(Vec2(5, 5), Vec2(0, 0), Vec2(10, 0)) == Vec2(5, 0)


def test_distance_to_long_segment_uses_perpendicular():
    # o bug que derrubava o snap de intersecao: extremos longe, segmento perto
    d = distance_to_segment(Vec2(50, 0.3), Vec2(0, 0), Vec2(100, 0))
    assert d == pytest.approx(0.3)


def test_polygon_area_square():
    sq = [Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)]
    assert polygon_area(sq) == pytest.approx(100.0)
    # ordem invertida nao muda a area absoluta
    assert polygon_area(list(reversed(sq))) == pytest.approx(100.0)


def test_polyline_length_open_and_closed():
    pts = [Vec2(0, 0), Vec2(10, 0), Vec2(10, 10)]
    assert polyline_length(pts) == pytest.approx(20.0)
    assert polyline_length(pts, closed=True) == pytest.approx(20.0 + math.hypot(10, 10))


@pytest.mark.parametrize(
    "target,expected",
    [(Vec2(0, 10), 0.0), (Vec2(10, 0), 90.0), (Vec2(0, -10), 180.0), (Vec2(-10, 0), 270.0)],
)
def test_azimuth_is_clockwise_from_north(target, expected):
    assert azimuth(Vec2(0, 0), target) == pytest.approx(expected)


def test_format_dms():
    assert format_dms(45.5) == "45°30'00.0\""
    assert format_dms(0.0) == "0°00'00.0\""


def test_format_dms_rounds_seconds_without_overflow():
    # 59.99s nao pode virar "60.0" segundos
    out = format_dms(1 + 59.9999 / 3600)
    assert "60.0" not in out


def test_bbox_union_and_intersect():
    a = BBox(0, 0, 10, 10)
    b = BBox(5, 5, 20, 20)
    assert a.intersects(b)
    u = a.union(b)
    assert (u.minx, u.miny, u.maxx, u.maxy) == (0, 0, 20, 20)
    assert not a.intersects(BBox(50, 50, 60, 60))


def test_empty_bbox():
    e = BBox()
    assert e.is_empty
    assert e.union(BBox(0, 0, 1, 1)) == BBox(0, 0, 1, 1)
