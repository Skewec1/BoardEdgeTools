"""裁剪页 GUI 流程集成测试（需 KiCad 自带 python + wx）。

    D:/Application/KiCAD/bin/python.exe tests/test_clip_gui_flow.py

用假的 GetBoard / GetCurrentSelection 驱动整条链路：
    拾取刀刃 → 拾取被裁对象 → 执行裁剪
"""

import os
import sys

os.environ.setdefault("BOARD_EDGE_TOOLS_CLI_MODE", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG_ROOT = os.path.dirname(ROOT)
sys.path.insert(0, PKG_ROOT)

import wx  # noqa: E402
import pcbnew  # noqa: E402
import BoardEdgeTools.plugin as P  # noqa: E402
from BoardEdgeTools import core  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print("  PASS  {}".format(name))
    else:
        print("  FAIL  {}  {}".format(name, extra))
        FAILS.append(name)


def add_segment(board, layer, x0, y0, x1, y1, width_mm=0.05):
    item = pcbnew.PCB_SHAPE(board)
    item.SetShape(pcbnew.S_SEGMENT)
    item.SetStart(pcbnew.VECTOR2I(core.mm2iu(x0), core.mm2iu(y0)))
    item.SetEnd(pcbnew.VECTOR2I(core.mm2iu(x1), core.mm2iu(y1)))
    item.SetWidth(core.mm2iu(width_mm))
    item.SetLayer(layer)
    board.Add(item)
    return item


def add_rect(board, layer, x0, y0, x1, y1, width_mm=0.05):
    item = pcbnew.PCB_SHAPE(board)
    item.SetShape(pcbnew.S_RECT)
    item.SetStart(pcbnew.VECTOR2I(core.mm2iu(x0), core.mm2iu(y0)))
    item.SetEnd(pcbnew.VECTOR2I(core.mm2iu(x1), core.mm2iu(y1)))
    item.SetWidth(core.mm2iu(width_mm))
    item.SetLayer(layer)
    board.Add(item)
    return item


class Fake(object):
    """临时替换 pcbnew 里的板子和选择集。"""

    def __init__(self, board, selection):
        self.board = board
        self.selection = list(selection)
        self.saved = {}

    def __enter__(self):
        self.saved = {
            "GetBoard": pcbnew.GetBoard,
            "GetCurrentSelection": pcbnew.GetCurrentSelection,
        }
        pcbnew.GetBoard = lambda: self.board
        pcbnew.GetCurrentSelection = lambda: list(self.selection)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            setattr(pcbnew, k, v)
        return False


def main():
    app = wx.App(False)
    frame = P.MainFrame(None)

    print("[初始状态]")
    check("刀刃未拾取时内外选项禁用",
          not frame.rb_del_in.IsEnabled() and not frame.rb_del_out.IsEnabled())
    check("裁剪页状态标签存在", frame.lbl_clip_status.GetLabel() == "就绪。")

    board = pcbnew.BOARD()
    blade = add_segment(board, pcbnew.Dwgs_User, 10, -5, 10, 25)
    target = add_rect(board, pcbnew.F_Mask, 0, 0, 20, 20)

    print("[拾取刀刃]")
    with Fake(board, [blade]):
        frame.on_pick_blade(None)
    check("刀刃已记录", frame.blade == [blade], frame.blade)
    check("刀刃折线 1 段", len(frame.blade_polys) == 1, frame.blade_polys)
    check("开放刀刃 → 内外仍禁用",
          not frame.rb_del_in.IsEnabled() and not frame.rb_del_out.IsEnabled())
    check("标签提示开放", "开放" in frame.lbl_blade.GetLabel(),
          frame.lbl_blade.GetLabel())

    print("[拾取被裁对象]")
    with Fake(board, [target]):
        frame.on_pick_targets(None)
    check("被裁对象已记录", frame.targets == [target], frame.targets)

    print("[执行裁剪]")
    before = len(list(board.GetDrawings()))
    with Fake(board, []):
        frame.on_clip_run(None)
    after = len(list(board.GetDrawings()))
    # 原矩形被删(1)，生成 6 段；刀刃默认勾选删除(-1)
    check("图形数 = 原 2 - 1(矩形) - 1(刀刃) + 6 = 6", after == 6,
          (before, after))
    check("撤销栈已记录", len(frame.last_items) == 6, len(frame.last_items))

    print("[拾取状态已重置]")
    check("刀刃已清空", frame.blade == [] and frame.blade_polys == [])
    check("被裁对象已清空", frame.targets == [])
    check("内外选项回到禁用", not frame.rb_del_in.IsEnabled())

    print("[撤销]")
    with Fake(board, []):
        frame.on_undo(None)
    # 裁剪不再删除原对象（否则 KiCad 刷新会卡死），撤销要把原对象**还原**：
    # 删掉新建的 5 段 + 把原矩形恢复成 S_RECT → 板上只剩原对象 1 个
    left = list(board.GetDrawings())
    check("撤销后只剩原对象", len(left) == 1, len(left))
    check("原对象已恢复为矩形",
          len(left) == 1 and left[0].GetShape() == pcbnew.S_RECT,
          left[0].GetShape() if left else None)

    frame.Destroy()
    print("")
    if FAILS:
        print("{} 项失败: {}".format(len(FAILS), ", ".join(FAILS)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
