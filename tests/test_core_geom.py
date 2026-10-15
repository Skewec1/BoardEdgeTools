"""纯几何单元测试：不需要 KiCad，用系统 Python 直接跑。

    python tests/test_core_geom.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import (  # noqa: E402
    IU_PER_MM, _close_ring, _dist, _point_in_rings, _polyline_midpoint,
    _seg_intersection, is_closed_ring, simplify, split_polyline,
)

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print("  PASS  {}".format(name))
    else:
        print("  FAIL  {}  {}".format(name, extra))
        FAILS.append(name)


def p(x_mm, y_mm):
    """单个小数坐标点 → IU 整数元组。"""
    return (int(round(x_mm * IU_PER_MM)), int(round(y_mm * IU_PER_MM)))


def mm(*pts):
    """(0,0) 之类的小数坐标 → IU 整数元组列表。"""
    return [p(x, y) for x, y in pts]


# ---------------------------------------------------------------------------
# 线段求交
# ---------------------------------------------------------------------------
def test_seg_intersection():
    print("[线段求交]")
    a1, a2 = (0, 0), (10 * IU_PER_MM, 0)
    b1, b2 = (5 * IU_PER_MM, -5 * IU_PER_MM), (5 * IU_PER_MM, 5 * IU_PER_MM)
    hit = _seg_intersection(a1, a2, b1, b2)
    check("十字相交", hit is not None and abs(hit[0] - 0.5) < 1e-9, hit)

    b3, b4 = (5 * IU_PER_MM, 5 * IU_PER_MM), (5 * IU_PER_MM, 9 * IU_PER_MM)
    check("延长线相交不算", _seg_intersection(a1, a2, b3, b4) is None)

    c1, c2 = (0, 0), (10 * IU_PER_MM, 0)
    check("平行不算", _seg_intersection(a1, a2, c1, c2) is None)

    # 端点接触：t≈1
    d1, d2 = (10 * IU_PER_MM, -1), (10 * IU_PER_MM, 1)
    hit = _seg_intersection(a1, a2, d1, d2)
    check("端点接触", hit is not None and abs(hit[0] - 1.0) < 1e-9, hit)


# ---------------------------------------------------------------------------
# 切断
# ---------------------------------------------------------------------------
def test_split():
    print("[切断]")
    line = mm((0, 0), (10, 0))
    blade = [(p(5, -5), p(5, 5))]

    pieces = split_polyline(line, blade)
    check("一刀两段", len(pieces) == 2, pieces)
    if len(pieces) == 2:
        check("第一段长度 5mm",
              abs((pieces[0][-1][0] - pieces[0][0][0]) / IU_PER_MM - 5.0) < 1e-6)
        check("第二段长度 5mm",
              abs((pieces[1][-1][0] - pieces[1][0][0]) / IU_PER_MM - 5.0) < 1e-6)
        check("断点重合", pieces[0][-1] == pieces[1][0], pieces)

    # 两刀三段
    blade2 = [(p(3, -5), p(3, 5)), (p(7, -5), p(7, 5))]
    pieces = split_polyline(line, blade2)
    check("两刀三段", len(pieces) == 3, len(pieces))

    # 无交点
    blade3 = [(p(20, -5), p(20, 5))]
    pieces = split_polyline(line, blade3)
    check("无交点原样返回", len(pieces) == 1, len(pieces))


# ---------------------------------------------------------------------------
# 闭合环
# ---------------------------------------------------------------------------
def test_ring():
    print("[闭合环]")
    ring = _close_ring(mm((0, 0), (10, 0), (10, 10), (0, 10)))
    check("补回首点", len(ring) == 5 and ring[0] == ring[-1], len(ring))
    check("判定为闭合", is_closed_ring(ring))
    check("开放折线不误判", not is_closed_ring(mm((0, 0), (10, 0), (10, 10))))

    # 闭合环的末段（末点→首点）也要能被切断：横刀 y=5 切在 x=0 的闭合边上
    segs = [(p(-5, 5), p(5, 5))]
    pieces = split_polyline(ring, segs)
    check("闭合边能被切断", len(pieces) == 2, len(pieces))

    # 整圈总长守恒：切完所有段长之和 = 原周长 40mm
    total = sum(sum(_dist(a, b) for a, b in zip(s[:-1], s[1:]))
                for s in pieces) / IU_PER_MM
    check("切断后总长守恒 40mm", abs(total - 40.0) < 1e-6, total)


# ---------------------------------------------------------------------------
# 内外判定
# ---------------------------------------------------------------------------
def test_inside():
    print("[内外判定]")
    square = mm((0, 0), (10, 0), (10, 10), (0, 10))
    check("中心在内", _point_in_rings(p(5, 5), [square]))
    check("外侧在外", not _point_in_rings(p(15, 5), [square]))

    # 带挖槽：外轮廓 + 反向挖槽一起算，挖槽里的点应判为"外"
    hole = list(reversed(mm((4, 4), (4, 6), (6, 6), (6, 4))))
    check("挖槽内判为外", not _point_in_rings(p(5, 5), [square, hole]))
    check("挖槽外仍为内", _point_in_rings(p(2, 2), [square, hole]))


# ---------------------------------------------------------------------------
# 弧长中点
# ---------------------------------------------------------------------------
def test_midpoint():
    print("[弧长中点]")
    pts = mm((0, 0), (10, 0), (10, 10))
    mid = _polyline_midpoint(pts)
    # 总长 20mm，一半 10mm → 正好落在拐角 (10,0)
    check("落在拐角", abs(mid[0] / IU_PER_MM - 10.0) < 1e-6
          and abs(mid[1] / IU_PER_MM - 0.0) < 1e-6, mid)


# ---------------------------------------------------------------------------
# 简化
# ---------------------------------------------------------------------------
def test_simplify():
    print("[共线合并]")
    pts = mm((0, 0), (1, 0), (2, 0), (3, 0), (4, 0))
    out = simplify(pts, 0.02)
    check("5点合并为2点", len(out) == 2, out)
    check("端点保留", out[0] == pts[0] and out[-1] == pts[-1], out)


def main():
    test_seg_intersection()
    test_split()
    test_ring()
    test_inside()
    test_midpoint()
    test_simplify()
    print("")
    if FAILS:
        print("{} 项失败: {}".format(len(FAILS), ", ".join(FAILS)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
