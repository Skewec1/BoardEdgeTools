"""KiCad 环境下的端到端测试：用 KiCad 自带 python 跑。

    D:/Application/KiCAD/bin/python.exe tests/test_clip_kicad.py

注意：会调 register()，必须在环境变量 BOARD_EDGE_TOOLS_CLI_MODE=1 下运行，
否则 KiCad 主程序外注册 ActionPlugin 会触发 PgmOrNull 断言（属正常现象）。
"""

import os
import sys

os.environ.setdefault("BOARD_EDGE_TOOLS_CLI_MODE", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import pcbnew  # noqa: E402
from core import (clip_items, collect_blade, iu2mm, mm2iu,  # noqa: E402
                  restore_shape)

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print("  PASS  {}".format(name))
    else:
        print("  FAIL  {}  {}".format(name, extra))
        FAILS.append(name)


def new_board():
    board = pcbnew.BOARD()
    return board


def add_segment(board, layer, x0, y0, x1, y1, width_mm=0.05):
    item = pcbnew.PCB_SHAPE(board)
    item.SetShape(pcbnew.S_SEGMENT)
    item.SetStart(pcbnew.VECTOR2I(mm2iu(x0), mm2iu(y0)))
    item.SetEnd(pcbnew.VECTOR2I(mm2iu(x1), mm2iu(y1)))
    item.SetWidth(mm2iu(width_mm))
    item.SetLayer(layer)
    board.Add(item)
    return item


def add_rect(board, layer, x0, y0, x1, y1, width_mm=0.05):
    item = pcbnew.PCB_SHAPE(board)
    item.SetShape(pcbnew.S_RECT)
    item.SetStart(pcbnew.VECTOR2I(mm2iu(x0), mm2iu(y0)))
    item.SetEnd(pcbnew.VECTOR2I(mm2iu(x1), mm2iu(y1)))
    item.SetWidth(mm2iu(width_mm))
    item.SetLayer(layer)
    board.Add(item)
    return item


def add_track(board, layer, x0, y0, x1, y1, width_mm=0.2):
    item = pcbnew.PCB_TRACK(board)
    item.SetStart(pcbnew.VECTOR2I(mm2iu(x0), mm2iu(y0)))
    item.SetEnd(pcbnew.VECTOR2I(mm2iu(x1), mm2iu(y1)))
    item.SetWidth(mm2iu(width_mm))
    item.SetLayer(layer)
    board.Add(item)
    return item


def main():
    print("KiCad {}".format(pcbnew.GetBuildVersion()))
    print("[裁剪 PCB_SHAPE 多边形]")
    board = new_board()

    # 被裁对象：一个 20x20 的矩形图形（S_RECT）
    target = add_rect(board, pcbnew.F_Mask, 0, 0, 20, 20)
    # 刀刃：一条竖线 x=10（开放线刀）
    blade = add_segment(board, pcbnew.Dwgs_User, 10, -5, 10, 25)

    polys, closed, skipped = core_collect(blade)
    check("刀刃识别为开放线", (not closed) and len(polys) == 1, (polys, closed))

    items, stats = clip_items(board, polys, [target])
    check("矩形被切断", stats["cut"] == 1, stats)
    # 矩形 4 条边，竖刀 x=10 切中上下两条水平边 → 环被切成 3 段弧；
    # 被切的两条边各一分为二（4 条）+ 未切的两条竖边（2 条）= 6 条线段
    check("生成 6 段", stats["pieces"] == 6, stats)
    check("都落在 F.Mask", all(i.GetLayer() == pcbnew.F_Mask for i in items),
          [i.GetLayer() for i in items])
    check("线宽保持", all(i.GetWidth() == mm2iu(0.05) for i in items))

    # 左半边（x<=10）应有 3 段：上边左半 + 左边 + 下边左半
    left = [i for i in items
            if iu2mm(i.GetStart()[0]) <= 10.001 and iu2mm(i.GetEnd()[0]) <= 10.001]
    check("左半 3 段", len(left) == 3, len(left))

    print("[原对象就地改造，绝不 Remove（防 KiCad 卡死）]")
    # 被裁对象通常是「当前选中」的，删掉它会让 Selection 工具留下悬空指针，
    # 之后 pcbnew.Refresh() / 任何画布交互都会卡死整个 KiCad。
    # 所以首段必须复用原对象，而不是 Remove + Add。
    board4 = new_board()
    t4 = add_track(board4, pcbnew.F_Cu, 0, 0, 20, 0)     # 走线
    s4 = add_segment(board4, pcbnew.F_Cu, 0, 10, 20, 10)  # 图形线段
    r4 = add_rect(board4, pcbnew.F_Cu, 0, 20, 20, 40)     # 矩形图形
    b4 = add_segment(board4, pcbnew.Dwgs_User, 10, -5, 10, 45)
    p4, c4, _ = core_collect(b4)
    items4, stats4 = clip_items(board4, p4, [t4, s4, r4])
    check("三个对象都被处理", stats4["cut"] == 3, stats4)
    check("走线原对象仍在板上", t4 in items4
          and isinstance(t4, pcbnew.PCB_TRACK), type(t4).__name__)
    check("线段原对象仍在板上", s4 in items4)
    check("矩形原对象仍在板上（已改成线段）",
          r4 in items4 and r4.GetShape() == pcbnew.S_SEGMENT,
          r4.GetShape())
    check("记录了 3 个改造快照（供撤销）",
          len(stats4["mutated"]) == 3, len(stats4["mutated"]))

    # 撤销：删掉新建的段 + 把原对象恢复成裁剪前的形状
    print("[撤销恢复]")
    mutated_ids = set(id(m[0]) for m in stats4["mutated"])
    for it in items4:
        if id(it) not in mutated_ids:
            try:
                board4.Remove(it)
            except Exception:
                pass
    for it, info in stats4["mutated"]:
        restore_shape(it, info)
    check("走线恢复原长 20mm",
          abs(iu2mm(t4.GetEnd()[0] - t4.GetStart()[0]) - 20.0) < 0.01,
          iu2mm(t4.GetEnd()[0] - t4.GetStart()[0]))
    check("矩形恢复为 S_RECT", r4.GetShape() == pcbnew.S_RECT, r4.GetShape())
    check("矩形恢复原尺寸",
          abs(iu2mm(r4.GetEnd()[1] - r4.GetStart()[1]) - 20.0) < 0.01,
          iu2mm(r4.GetEnd()[1] - r4.GetStart()[1]))

    print("[刀刃不闭合时不能判内外]")
    board2 = new_board()
    t2 = add_segment(board2, pcbnew.F_Cu, 0, 0, 20, 0)
    b2 = add_segment(board2, pcbnew.Dwgs_User, 10, -5, 10, 5)
    p2, c2, _ = core_collect(b2)
    items2, stats2 = clip_items(board2, p2, [t2])
    check("开放刀刃只切断", stats2["cut"] == 1 and stats2["pieces"] == 2, stats2)

    print("[走线也能裁]")
    board3 = new_board()
    t3 = add_track(board3, pcbnew.F_Cu, 0, 0, 20, 0)
    b3 = add_segment(board3, pcbnew.Dwgs_User, 10, -5, 10, 5)
    p3, c3, _ = core_collect(b3)
    items3, stats3 = clip_items(board3, p3, [t3])
    check("走线切成 2 段", stats3["cut"] == 1 and stats3["pieces"] == 2, stats3)
    check("新对象仍是 PCB_TRACK",
          all(isinstance(i, pcbnew.PCB_TRACK) for i in items3),
          [type(i).__name__ for i in items3])

    print("[闭合刀刃 + 删除内侧 / 外侧]")
    # 刀刃：闭合矩形 (5,-5)-(15,25)；被裁：水平线 (0,0)→(20,0)
    # 交点 x=5 / x=15 → 切成 [0,5] [5,15] [15,20] 三段
    def make():
        b = new_board()
        line = add_segment(b, pcbnew.F_Cu, 0, 0, 20, 0)
        bl = add_rect(b, pcbnew.Dwgs_User, 5, -5, 15, 25)
        return b, line, bl

    for mode, expect in (("none", 3), ("inside", 2), ("outside", 1)):
        b, line, bl = make()
        pp, cc, _ = core_collect(bl)
        check("刀刃是闭合的", cc, cc)
        _items, st = clip_items(b, pp, [line], mode)
        check("delete={} → {} 段".format(mode, expect),
              st["pieces"] == expect, st)

    print("")
    if FAILS:
        print("{} 项失败: {}".format(len(FAILS), ", ".join(FAILS)))
        return 1
    print("全部通过")
    return 0


def core_collect(item):
    from core import collect_blade as _cb
    return _cb([item])


if __name__ == "__main__":
    sys.exit(main())
