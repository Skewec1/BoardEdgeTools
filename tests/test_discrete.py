"""「输出为离散图线」功能测试（需 KiCad 自带 python）。

    D:/Application/KiCAD/bin/python.exe tests/test_discrete.py

对应飞书《KiCad 进阶操作》27.2：插件生成的闭合轮廓没法逐段删减，
要先「从选区创建图线」转成离散线段。勾选离散图线后一步到位。
"""

import os
import sys

os.environ.setdefault("BOARD_EDGE_TOOLS_CLI_MODE", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG_ROOT = os.path.dirname(ROOT)
sys.path.insert(0, PKG_ROOT)

import pcbnew  # noqa: E402
from BoardEdgeTools import core  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print("  PASS  {}".format(name))
    else:
        print("  FAIL  {}  {}".format(name, extra))
        FAILS.append(name)


def make_source(board, n=4):
    """4 条线段拼成 50x30 闭合矩形（模拟用户框选的来源图形）。"""
    sel = []
    for x0, y0, x1, y1 in ((0, 0, 50, 0), (50, 0, 50, 30),
                           (50, 30, 0, 30), (0, 30, 0, 0)):
        it = pcbnew.PCB_SHAPE(board)
        it.SetShape(pcbnew.S_SEGMENT)
        it.SetStart(pcbnew.VECTOR2I(core.mm2iu(x0), core.mm2iu(y0)))
        it.SetEnd(pcbnew.VECTOR2I(core.mm2iu(x1), core.mm2iu(y1)))
        it.SetWidth(core.mm2iu(0.05))
        it.SetLayer(pcbnew.Edge_Cuts)
        board.Add(it)
        sel.append(it)
    return sel


def seg_length_mm(item):
    a, b = item.GetStart(), item.GetEnd()
    return core.iu2mm(int(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5))


def poly_perimeter_mm(item):
    """闭合多边形的周长（首尾相连）。"""
    pts = [(p[0], p[1]) for p in item.GetPolyShape().Outline(0).CPoints()]
    total = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        total += ((x0 - x1) ** 2 + (y0 - y1) ** 2) ** 0.5
    return core.iu2mm(int(total))


def main():
    print("[描画轮廓：多边形 vs 离散图线]")
    board = pcbnew.BOARD()
    sel = make_source(board)
    n_source = len(list(board.GetDrawings()))

    items_poly = core.generate_outline(
        board, [pcbnew.User_1], -1.0, core.CORNER_STRATEGIES[0][1],
        line_width_mm=0.05, source="selection", selection=sel)
    check("默认输出 1 条闭合多边形",
          len(items_poly) == 1
          and items_poly[0].GetShape() == pcbnew.S_POLYGON,
          [(type(i).__name__, i.GetShape()) for i in items_poly])

    items_disc = core.generate_outline(
        board, [pcbnew.User_2], -1.0, core.CORNER_STRATEGIES[0][1],
        line_width_mm=0.05, source="selection", selection=sel, discrete=True)
    shapes = [i.GetShape() for i in items_disc]
    check("离散输出全是独立线段",
          len(items_disc) > 1 and all(s == pcbnew.S_SEGMENT for s in shapes),
          shapes[:8])
    check("全部落在指定层 User.2",
          all(i.GetLayer() == pcbnew.User_2 for i in items_disc))

    total = sum(seg_length_mm(i) for i in items_disc)
    # 与多边形输出自洽：离散线段总长 = 闭合多边形周长
    expect = poly_perimeter_mm(items_poly[0])
    check("离散线段总长 = 多边形周长({:.0f}mm)".format(expect),
          abs(total - expect) < 0.05,
          "{:.3f} mm vs {:.3f}".format(total, expect))

    print("[源对象保留]")
    check("源图形没有被删除",
          len(list(board.GetDrawings())) == n_source + len(items_poly)
          + len(items_disc),
          len(list(board.GetDrawings())))

    print("[露铜带：离散 vs 多边形]")
    board2 = pcbnew.BOARD()
    sel2 = make_source(board2)
    band_poly = core.generate_band(board2, [pcbnew.F_Mask], band_mm=1.0,
                                   source="selection", selection=sel2)
    check("无断开区默认 1 条多边形",
          len(band_poly) == 1
          and band_poly[0].GetShape() == pcbnew.S_POLYGON,
          len(band_poly))
    band_disc = core.generate_band(board2, [pcbnew.F_Mask], band_mm=1.0,
                                   source="selection", selection=sel2,
                                   discrete=True)
    check("勾选离散后输出非空独立线段",
          len(band_disc) >= 4
          and all(i.GetShape() == pcbnew.S_SEGMENT for i in band_disc),
          len(band_disc))
    total_band = sum(seg_length_mm(i) for i in band_disc)
    # 与多边形输出自洽（带宽 1mm、贴边 → 中心线内缩 0.5mm）
    expect_band = poly_perimeter_mm(band_poly[0])
    check("露铜带离散线段总长 = 多边形周长({:.0f}mm)".format(expect_band),
          abs(total_band - expect_band) < 0.2,
          "{:.3f} mm vs {:.3f}".format(total_band, expect_band))

    print("[带断开区 + 离散：不冲突]")
    band_gap = core.generate_band(board2, [pcbnew.F_Mask], band_mm=1.0,
                                  gap_boxes_mm=[(20, -100, 30, 100)],
                                  source="selection", selection=sel2,
                                  discrete=True)
    check("分段露铜带照常工作",
          len(band_gap) >= 6
          and all(i.GetShape() == pcbnew.S_SEGMENT for i in band_gap),
          len(band_gap))

    print("")
    if FAILS:
        print("{} 项失败: {}".format(len(FAILS), ", ".join(FAILS)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
