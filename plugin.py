"""KiCad ActionPlugin：描画 + 裁剪工具。

窗口分两页（wx.Notebook）：

**描画页**
1. **来源**：PCB 上**框选的图形**（线段 / 圆弧 / 多边形 / 矩形 / 圆 /
   敷铜轮廓 / 封装包围盒），或**板框**（Edge.Cuts，含挖槽）。
2. **描画轮廓**：对来源图形整体做等距偏移（负=内缩，正=外扩），
   输出闭合多边形到任意层。勾选**「输出为离散图线」**后改为拆成
   逐段独立的线段（``S_SEGMENT``），可在指定层生成、源对象保留，
   生成结果可像普通图线一样单段删减修改。
3. **描画露铜带**：沿来源图形描一条等宽带（阻焊开窗带），
   可用矩形断开区任意分段。

**裁剪页**
4. **裁剪线条**：画一条线（或闭合图形）当**刀刃**，选中要裁的线条，
   在交点处切断。支持走线 ``PCB_TRACK`` / 圆弧走线 ``PCB_ARC`` /
   图形 ``PCB_SHAPE``（线段、圆弧、矩形、圆、多边形、贝塞尔）。
   可选保留两侧（默认，等同 AD ``Edit → Trim``）/ 删除刀刃内侧 /
   删除刀刃外侧。

GUI 是**非模态**窗口（置顶），可以一边在画布上框选图形 / 画矩形标记，
一边点按钮取数据。
"""

import functools
import json
import os
import sys
import traceback

import wx

try:
    import pcbnew
    _ActionPlugin = pcbnew.ActionPlugin
except ImportError:  # 非 KiCad 环境（静态检查 / 单元测试）
    pcbnew = None
    _ActionPlugin = object

from . import core

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(PLUGIN_DIR, "config.json")

RUN_LOG_PATH = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "kicad",
    "BoardEdgeTools", "run.log")


def _log(msg):
    """追加一行到 run.log（带时间戳），任何情况下都不抛异常。

    时间戳是排查「卡死」的关键：卡住前最后一条日志 + 下一条日志的
    时间差，能直接定位卡在哪一步。
    """
    try:
        import datetime
        stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        os.makedirs(os.path.dirname(RUN_LOG_PATH), exist_ok=True)
        with open(RUN_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write("{} | {}\n".format(stamp, msg.rstrip()))
    except Exception:
        pass


# core 的阶段诊断日志统一走 _log（带时间戳），用于定位卡死点
core.set_log_fn(_log)


def _msg(parent, text, title, style=wx.OK | wx.ICON_INFORMATION):
    """弹模态对话框，**用完必须 Destroy**。

    坑：``wx.MessageDialog(...).ShowModal()`` 这种一次性写法不销毁对话框，
    会直接把 wx 事件循环卡住 → 整个 KiCad 无响应（看起来像"卡死"）。
    所有弹窗统一走这里。
    """
    dlg = wx.MessageDialog(parent, text, title, style)
    try:
        return dlg.ShowModal()
    finally:
        dlg.Destroy()


def _guard(fn):
    """事件处理器兜底：异常写 run.log + 弹窗，绝不穿透到 wx 主循环。"""
    @functools.wraps(fn)
    def wrapper(self, event=None):
        try:
            _log("[evt] {}".format(fn.__name__))
            return fn(self, event)
        except Exception:
            tb = traceback.format_exc()
            _log("[err] {} 崩溃:\n{}".format(fn.__name__, tb))
            try:
                _msg(self,
                     "插件内部错误，已记录到:\n{}\n\n{}".format(
                         RUN_LOG_PATH, tb[-1200:]),
                     "描画 / 裁剪工具", wx.OK | wx.ICON_ERROR)
            except Exception:
                pass
    return wrapper


DEFAULT_CONFIG = {
    "source": "selection",          # selection | board
    "mode": "band",                 # band | outline
    "offset_mm": -1.0,
    "band_mm": 1.0,
    "inset_mm": 0.0,
    "layer": "F_Mask",
    "mirror": True,
    "corner": core.CORNER_STRATEGIES[0][1],
    "step_mm": 0.3,
    "tol_mm": 0.02,
    "marker_layer": "User_9",
    "delete_markers": True,
    "replace_edge_cuts": False,
    "discrete": False,
    "line_width_mm": 0.05,
    "gap_boxes": [],
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _fmt_box(box):
    return "({:.3f}, {:.3f}) - ({:.3f}, {:.3f})".format(*box)


class MainFrame(wx.Frame):
    """非模态主窗口。"""

    def __init__(self, parent=None):
        _log("[gui] MainFrame init start")
        super(MainFrame, self).__init__(
            parent,
            title="描画 / 裁剪工具 BoardEdgeTools v1.4.0",
            style=(wx.DEFAULT_FRAME_STYLE | wx.STAY_ON_TOP | wx.TAB_TRAVERSAL))
        self.SetSizeHints(wx.Size(420, 620))

        self.cfg = load_config()
        self.last_items = []
        self.clip_mutated = []   # 裁剪时被原地改造的原对象 + 原几何快照
        self.gap_boxes = [list(b) for b in self.cfg.get("gap_boxes", [])]
        self.selection = []          # 作为「来源」的选中图形

        # 裁剪页状态
        self.blade = []              # 刀刃图形
        self.blade_polys = []
        self.blade_closed = False
        self.targets = []            # 被裁对象

        # wx.Frame 只有一个子窗口时会自动把它撑满客户区
        self.nb = wx.Notebook(self)

        panel = wx.Panel(self.nb)
        root = wx.BoxSizer(wx.VERTICAL)
        self.nb.AddPage(panel, "描画（偏移 / 露铜带）")

        # ---------------- 来源 ----------------
        src_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "来源（描画基准）")
        self.rb_src_sel = wx.RadioButton(
            panel, label="选中图形（框选的线条 / 圆弧 / 多边形）",
            style=wx.RB_GROUP)
        self.rb_src_board = wx.RadioButton(panel, label="板框（Edge.Cuts，含挖槽）")
        src_box.Add(self.rb_src_sel, 0, wx.ALL, 3)
        src_box.Add(self.rb_src_board, 0, wx.ALL, 3)
        root.Add(src_box, 0, wx.EXPAND | wx.ALL, 6)

        pick_row = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_pick_src = wx.Button(panel, label="拾取当前选中图形")
        self.lbl_src = wx.StaticText(panel, label="未拾取")
        pick_row.Add(self.btn_pick_src, 0, wx.RIGHT, 6)
        pick_row.Add(self.lbl_src, 0, wx.ALIGN_CENTER_VERTICAL)
        src_box.Add(pick_row, 0, wx.ALL, 3)
        root.Add(wx.BoxSizer(wx.VERTICAL), 0)

        # ---------------- 模式 ----------------
        mode_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "模式")
        self.rb_band = wx.RadioButton(
            panel, label="描画露铜带（阻焊开窗带，可分段）", style=wx.RB_GROUP)
        self.rb_outline = wx.RadioButton(panel, label="描画轮廓（整圈闭合，内缩/外扩）")
        mode_box.Add(self.rb_band, 0, wx.ALL, 3)
        mode_box.Add(self.rb_outline, 0, wx.ALL, 3)
        root.Add(mode_box, 0, wx.EXPAND | wx.ALL, 6)

        # ---------------- 参数 ----------------
        grid = wx.FlexGridSizer(cols=2, vgap=5, hgap=8)
        grid.AddGrowableCol(1)

        self.sc_offset = self._spin(panel, -1000.0, 1000.0,
                         self.cfg.get("offset_mm", -1.0))
        self.sc_band = self._spin(panel, 0.001, 1000.0,
                        self.cfg.get("band_mm", 1.0))
        self.sc_inset = self._spin(panel, -100.0, 1000.0,
                         self.cfg.get("inset_mm", 0.0))
        self.sc_width = self._spin(panel, 0.0, 10.0,
                         self.cfg.get("line_width_mm", 0.05))
        self.sc_step = self._spin(panel, 0.02, 10.0,
                        self.cfg.get("step_mm", 0.3))
        self.sc_tol = self._spin(panel, 0.001, 1.0,
                       self.cfg.get("tol_mm", 0.02))

        self.cmb_layer = wx.ComboBox(
            panel, style=wx.CB_DROPDOWN | wx.CB_READONLY,
            choices=[n for n, _ in core.LAYER_CHOICES])
        self._select_combo(self.cmb_layer, core.LAYER_CHOICES,
                           self.cfg.get("layer", "F_Mask"))

        self.cmb_corner = wx.ComboBox(
            panel, style=wx.CB_DROPDOWN | wx.CB_READONLY,
            choices=[n for n, _ in core.CORNER_STRATEGIES])
        self._select_combo(self.cmb_corner, core.CORNER_STRATEGIES,
                           self.cfg.get("corner",
                                        core.CORNER_STRATEGIES[0][1]))

        self.cmb_marker = wx.ComboBox(
            panel, style=wx.CB_DROPDOWN | wx.CB_READONLY,
            choices=[n for n, _ in core.LAYER_CHOICES])
        self._select_combo(self.cmb_marker, core.LAYER_CHOICES,
                           self.cfg.get("marker_layer", "User_9"))

        grid.Add(wx.StaticText(panel, label="偏移量 (mm)  负=内缩 / 正=外扩"), 0,
               wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.sc_offset, 0, wx.EXPAND)
        grid.Add(wx.StaticText(panel, label="带宽 W (mm)"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.sc_band, 0, wx.EXPAND)
        grid.Add(wx.StaticText(panel, label="外沿距来源图形 (mm)"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.sc_inset, 0, wx.EXPAND)
        grid.Add(wx.StaticText(panel, label="线宽（仅轮廓模式, mm）"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.sc_width, 0, wx.EXPAND)
        grid.Add(wx.StaticText(panel, label="目标层"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.cmb_layer, 0, wx.EXPAND)
        grid.Add(wx.StaticText(panel, label="角点处理"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.cmb_corner, 0, wx.EXPAND)
        grid.Add(wx.StaticText(panel, label="标记层（画矩形用）"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.cmb_marker, 0, wx.EXPAND)

        self.chk_mirror = wx.CheckBox(panel, label="同时生成另一面（Top↔Bottom）")
        self.chk_mirror.SetValue(bool(self.cfg.get("mirror", True)))
        self.chk_del_marker = wx.CheckBox(panel, label="读取后删除标记图形")
        self.chk_del_marker.SetValue(bool(self.cfg.get("delete_markers",
                                                        True)))
        self.chk_replace = wx.CheckBox(panel,
                                       label="替换现有板框（仅 Edge.Cuts，会删除源板框）")
        self.chk_replace.SetValue(bool(self.cfg.get("replace_edge_cuts",
                                                    False)))
        self.chk_discrete = wx.CheckBox(
            panel, label="输出为离散图线（逐段独立，可单独删减）")
        self.chk_discrete.SetValue(bool(self.cfg.get("discrete", False)))

        param_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "参数")
        param_box.Add(grid, 0, wx.EXPAND | wx.ALL, 4)
        param_box.Add(self.chk_mirror, 0, wx.ALL, 3)
        param_box.Add(self.chk_discrete, 0, wx.ALL, 3)
        param_box.Add(self.chk_replace, 0, wx.ALL, 3)
        root.Add(param_box, 0, wx.EXPAND | wx.ALL, 6)

        # ---------------- 断开区 ----------------
        gap_box = wx.StaticBoxSizer(wx.VERTICAL, panel,
                                    "断开区（矩形，板坐标 mm）")
        self.list_gaps = wx.ListBox(panel, size=(-1, 80))
        gap_box.Add(self.list_gaps, 1, wx.EXPAND | wx.ALL, 3)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_marker = wx.Button(panel, label="读取标记层矩形")
        self.btn_sel_gap = wx.Button(panel, label="用选中对象作断开区")
        self.btn_add = wx.Button(panel, label="手动添加…")
        self.btn_del = wx.Button(panel, label="删除")
        self.btn_clear = wx.Button(panel, label="清空")
        for b in (self.btn_marker, self.btn_sel_gap, self.btn_add,
                  self.btn_del, self.btn_clear):
            btn_row.Add(b, 0, wx.RIGHT, 3)
        gap_box.Add(btn_row, 0, wx.ALL, 3)
        gap_box.Add(wx.StaticText(
            panel, label="用法：先在标记层画矩形 → 点「读取标记层矩形」；"
                         "或选中器件/图形 → 点「用选中对象作断开区」。"),
            0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)
        gap_box.Add(self.chk_del_marker, 0, wx.LEFT | wx.BOTTOM, 4)
        root.Add(gap_box, 0, wx.EXPAND | wx.ALL, 6)

        # ---------------- 高级 ----------------
        adv = wx.FlexGridSizer(cols=2, vgap=5, hgap=8)
        adv.AddGrowableCol(1)
        adv.Add(wx.StaticText(panel, label="离散步长 (mm)"), 0,
                wx.ALIGN_CENTER_VERTICAL)
        adv.Add(self.sc_step, 0, wx.EXPAND)
        adv.Add(wx.StaticText(panel, label="共线合并容差 (mm)"), 0,
                wx.ALIGN_CENTER_VERTICAL)
        adv.Add(self.sc_tol, 0, wx.EXPAND)
        adv_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "精度")
        adv_box.Add(adv, 0, wx.EXPAND | wx.ALL, 4)
        root.Add(adv_box, 0, wx.EXPAND | wx.ALL, 6)

        # ---------------- 按钮 ----------------
        btn_main = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_run = wx.Button(panel, label="生成")
        self.btn_undo = wx.Button(panel, label="撤销上次生成")
        self.btn_close = wx.Button(panel, label="关闭")
        btn_main.Add(self.btn_run, 0, wx.RIGHT, 5)
        btn_main.Add(self.btn_undo, 0, wx.RIGHT, 5)
        btn_main.AddStretchSpacer()
        btn_main.Add(self.btn_close, 0)
        root.Add(btn_main, 0, wx.EXPAND | wx.ALL, 6)

        self.status = wx.StaticText(panel, label="就绪。")
        self.status.SetForegroundColour(wx.Colour(90, 90, 90))
        root.Add(self.status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        panel.SetSizer(root)

        self._build_clip_page()

        self.Fit()
        self.SetMinSize(self.GetSize())

        # 初始化
        if self.cfg.get("source", "selection") == "board":
            self.rb_src_board.SetValue(True)
        else:
            self.rb_src_sel.SetValue(True)
        if self.cfg.get("mode", "band") == "outline":
            self.rb_outline.SetValue(True)
        else:
            self.rb_band.SetValue(True)
        self._refresh_gap_list()
        self._sync_mode()

        # 事件
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_RADIOBUTTON, self._sync_mode, self.rb_src_sel)
        self.Bind(wx.EVT_RADIOBUTTON, self._sync_mode, self.rb_src_board)
        self.Bind(wx.EVT_RADIOBUTTON, self._sync_mode, self.rb_band)
        self.Bind(wx.EVT_RADIOBUTTON, self._sync_mode, self.rb_outline)
        self.Bind(wx.EVT_BUTTON, self.on_pick_source, self.btn_pick_src)
        self.Bind(wx.EVT_BUTTON, self.on_read_markers, self.btn_marker)
        self.Bind(wx.EVT_BUTTON, self.on_sel_gap, self.btn_sel_gap)
        self.Bind(wx.EVT_BUTTON, self.on_add_manual, self.btn_add)
        self.Bind(wx.EVT_BUTTON, self.on_del_gap, self.btn_del)
        self.Bind(wx.EVT_BUTTON, self.on_clear_gaps, self.btn_clear)
        self.Bind(wx.EVT_BUTTON, self.on_generate, self.btn_run)
        self.Bind(wx.EVT_BUTTON, self.on_undo, self.btn_undo)
        self.Bind(wx.EVT_BUTTON, self.on_close, self.btn_close)
        _log("[gui] MainFrame init done")

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------
    def _spin(self, parent, lo, hi, value):
        ctrl = wx.SpinCtrlDouble(parent, min=lo, max=hi, inc=0.1,
               initial=float(value))
        ctrl.SetDigits(3)
        return ctrl

    @staticmethod
    def _select_combo(combo, choices, attr_name):
        for index, (_label, attr) in enumerate(choices):
            if attr == attr_name:
                combo.SetSelection(index)
                return
        combo.SetSelection(0)

    @staticmethod
    def _attr_of(choices, index):
        return choices[index][1]

    def set_status(self, text, warn=False):
        colour = wx.Colour(180, 60, 60) if warn else wx.Colour(90, 90, 90)
        for label in (self.status, getattr(self, "lbl_clip_status", None)):
            if label is None:
                continue
            label.SetLabel(text)
            label.SetForegroundColour(colour)
            try:
                width = label.GetParent().GetClientSize().width
                if width > 120:
                    label.Wrap(width - 20)
            except Exception:
                pass
        self.Layout()

    def _refresh_gap_list(self):
        self.list_gaps.Clear()
        for box in self.gap_boxes:
            self.list_gaps.Append(_fmt_box(box))

    def _sync_mode(self, event=None):
        band_mode = self.rb_band.GetValue()
        sel_mode = self.rb_src_sel.GetValue()
        self.sc_band.Enable(band_mode)
        self.sc_inset.Enable(band_mode)
        self.sc_width.Enable(not band_mode)
        self.sc_offset.Enable(not band_mode)
        self.btn_pick_src.Enable(sel_mode)
        if sel_mode:
            self.lbl_src.SetLabel(
                "已拾取 {} 个对象".format(len(self.selection))
                if self.selection else "未拾取")
        else:
            self.lbl_src.SetLabel("使用板框")

    def _collect_config(self):
        return {
            "source": "board" if self.rb_src_board.GetValue() else "selection",
            "mode": "outline" if self.rb_outline.GetValue() else "band",
            "offset_mm": self.sc_offset.GetValue(),
            "band_mm": self.sc_band.GetValue(),
            "inset_mm": self.sc_inset.GetValue(),
            "line_width_mm": self.sc_width.GetValue(),
            "layer": self._attr_of(core.LAYER_CHOICES,
                                   self.cmb_layer.GetSelection()),
            "corner": self._attr_of(core.CORNER_STRATEGIES,
                                    self.cmb_corner.GetSelection()),
            "marker_layer": self._attr_of(core.LAYER_CHOICES,
                                          self.cmb_marker.GetSelection()),
            "mirror": self.chk_mirror.GetValue(),
            "delete_markers": self.chk_del_marker.GetValue(),
            "replace_edge_cuts": self.chk_replace.GetValue(),
            "discrete": self.chk_discrete.GetValue(),
            "step_mm": self.sc_step.GetValue(),
            "tol_mm": self.sc_tol.GetValue(),
            "gap_boxes": [list(b) for b in self.gap_boxes],
        }

    def _target_layers(self, layer_attr):
        layers = [core.layer_id(layer_attr)]
        if self.chk_mirror.GetValue():
            mirror = core.MIRROR_LAYERS.get(layer_attr)
            if mirror:
                layers.append(core.layer_id(mirror))
        return layers

    # ------------------------------------------------------------------
    # 来源 / 断开区
    # ------------------------------------------------------------------
    @_guard
    def on_pick_source(self, event):
        """把 PCB 上当前选中的对象存为描画来源。"""
        try:
            selection = list(pcbnew.GetCurrentSelection())
        except Exception as exc:
            self.set_status("读取选择失败：{}".format(exc), warn=True)
            return

        if not selection:
            self.set_status(
                "当前没有选中对象。请先在 PCB 编辑器里框选要描画的图形。", warn=True)
            return

        self.selection = selection
        usable, skipped = core.collect_selection_polylines(selection)
        if usable:
            msg = "已拾取 {} 个对象（{} 段可描画）".format(
                len(selection), len(usable))
            if skipped:
                msg += "，忽略: " + ", ".join(
                    "{}×{}".format(k, v) for k, v in sorted(skipped.items()))
            self.set_status(msg)
        else:
            detail = ", ".join("{}×{}".format(k, v)
                               for k, v in sorted(skipped.items()))
            self.set_status(
                "拾取的 {} 个对象都不是可描画类型（{}）".format(
                    len(selection), detail), warn=True)
        self._sync_mode()

    def _bboxes_from_selection(self):
        boxes = []
        for item in list(pcbnew.GetCurrentSelection()):
            try:
                box = item.GetBoundingBox()
                boxes.append([
                    core.iu2mm(box.GetX()),
                    core.iu2mm(box.GetY()),
                    core.iu2mm(box.GetX() + box.GetWidth()),
                    core.iu2mm(box.GetY() + box.GetHeight()),
                ])
            except Exception:
                continue
        return boxes

    @_guard
    def on_read_markers(self, event):
        """扫描标记层上的图形，取其包围盒作为断开区。"""
        board = pcbnew.GetBoard()
        layer_attr = self._attr_of(core.LAYER_CHOICES,
                                   self.cmb_marker.GetSelection())
        layer = core.layer_id(layer_attr)

        found, marks = [], []
        for item in list(board.GetDrawings()):
            try:
                if item.GetLayer() != layer:
                    continue
            except Exception:
                continue
            try:
                box = item.GetBoundingBox()
                x0 = core.iu2mm(box.GetX())
                y0 = core.iu2mm(box.GetY())
                x1 = core.iu2mm(box.GetX() + box.GetWidth())
                y1 = core.iu2mm(box.GetY() + box.GetHeight())
                if abs(x1 - x0) < 1e-6 and abs(y1 - y0) < 1e-6:
                    continue
                found.append([x0, y0, x1, y1])
                marks.append(item)
            except Exception:
                continue

        if not found:
            self.set_status("标记层上没有找到图形。请先在 {} 上画矩形。".format(
            core.layer_label(layer_attr)), warn=True)
            return

        self.gap_boxes.extend(found)
        self._refresh_gap_list()

        if self.chk_del_marker.GetValue():
            for item in marks:
                try:
                    board.Remove(item)
                except Exception:
                    pass
            wx.CallAfter(self._do_refresh)

        self.set_status("已读取 {} 个标记矩形{}。".format(
            len(found), "并删除标记图形"
            if self.chk_del_marker.GetValue() else ""))

    @_guard
    def on_sel_gap(self, event):
        """用 PCB 上当前选中对象的包围盒作为断开区。"""
        try:
            boxes = self._bboxes_from_selection()
        except Exception as exc:
            self.set_status("读取选择失败：{}".format(exc), warn=True)
            return
        if not boxes:
            self.set_status("当前没有选中对象。", warn=True)
            return
        self.gap_boxes.extend(boxes)
        self._refresh_gap_list()
        self.set_status("已添加 {} 个断开区。".format(len(boxes)))

    @_guard
    def on_add_manual(self, event):
        text = wx.GetTextFromUser(
            "输入矩形坐标（mm），格式：x0,y0,x1,y1", "添加断开区", "")
        if not text:
            return
        try:
            parts = [float(v) for v in text.replace("，", ",").split(",")]
            if len(parts) != 4:
                raise ValueError
        except ValueError:
            self.set_status("格式不对，应为 x0,y0,x1,y1 四个数字。", warn=True)
            return
        self.gap_boxes.append(parts)
        self._refresh_gap_list()
        self.set_status("已添加 1 个断开区。")

    @_guard
    def on_del_gap(self, event):
        index = self.list_gaps.GetSelection()
        if index == wx.NOT_FOUND:
            self.set_status("请先在列表里选中一个断开区。", warn=True)
            return
        del self.gap_boxes[index]
        self._refresh_gap_list()
        self.set_status("已删除 1 个断开区。")

    @_guard
    def on_clear_gaps(self, event):
        self.gap_boxes = []
        self._refresh_gap_list()
        self.set_status("断开区已清空。")

    # ------------------------------------------------------------------
    # 裁剪页
    # ------------------------------------------------------------------
    def _build_clip_page(self):
        panel = wx.Panel(self.nb)
        self.nb.AddPage(panel, "裁剪线条")
        root = wx.BoxSizer(wx.VERTICAL)

        # ---------------- 刀刃 ----------------
        blade_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "① 刀刃（切割边界）")
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_pick_blade = wx.Button(panel, label="拾取当前选中图形作刀刃")
        self.lbl_blade = wx.StaticText(panel, label="未拾取")
        row.Add(self.btn_pick_blade, 0, wx.RIGHT, 6)
        row.Add(self.lbl_blade, 0, wx.ALIGN_CENTER_VERTICAL)
        blade_box.Add(row, 0, wx.ALL, 3)
        blade_box.Add(wx.StaticText(
            panel, label="开放线（一条直线刀）或闭合图形（矩形 / 圆 / 多边形）均可。"),
            0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)
        root.Add(blade_box, 0, wx.EXPAND | wx.ALL, 6)

        # ---------------- 被裁对象 ----------------
        tgt_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "② 被裁对象")
        row2 = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_pick_targets = wx.Button(panel, label="拾取当前选中图形作被裁对象")
        self.lbl_targets = wx.StaticText(panel, label="未拾取")
        row2.Add(self.btn_pick_targets, 0, wx.RIGHT, 6)
        row2.Add(self.lbl_targets, 0, wx.ALIGN_CENTER_VERTICAL)
        tgt_box.Add(row2, 0, wx.ALL, 3)
        tgt_box.Add(wx.StaticText(
            panel, label="走线 / 圆弧走线 / 图形线条 / 圆弧 / 多边形 / 矩形 / 圆。"
                         "敷铜与封装会被跳过（不会破坏）。"),
            0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)
        root.Add(tgt_box, 0, wx.EXPAND | wx.ALL, 6)

        # ---------------- 裁剪后 ----------------
        del_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "③ 裁剪后")
        self.rb_keep = wx.RadioButton(
            panel, label="保留两侧（仅切断，等同 AD Edit → Trim）",
            style=wx.RB_GROUP)
        self.rb_del_in = wx.RadioButton(panel, label="删除刀刃内侧部分")
        self.rb_del_out = wx.RadioButton(panel, label="删除刀刃外侧部分")
        for rb in (self.rb_keep, self.rb_del_in, self.rb_del_out):
            del_box.Add(rb, 0, wx.ALL, 3)
        self.chk_del_blade = wx.CheckBox(panel, label="裁剪后删除刀刃图形")
        self.chk_del_blade.SetValue(True)
        del_box.Add(self.chk_del_blade, 0, wx.ALL, 3)
        root.Add(del_box, 0, wx.EXPAND | wx.ALL, 6)

        # ---------------- 按钮 ----------------
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_clip_run = wx.Button(panel, label="执行裁剪")
        self.btn_clip_undo = wx.Button(panel, label="撤销上次")
        btn_row.Add(self.btn_clip_run, 0, wx.RIGHT, 5)
        btn_row.Add(self.btn_clip_undo, 0)
        root.Add(btn_row, 0, wx.EXPAND | wx.ALL, 6)

        self.lbl_clip_status = wx.StaticText(panel, label="就绪。")
        self.lbl_clip_status.SetForegroundColour(wx.Colour(90, 90, 90))
        root.Add(self.lbl_clip_status, 0,
                 wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        panel.SetSizer(root)

        self.btn_pick_blade.Bind(wx.EVT_BUTTON, self.on_pick_blade)
        self.btn_pick_targets.Bind(wx.EVT_BUTTON, self.on_pick_targets)
        self.btn_clip_run.Bind(wx.EVT_BUTTON, self.on_clip_run)
        self.btn_clip_undo.Bind(wx.EVT_BUTTON, self.on_undo)

        # 「删除内侧 / 外侧」需要闭合刀刃才有意义
        self.rb_del_in.Enable(False)
        self.rb_del_out.Enable(False)
        self._sync_clip()

    def _sync_clip(self):
        """按刀刃是否闭合，启用 / 禁用内外删除选项。"""
        ok = self.blade_closed and bool(self.blade_polys)
        self.rb_del_in.Enable(ok)
        self.rb_del_out.Enable(ok)
        if not ok and (self.rb_del_in.GetValue() or self.rb_del_out.GetValue()):
            self.rb_keep.SetValue(True)

    def _current_selection(self):
        try:
            return list(pcbnew.GetCurrentSelection())
        except Exception:
            return []

    @_guard
    def on_pick_blade(self, event):
        selection = self._current_selection()
        if not selection:
            self.set_status(
                "当前没有选中对象。请先在 PCB 编辑器里框选要当刀刃的图形。",
                warn=True)
            return

        polys, closed, skipped = core.collect_blade(selection)
        if not polys:
            detail = ", ".join("{}×{}".format(k, v)
                               for k, v in sorted(skipped.items())) or "无对象"
            self.set_status(
                "刀刃拾取失败，选中的对象都不可描画（{}）。".format(detail),
                warn=True)
            return

        self.blade = selection
        self.blade_polys = polys
        self.blade_closed = closed
        self.lbl_blade.SetLabel("{} 个对象 / {} 段{}".format(
            len(selection), len(polys), "（闭合）" if closed else "（开放）"))
        self._sync_clip()
        self.set_status("刀刃已拾取：{} 段，{}。".format(
            len(polys), "闭合边界" if closed else "开放线，只能切断不能判内外"))

    @_guard
    def on_pick_targets(self, event):
        selection = self._current_selection()
        if not selection:
            self.set_status(
                "当前没有选中对象。请先在 PCB 编辑器里框选要裁剪的线条。",
                warn=True)
            return

        usable, skipped = core.collect_selection_polylines(selection)
        if not usable:
            detail = ", ".join("{}×{}".format(k, v)
                               for k, v in sorted(skipped.items())) or "无对象"
            self.set_status(
                "没有可裁剪的对象（{}）。".format(detail), warn=True)
            return

        self.targets = selection
        self.lbl_targets.SetLabel("{} 个对象 / {} 段".format(
            len(selection), len(usable)))
        self.set_status("被裁对象已拾取：{} 个对象、{} 段。".format(
            len(selection), len(usable)))

    @_guard
    def on_clip_run(self, event):
        board = pcbnew.GetBoard()

        if not self.blade_polys:
            _msg(self,
                 "还没拾取刀刃。\n\n"
                 "请先在 PCB 编辑器里框选要当刀刃的图形，然后点「拾取…作刀刃」。",
                 "裁剪线条")
            return
        if not self.targets:
            _msg(self,
                 "还没拾取被裁对象。\n\n"
                 "请在 PCB 编辑器里框选要裁剪的线条，然后点「拾取…作被裁对象」。",
                 "裁剪线条")
            return

        delete = "none"
        if self.rb_del_in.GetValue():
            delete = "inside"
        elif self.rb_del_out.GetValue():
            delete = "outside"

        if delete != "none" and not self.blade_closed:
            _msg(self,
                 "刀刃是开放线，没有「内 / 外」的概念，已按「保留两侧」处理。\n\n"
                 "要用删除功能，请把刀刃画成闭合图形（矩形 / 圆 / 多边形）。",
                 "裁剪线条", wx.OK | wx.ICON_WARNING)
            delete = "none"

        # 规模保护：误框选整板时先确认，避免长时间无响应
        n_targets = len(self.targets)
        if n_targets > 500:
            answer = _msg(
                self,
                "被裁对象有 {} 个，数量偏大，裁剪期间 KiCad 会短暂无响应。\n\n"
                "如果是误框选了整块板子，请点「否」并重新框选。\n"
                "确定继续吗？".format(n_targets),
                "裁剪线条", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
            if answer != wx.ID_YES:
                self.set_status("已取消。")
                return

        _log("[clip] 开始: 刀刃 {} 段 / 目标 {} 个 / delete={}".format(
            len(self.blade_polys), n_targets, delete))
        try:
            self.SetCursor(wx.Cursor(wx.CURSOR_WAIT))
            try:
                items, stats = core.clip_items(board, self.blade_polys,
                                               self.targets, delete)
            finally:
                self.SetCursor(wx.NullCursor)
            _log("[clip] clip_items 返回: cut={} pieces={} elapsed={:.3f}s".format(
                stats.get("cut", -1), stats.get("pieces", -1),
                stats.get("elapsed", -1.0)))
        except core.BoardEdgeError as exc:
            self.set_status(str(exc).replace("\n", " "), warn=True)
            _msg(self, str(exc), "裁剪线条", wx.OK | wx.ICON_WARNING)
            return
        except Exception as exc:
            tb = traceback.format_exc()
            _log("[err] clip_items 崩溃:\n{}".format(tb))
            msg = "裁剪失败：{}".format(exc)
            self.set_status(msg, warn=True)
            _msg(self, msg, "裁剪线条", wx.OK | wx.ICON_ERROR)
            return

        if self.chk_del_blade.GetValue():
            _log("[clip] 删除刀刃图形: {} 个".format(len(self.blade)))
            for item in self.blade:
                try:
                    board.Remove(item)
                except Exception:
                    pass
            _log("[clip] 刀刃图形删除完成")

        try:
            pcbnew.GetCurrentSelection().clear()
        except Exception:
            pass

        self.last_items = items
        self.clip_mutated = list(stats.get("mutated", []))
        self.blade = []
        self.blade_polys = []
        self.blade_closed = False
        self.targets = []
        self.lbl_blade.SetLabel("未拾取")
        self.lbl_targets.SetLabel("未拾取")
        self._sync_clip()

        if stats["cut"] == 0:
            self.set_status(
                "没有线条与刀刃相交（处理 {} 个对象，跳过 {} 个）。".format(
                    stats["cut"] + stats["skipped"], stats["skipped"]),
                warn=True)
        else:
            self.set_status("已切断 {} 根 / 条 → 生成 {} 段（{:.2f}s）。".format(
                stats["cut"], stats["pieces"], stats.get("elapsed", 0.0)))

        # 刷新放到事件队列里做：让按钮事件先走完（KiCad 处理完 mouse-up
        # 与 Selection 更新），再重绘视图。pcbnew.Refresh() 是 C 层同步
        # 重绘，在按钮处理函数里直接调用时若视图状态未更新会卡死。
        _log("[clip] 提交 wx.CallAfter 刷新，按钮处理函数即将返回")
        wx.CallAfter(self._do_refresh)

    def _do_refresh(self):
        """延迟到事件队列再刷新视图（pcbnew.Refresh 是 C 层同步重绘）。"""
        _log("[clip] wx.CallAfter 进入 → 调用 pcbnew.Refresh()")
        try:
            pcbnew.Refresh()
            _log("[clip] Refresh 返回，全部完成")
        except Exception:
            _log("[err] Refresh 失败:\n" + traceback.format_exc())

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------
    @_guard
    def on_generate(self, event):
        board = pcbnew.GetBoard()
        cfg = self._collect_config()
        save_config(cfg)

        source = cfg["source"]
        selection = list(self.selection) if source == "selection" else None

        if source == "selection" and not selection:
            dlg = wx.MessageDialog(
                self,
                "还没拾取来源图形。\n\n"
                "请在 PCB 编辑器里框选要描画的图形，然后点「拾取当前选中图形」；\n"
                "或把「来源」改成「板框」。",
                "描画工具", wx.OK | wx.ICON_INFORMATION)
            try:
                dlg.ShowModal()
            finally:
                dlg.Destroy()
            return

        layer_attr = cfg["layer"]
        layers = self._target_layers(layer_attr)
        corner = cfg["corner"]

        try:
            if layer_attr == "Edge_Cuts" and not cfg["replace_edge_cuts"]:
                dlg = wx.MessageDialog(
                    self,
                    "目标层是 Edge.Cuts，但未勾选「替换现有板框」。\n"
                    "叠加图形会让板框变成非法形状，确定继续吗？",
                    "描画工具", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
                try:
                    if dlg.ShowModal() != wx.ID_YES:
                        return
                finally:
                    dlg.Destroy()

            if cfg["mode"] == "outline":
                items = core.generate_outline(
                    board, layers, cfg["offset_mm"], corner,
                    line_width_mm=cfg["line_width_mm"],
                    replace_edge_cuts=cfg["replace_edge_cuts"],
                    source=source, selection=selection,
                    discrete=cfg["discrete"])
            else:
                items = core.generate_band(
                    board, layers, cfg["band_mm"], cfg["inset_mm"], corner,
                    gap_boxes_mm=self.gap_boxes,
                    step_mm=cfg["step_mm"], tol_mm=cfg["tol_mm"],
                    source=source, selection=selection,
                    discrete=cfg["discrete"])
        except core.BoardEdgeError as exc:
            self.set_status(str(exc).replace("\n", " "), warn=True)
            _msg(self, str(exc), "描画工具", wx.OK | wx.ICON_WARNING)
            return
        except Exception as exc:
            tb = traceback.format_exc()
            _log("[err] generate 崩溃:\n{}".format(tb))
            msg = "生成失败：{}".format(exc)
            self.set_status(msg, warn=True)
            _msg(self, msg, "描画工具", wx.OK | wx.ICON_ERROR)
            return

        self.last_items = items
        self.clip_mutated = []
        wx.CallAfter(self._do_refresh)
        names = [core.layer_label(layer_attr)]
        if self.chk_mirror.GetValue() and layer_attr in core.MIRROR_LAYERS:
            names.append(core.layer_label(core.MIRROR_LAYERS[layer_attr]))
        self.set_status("已生成 {} 个图形 → {}".format(len(items),
                                             ", ".join(names)))

    @_guard
    def on_undo(self, event):
        if not self.last_items:
            self.set_status("没有可撤销的生成结果（重启插件后失效）。", warn=True)
            return
        board = pcbnew.GetBoard()

        # 裁剪时首段是**原地改造的原对象**，不能删——否则会留下悬空指针
        # （KiCad Selection 工具持有它 → 刷新视图卡死）。
        # 只删新建出来的段，再把原对象恢复成裁剪前的形状。
        mutated = getattr(self, "clip_mutated", [])
        mutated_ids = set(id(m[0]) for m in mutated)
        for item in self.last_items:
            if id(item) in mutated_ids:
                continue
            try:
                board.Remove(item)
            except Exception:
                pass
        for item, info in mutated:
            core.restore_shape(item, info)

        removed = len(self.last_items) - len(mutated)
        self.last_items = []
        self.clip_mutated = []
        _log("[undo] 删除 {} 段 / 恢复 {} 个原对象".format(
            removed, len(mutated)))
        wx.CallAfter(self._do_refresh)
        self.set_status("已撤销（删除 {} 段、恢复 {} 条原线条）。".format(
            removed, len(mutated)))

    def on_close(self, event):
        try:
            save_config(self._collect_config())
        except Exception:
            _log("[err] on_close save_config 失败:\n" + traceback.format_exc())
        try:
            self.Destroy()
        except Exception:
            pass


class BoardEdgeToolsPlugin(_ActionPlugin):
    """工具栏按钮入口。"""

    def defaults(self):
        self.name = "描画 / 裁剪工具（偏移 / 露铜带 / 切断）"
        self.category = "板边"
        self.description = "对选中图形或板框做等距描画（可选离散图线）、分段露铜带，或把线条在刀刃处切断"
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(PLUGIN_DIR, "icon.png")
        self.category_name = self.category

    def Run(self):
        _log("[run] Run() 开始")
        try:
            parent = None
            try:
                app = wx.GetApp()
                if app is not None:
                    parent = app.GetTopWindow()
            except Exception:
                parent = None
            frame = MainFrame(parent)
            frame.Show()
            frame.Raise()
            _log("[run] 窗口已显示")
        except Exception:
            tb = traceback.format_exc()
            _log("[err] Run() 崩溃:\n" + tb)
            try:
                _msg(None, tb[-1200:], "描画 / 裁剪工具启动失败",
                     wx.OK | wx.ICON_ERROR)
            except Exception:
                pass
