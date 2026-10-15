"""几何核心：来源图形提取、等距偏移（描画）、分段输出。

支持两种**来源**（source）：

* ``board``     —— 板框（Edge.Cuts 上所有闭合轮廓，含板内挖槽）
* ``selection`` —— PCB 上当前框选的图形（线段 / 圆弧 / 多边形 / 矩形 /
                   圆 / 敷铜轮廓 / 封装包围盒）

流程：提取来源 → 连成闭合环 → Inflate 等距偏移 → 输出到目标层。

KiCad 10.0.x 已验证的 API 要点（与旧版不同，勿改）：
  * ``SHAPE_POLY_SET.Inflate(aAmount, CORNER_STRATEGY, aMaxError)`` 三参数
  * 图形类型枚举是 ``pcbnew.S_POLYGON``（旧名 ``SHAPE_T_POLYGON`` 已废弃）
  * ``BOARD.GetBoardPolygonOutlines(poly, aInferOutlineIfNecessary)``
  * ``PCB_SHAPE``：``SetShape / SetPolyPoints / SetStart / SetEnd / SetWidth /
    SetLayer / SetFilled``
"""

import math

try:
    import pcbnew
except ImportError:  # 便于在非 KiCad 环境下做静态检查
    pcbnew = None

# KiCad 内部单位：1 mm = 1e6 nm
IU_PER_MM = 1000000


class BoardEdgeError(Exception):
    """插件自己的可预期错误，GUI 会弹窗显示。"""


# 诊断日志钩子：plugin.py 会注入带时间戳的 _log。
# core 保持零 GUI 依赖，日志函数由外部注入。
LOG_FN = None


def set_log_fn(fn):
    global LOG_FN
    LOG_FN = fn


def _clog(msg):
    """core 内部诊断日志：没注入钩子时静默。"""
    if LOG_FN is not None:
        try:
            LOG_FN("[core] " + msg)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 单位换算
# ---------------------------------------------------------------------------
def mm2iu(value_mm):
    return int(round(float(value_mm) * IU_PER_MM))


def iu2mm(value_iu):
    return float(value_iu) / IU_PER_MM


def _pt(v):
    return (int(v.x), int(v.y))


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ---------------------------------------------------------------------------
# 可选参数表
# ---------------------------------------------------------------------------
CORNER_STRATEGIES = [
    ("倒角（全部角）", "CORNER_STRATEGY_CHAMFER_ALL_CORNERS"),
    ("圆角（全部角）", "CORNER_STRATEGY_ROUND_ALL_CORNERS"),
    ("倒角（仅锐角）", "CORNER_STRATEGY_CHAMFER_ACUTE_CORNERS"),
    ("圆角（仅锐角）", "CORNER_STRATEGY_ROUND_ACUTE_CORNERS"),
    ("保留尖角", "CORNER_STRATEGY_ALLOW_ACUTE_CORNERS"),
]

LAYER_CHOICES = [
    ("F.Cu", "F_Cu"),
    ("B.Cu", "B_Cu"),
    ("F.Mask（阻焊开窗）", "F_Mask"),
    ("B.Mask（阻焊开窗）", "B_Mask"),
    ("F.Paste", "F_Paste"),
    ("B.Paste", "B_Paste"),
    ("F.SilkS", "F_SilkS"),
    ("B.SilkS", "B_SilkS"),
    ("Edge.Cuts（替换板框）", "Edge_Cuts"),
    ("Dwgs.User", "Dwgs_User"),
    ("Cmts.User", "Cmts_User"),
    ("Margin", "Margin"),
    ("User.1", "User_1"),
    ("User.2", "User_2"),
    ("User.3", "User_3"),
    ("User.4", "User_4"),
    ("User.5", "User_5"),
    ("User.6", "User_6"),
    ("User.7", "User_7"),
    ("User.8", "User_8"),
    ("User.9（默认标记层）", "User_9"),
]

MIRROR_LAYERS = {
    "F_Cu": "B_Cu", "B_Cu": "F_Cu",
    "F_Mask": "B_Mask", "B_Mask": "F_Mask",
    "F_Paste": "B_Paste", "B_Paste": "F_Paste",
    "F_SilkS": "B_SilkS", "B_SilkS": "F_SilkS",
}


def layer_id(attr_name):
    return getattr(pcbnew, attr_name)


def corner_id(attr_name):
    return getattr(pcbnew, attr_name)


def layer_label(attr_name):
    for label, attr in LAYER_CHOICES:
        if attr == attr_name:
            return label
    return attr_name


# ---------------------------------------------------------------------------
# 来源一：板框
# ---------------------------------------------------------------------------
def fetch_board_outline(board, allow_infer=False):
    poly = pcbnew.SHAPE_POLY_SET()
    if not board.GetBoardPolygonOutlines(poly, bool(allow_infer)):
        raise BoardEdgeError(
            "板框无效：Edge.Cuts 上的图形不闭合。\n"
            "请先跑一次 DRC 修复，或用 Heal Shapes 工具补齐缺口。")
    if poly.OutlineCount() == 0:
        raise BoardEdgeError("当前 PCB 没有可用的板框（Edge.Cuts 为空）。")
    return poly


# ---------------------------------------------------------------------------
# 来源二：选中图形
# ---------------------------------------------------------------------------
def _circumcenter(a, b, c):
    """三点求圆心；共线返回 (None, 0)。"""
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None, 0.0
    ux = ((ax * ax + ay * ay) * (by - cy) +
          (bx * bx + by * by) * (cy - ay) +
          (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) +
          (bx * bx + by * by) * (ax - cx) +
          (cx * cx + cy * cy) * (bx - ax)) / d
    return (ux, uy), math.hypot(ax - ux, ay - uy)


def arc_to_polyline(start, mid, end, segments=24):
    """起点 / 弧中点 / 终点 → 采样折线（KiCad 内部单位）。"""
    center, _ = _circumcenter(start, mid, end)
    if center is None:
        return [start, end]

    cx, cy = center
    r = _dist(start, center)
    a0 = math.atan2(start[1] - cy, start[0] - cx)
    am = math.atan2(mid[1] - cy, mid[0] - cx)
    a1 = math.atan2(end[1] - cy, end[0] - cx)

    def norm(x):
        return (x + 2 * math.pi) % (2 * math.pi)

    # 判断弧是从起点逆时针还是顺时针扫到终点（以弧中点为判据）
    if norm(am - a0) <= norm(a1 - a0) + 1e-12:
        sweep = norm(a1 - a0)
    else:
        sweep = -norm(a0 - a1)

    pts = []
    for i in range(segments + 1):
        ang = a0 + sweep * (float(i) / segments)
        pts.append((int(round(cx + r * math.cos(ang))),
                    int(round(cy + r * math.sin(ang)))))
    return pts


def _close_ring(pts):
    """给闭合环显式补回首点。

    KiCad 的闭合来源（矩形 / 圆 / 多边形 / 敷铜 / 封装）首末点**不重合**，
    靠隐式闭合。裁剪时若不补，最后一条闭合边（末点→首点）会被漏切；
    描画时若不补，``chain_polylines`` 会当成开放折线丢掉。
    """
    pts = list(pts)
    if len(pts) < 3:
        return pts
    if pts[0] == pts[-1]:
        return pts
    pts.append(pts[0])
    return pts


def _polyset_to_polylines(poly):
    """SHAPE_POLY_SET → 折线列表（外轮廓 + 挖槽都取，显式闭合）。"""
    out = []
    for oi in range(poly.OutlineCount()):
        chain = poly.COutline(oi)
        pts = [_pt(chain.CPoint(i)) for i in range(chain.PointCount())]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) >= 3:
            pts.append(pts[0])          # 显式闭合，chain_polylines 才认
            out.append(pts)
        for hi in range(poly.HoleCount(oi)):
            hole = poly.CHole(oi, hi)
            pts = [_pt(hole.CPoint(i)) for i in range(hole.PointCount())]
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            if len(pts) >= 3:
                pts.append(pts[0])
                out.append(pts)
    return out


def shape_to_polyline(item, arc_segments=24):
    """图形对象 → 折线**列表**（一条对象可能产出多条折线，如带挖槽的敷铜）。

    不支持的类型返回空列表。支持：
    * ``ZONE`` 敷铜 —— 取外轮廓 + 挖槽
    * ``FOOTPRINT`` 封装 —— 取包围盒矩形
    * ``PCB_SHAPE``：segment / arc / polygon / rect / circle / curve(贝塞尔)
    * ``PCB_TRACK`` / ``PCB_ARC`` 走线
    * 尺寸标注（PCB_DIMENSION_BASE）不支持，返回空
    """
    try:
        # 敷铜（ZONE）：外轮廓 + 挖槽全部描出
        if isinstance(item, pcbnew.ZONE):
            return _polyset_to_polylines(item.Outline())

        # 封装：取包围盒
        if isinstance(item, pcbnew.FOOTPRINT):
            box = item.GetBoundingBox()
            x0, y0 = int(box.GetX()), int(box.GetY())
            x1 = int(box.GetX() + box.GetWidth())
            y1 = int(box.GetY() + box.GetHeight())
            return [_close_ring([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])]

        # 尺寸标注不支持
        if isinstance(item, pcbnew.PCB_DIMENSION_BASE):
            return []

        # 走线：注意 PCB_TRACK **没有** GetShape()，必须先按子类判断
        if isinstance(item, pcbnew.PCB_VIA):
            # 过孔没有可用轮廓（起终点相同），跳过
            return []
        if isinstance(item, pcbnew.PCB_ARC):
            return [arc_to_polyline(_pt(item.GetStart()),
                                    _pt(item.GetMid()),
                                    _pt(item.GetEnd()), arc_segments)]
        if isinstance(item, pcbnew.PCB_TRACK):
            return [[_pt(item.GetStart()), _pt(item.GetEnd())]]

        st = item.GetShape()
        if st == pcbnew.S_SEGMENT:
            return [[_pt(item.GetStart()), _pt(item.GetEnd())]]
        if st == pcbnew.S_ARC:
            return [arc_to_polyline(_pt(item.GetStart()),
                                    _pt(item.GetArcMid()),
                                    _pt(item.GetEnd()), arc_segments)]
        if st == pcbnew.S_RECT:
            p0 = _pt(item.GetStart())
            p1 = _pt(item.GetEnd())
            return [_close_ring([p0, (p1[0], p0[1]), p1, (p0[0], p1[1])])]
        if st == pcbnew.S_CIRCLE:
            c = _pt(item.GetCenter())
            r = int(item.GetRadius())
            return [_close_ring([
                (int(round(c[0] + r * math.cos(2 * math.pi * i / arc_segments))),
                 int(round(c[1] + r * math.sin(2 * math.pi * i / arc_segments))))
                for i in range(arc_segments)])]
        if st == pcbnew.S_CURVE:
            # 贝塞尔曲线：优先用 KiCad 自带的离散结果
            try:
                chain = item.GetBezierPoints()
                pts = [_pt(chain.CPoint(i)) for i in range(chain.PointCount())]
                if len(pts) >= 2:
                    return [pts]
            except Exception:
                pass
            p0 = _pt(item.GetStart())
            c1 = _pt(item.GetBezierC1())
            c2 = _pt(item.GetBezierC2())
            p3 = _pt(item.GetEnd())
            pts = []
            for i in range(arc_segments + 1):
                t = float(i) / arc_segments
                u = 1.0 - t
                x = (u * u * u * p0[0] + 3 * u * u * t * c1[0] +
                     3 * u * t * t * c2[0] + t * t * t * p3[0])
                y = (u * u * u * p0[1] + 3 * u * u * t * c1[1] +
                     3 * u * t * t * c2[1] + t * t * t * p3[1])
                pts.append((int(round(x)), int(round(y))))
            return [pts]
        if st == pcbnew.S_POLYGON:
            chain = item.GetPolyShape().COutline(0)
            out = [_pt(chain.CPoint(i)) for i in range(chain.PointCount())]
            if len(out) > 1 and out[0] == out[-1]:
                out = out[:-1]
            if len(out) < 3:
                return []
            return [_close_ring(out)]
    except Exception:
        return []
    return []


def collect_selection_polylines(selection):
    """从选择集里取出所有可描画的折线。

    Returns:
        (polylines, skipped) —— skipped 是 {类型名: 个数}，用于报错提示。
    """
    polys, skipped = [], {}
    for item in selection:
        result = shape_to_polyline(item)
        if result:
            polys.extend(result)
        else:
            name = type(item).__name__
            skipped[name] = skipped.get(name, 0) + 1
    return polys, skipped


def chain_polylines(polylines, eps_mm=0.002):
    """把零散折线按端点拼接成闭合环（首尾点不重复存）。

    拼不成闭环的段会被丢弃；一条都拼不出来时报错。
    """
    eps = mm2iu(eps_mm)
    remaining = [list(p) for p in polylines]
    contours = []

    while remaining:
        current = remaining.pop(0)
        extended = True
        while extended:
            extended = False
            for i, seg in enumerate(remaining):
                if _dist(current[-1], seg[0]) <= eps:
                    current = current + seg[1:]
                elif _dist(current[-1], seg[-1]) <= eps:
                    current = current + list(reversed(seg))[1:]
                elif _dist(current[0], seg[-1]) <= eps:
                    current = seg[:-1] + current
                elif _dist(current[0], seg[0]) <= eps:
                    current = list(reversed(seg))[:-1] + current
                else:
                    continue
                remaining.pop(i)
                extended = True
                break

        if len(current) >= 3 and _dist(current[0], current[-1]) <= eps:
            contours.append(current[:-1])

    if not contours:
        raise BoardEdgeError(
            "选中的图形拼不出闭合轮廓。\n"
            "请确认框选的是一条完整闭合的图形（线段端点需重合），"
            "或改用「来源 = 板框」。")
    return contours


# ---------------------------------------------------------------------------
# 偏移
# ---------------------------------------------------------------------------
def build_poly_from_contours(contours):
    poly = pcbnew.SHAPE_POLY_SET()
    for contour in contours:
        if len(contour) < 3:
            continue
        poly.NewOutline()
        for x, y in contour:
            poly.Append(int(x), int(y))
    if poly.OutlineCount() == 0:
        raise BoardEdgeError("没有可用于描画的轮廓。")
    return poly


def offset_poly(poly, offset_mm, corner_attr, max_error_mm=0.001):
    result = pcbnew.SHAPE_POLY_SET()
    for oi in range(poly.OutlineCount()):
        chain = poly.COutline(oi)
        result.NewOutline()
        for i in range(chain.PointCount()):
            p = chain.CPoint(i)
            result.Append(int(p.x), int(p.y))
        for hi in range(poly.HoleCount(oi)):
            hole = poly.CHole(oi, hi)
            result.NewHole()
            for i in range(hole.PointCount()):
                p = hole.CPoint(i)
                result.Append(int(p.x), int(p.y))
    result.Inflate(mm2iu(offset_mm), corner_id(corner_attr),
                   mm2iu(max_error_mm))
    if result.OutlineCount() == 0:
        raise BoardEdgeError("偏移量过大，偏移后的轮廓已消失。请减小偏移量。")
    return result


def outline_points(poly, oi):
    chain = poly.COutline(oi)
    n = chain.PointCount()
    pts = [(int(chain.CPoint(i).x), int(chain.CPoint(i).y)) for i in range(n)]
    if n > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def all_contours(poly):
    """取出所有闭合环（外轮廓 + 板内挖槽）。"""
    contours = []
    for oi in range(poly.OutlineCount()):
        contour = outline_points(poly, oi)
        if len(contour) >= 3:
            contours.append(contour)
        for hi in range(poly.HoleCount(oi)):
            hole = poly.CHole(oi, hi)
            n = hole.PointCount()
            pts = [(int(hole.CPoint(i).x), int(hole.CPoint(i).y))
                   for i in range(n)]
            if n > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            if len(pts) >= 3:
                contours.append(pts)
    return contours


def outline_length_mm(poly):
    total = 0
    for oi in range(poly.OutlineCount()):
        total += poly.COutline(oi).Length()
    return iu2mm(total)


# ---------------------------------------------------------------------------
# 离散 / 切分 / 简化
# ---------------------------------------------------------------------------
def resample(points, step_mm):
    step_iu = max(1, mm2iu(step_mm))
    dense = []
    n = len(points)
    for i in range(n):
        ax, ay = points[i]
        bx, by = points[(i + 1) % n]
        seg = _dist((ax, ay), (bx, by))
        count = max(1, int(seg / step_iu))
        for k in range(count):
            t = float(k) / count
            dense.append((ax + (bx - ax) * t, ay + (by - ay) * t))
    return dense


def _in_boxes(x_iu, y_iu, boxes_mm):
    x, y = iu2mm(x_iu), iu2mm(y_iu)
    for box in boxes_mm:
        x0, y0, x1, y1 = box
        if min(x0, x1) <= x <= max(x0, x1) and min(y0, y1) <= y <= max(y0, y1):
            return True
    return False


def split_runs(points, boxes_mm, step_mm=0.3):
    if not boxes_mm:
        return [list(points)], True

    dense = resample(points, step_mm)
    dense.append(dense[0])

    runs, current = [], []
    for x, y in dense:
        if _in_boxes(x, y, boxes_mm):
            if len(current) > 1:
                runs.append(current)
            current = []
        else:
            current.append((x, y))
    if len(current) > 1:
        runs.append(current)

    if not runs:
        raise BoardEdgeError("断开区覆盖了整圈轮廓，没有可生成的部分。")
    return runs, False


def simplify(points, tol_mm=0.02):
    """Ramer–Douglas–Peucker：合并近似共线点，减少图形数量。"""
    tol_iu = max(1, mm2iu(tol_mm))
    if len(points) < 3:
        return list(points)

    ax, ay = points[0]
    bx, by = points[-1]
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy) or 1.0

    dmax, index = 0.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = abs(dy * (px - ax) - dx * (py - ay)) / length
        if d > dmax:
            dmax, index = d, i

    if dmax > tol_iu:
        return simplify(points[:index + 1], tol_mm)[:-1] + \
           simplify(points[index:], tol_mm)
    return [points[0], points[-1]]


# ---------------------------------------------------------------------------
# 生成图形
# ---------------------------------------------------------------------------
def build_items(board, layer, runs, closed, width_mm, discrete=False):
    """把若干段折线写成 PCB 图形。

    被切断的段**必须**用 S_SEGMENT 逐条输出：多边形会自动首尾相连，
    缺口处会多出一条露铜带。

    Args:
        discrete: True 时**即使是闭合环也逐段输出独立 S_SEGMENT**
            （等同 KiCad「从选区创建图线」的结果），每段可单独删减；
            False 时闭合环输出为一条 S_POLYGON 多边形（默认）。
    """
    width_iu = mm2iu(width_mm)
    items = []

    if closed and len(runs) == 1 and not discrete:
        pts = runs[0]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) < 3:
            raise BoardEdgeError("偏移后的轮廓点数不足，无法构成多边形。")
        item = pcbnew.PCB_SHAPE(board)
        item.SetShape(pcbnew.S_POLYGON)
        item.SetPolyPoints([pcbnew.VECTOR2I(int(x), int(y)) for x, y in pts])
        item.SetWidth(width_iu)
        item.SetFilled(False)
        item.SetLayer(layer)
        board.Add(item)
        items.append(item)
        return items

    if closed and discrete:
        # 离散输出闭合环：显式补回首点，最后一段才能闭合
        runs = [r if (len(r) > 1 and r[0] == r[-1]) else list(r) + [r[0]]
                for r in runs]

    for run in runs:
        # 闭合环不能过 simplify：RDP 基准线是首尾两点，闭合时两点重合，
        # 所有点到"线"的距离恒为 0 → 整圈塌缩成 2 个重合点 → 输出为空。
        # 离散闭合输出直接用原始顶点（偏移结果本身点不多，无需合并）。
        pts = run if closed else simplify(run)
        for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
            if (int(x0), int(y0)) == (int(x1), int(y1)):
                continue
            item = pcbnew.PCB_SHAPE(board)
            item.SetShape(pcbnew.S_SEGMENT)
            item.SetStart(pcbnew.VECTOR2I(int(x0), int(y0)))
            item.SetEnd(pcbnew.VECTOR2I(int(x1), int(y1)))
            item.SetWidth(width_iu)
            item.SetLayer(layer)
            board.Add(item)
            items.append(item)
    return items


def delete_items(board, items):
    for item in list(items):
        try:
            board.Remove(item)
        except Exception:
            pass
    return True


def remove_layer_shapes(board, layer):
    for item in list(board.GetDrawings()):
        try:
            if item.GetLayer() == layer:
                board.Remove(item)
        except Exception:
            continue


def get_source_contours(board, source="selection", selection=None):
    """按来源取出闭合轮廓。"""
    if source == "board":
        return all_contours(fetch_board_outline(board))

    selection = list(selection or [])
    polys, skipped = collect_selection_polylines(selection)
    if not polys:
        detail = ", ".join("{}×{}".format(k, v)
                           for k, v in sorted(skipped.items())) or "无对象"
        raise BoardEdgeError(
            "选中的 {} 个对象里没有可描画的图形（{}）。\n"
            "支持：线条 / 圆弧 / 多边形 / 矩形 / 圆 / 贝塞尔曲线 / "
            "走线 / 敷铜轮廓 / 封装。".format(len(selection), detail))
    return chain_polylines(polys)


# ---------------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 裁剪：把线条在交点处切断
# ---------------------------------------------------------------------------
def _seg_intersection(a1, a2, b1, b2):
    """两线段求交，返回 (t, (x, y))；t 是交点在 a1→a2 上的参数。无交点返回 None。"""
    x1, y1 = float(a1[0]), float(a1[1])
    x2, y2 = float(a2[0]), float(a2[1])
    x3, y3 = float(b1[0]), float(b1[1])
    x4, y4 = float(b2[0]), float(b2[1])
    dx1, dy1 = x2 - x1, y2 - y1
    dx2, dy2 = x4 - x3, y4 - y3
    det = -dx1 * dy2 + dx2 * dy1
    if abs(det) < 1e-12:
        return None
    t = (-(x3 - x1) * dy2 + dx2 * (y3 - y1)) / det
    u = (dx1 * (y3 - y1) - dy1 * (x3 - x1)) / det
    if -1e-9 <= t <= 1.0 + 1e-9 and -1e-9 <= u <= 1.0 + 1e-9:
        t = min(1.0, max(0.0, t))
        return (t, (x1 + t * dx1, y1 + t * dy1))
    return None


def _point_in_rings(point, rings):
    """偶奇规则：把所有环（外轮廓 + 挖槽）一起算穿越次数。"""
    x, y = point
    inside = False
    for ring in rings:
        n = len(ring)
        if n < 3:
            continue
        for i in range(n):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % n]
            if (y1 > y) != (y2 > y):
                x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if x < x_cross:
                    inside = not inside
    return inside


def _polyline_length(points):
    return sum(_dist(points[i], points[i + 1])
               for i in range(len(points) - 1))


def _polyline_midpoint(points):
    """折线弧长中点（不是首尾连线中点）。

    圆环上的一段弧，首尾连线中点可能落在环外，用它判内外会出错。
    """
    if len(points) == 0:
        return None
    if len(points) == 1:
        return points[0]
    total = _polyline_length(points)
    if total <= 0:
        return points[0]
    half = total / 2.0
    acc = 0.0
    for i in range(len(points) - 1):
        d = _dist(points[i], points[i + 1])
        if acc + d >= half:
            t = (half - acc) / (d or 1.0)
            return (points[i][0] + (points[i + 1][0] - points[i][0]) * t,
                    points[i][1] + (points[i + 1][1] - points[i][1]) * t)
        acc += d
    return points[-1]


def is_closed_ring(points, eps_mm=0.002):
    """判断一条折线是不是闭合环（首末点重合）。"""
    if len(points) < 3:
        return False
    return _dist(points[0], points[-1]) <= mm2iu(eps_mm)


def collect_blade(selection):
    """取刀刃：选中对象 → 折线列表（开放 / 闭合都要）。

    刀刃可以是开放线（一条直线刀），所以**不能**走 chain_polylines。
    返回 (polylines, closed, skipped)。
    """
    polys, skipped = collect_selection_polylines(selection)
    if not polys:
        return [], False, skipped
    closed = all(is_closed_ring(p) for p in polys)
    return polys, closed, skipped


def split_polyline(points, blade_segs, min_len_mm=0.001, index=None):
    """用刀刃线段把一条折线切断，返回若干条子折线。

    子折线**保留原始顶点顺序**（切点之间的顶点一个都不能丢，否则周长会缩水）。
    没有交点时原样返回（一条）。

    Args:
        index: 可选的 :class:`SegmentGrid`。批量裁剪时**务必**传入，
               否则每条目标都会重建一次索引。
    """
    if len(points) < 2:
        return [list(points)]

    grid = index if index is not None else SegmentGrid(blade_segs)

    # 每条线段上可能有多个交点（多把刀），按参数 t 排序
    cuts_by_seg = {}
    for idx in range(len(points) - 1):
        a1, a2 = points[idx], points[idx + 1]
        hits = []
        for blade in grid.query(a1, a2):
            hit = _seg_intersection(a1, a2, blade[0], blade[1])
            if hit is not None:
                hits.append(hit)
        if hits:
            hits.sort(key=lambda h: h[0])
            cuts_by_seg[idx] = hits

    if not cuts_by_seg:
        return [list(points)]

    pieces = []
    current = [points[0]]
    for idx in range(len(points) - 1):
        if idx in cuts_by_seg:
            for _t, pt in cuts_by_seg[idx]:
                current.append(pt)
                if len(current) > 1:
                    pieces.append(current)
                current = [pt]
        current.append(points[idx + 1])
    if len(current) > 1:
        pieces.append(current)

    limit = mm2iu(min_len_mm)
    out = []
    for piece in pieces:
        # 去掉相邻重复点（切点正好落在顶点上时会重复）
        dedup = [piece[0]]
        for q in piece[1:]:
            if q != dedup[-1]:
                dedup.append(q)
        if len(dedup) >= 2 and _polyline_length(dedup) >= limit:
            out.append(dedup)
    return out


def blade_segments(contours):
    """轮廓 → 线段列表（含闭合段）。"""
    segs = []
    for ring in contours:
        n = len(ring)
        for i in range(n):
            segs.append((ring[i], ring[(i + 1) % n]))
    return segs


class SegmentGrid(object):
    """刀刃线段的均匀网格空间索引。

    直接暴力求交是 ``O(刀刃段数 × 目标点数)``。刀刃一复杂（敷铜轮廓、
    圆采样、密集圆弧）就轻松上千万次，KiCad 主线程会长时间无响应
    ——表现就是"点了执行裁剪就卡死"。

    建索引后每段只跟同格子的少数候选求交，近似 ``O(n + m)``。
    """

    def __init__(self, segments, cell_factor=1.0):
        self.segments = list(segments)
        self.buckets = {}
        if not self.segments:
            self.min_x = self.min_y = 0
            self.cell = float(IU_PER_MM)
            return

        xs, ys = [], []
        max_len = 0.0
        for a, b in self.segments:
            xs.append(a[0])
            xs.append(b[0])
            ys.append(a[1])
            ys.append(b[1])
            max_len = max(max_len, _dist(a, b))

        self.min_x, self.min_y = min(xs), min(ys)
        span_x = float(max(xs) - self.min_x)
        span_y = float(max(ys) - self.min_y)

        # 目标：整块 bbox 大致切成 n 格
        n = float(len(self.segments))
        area = max(span_x * span_y, 1.0)
        cell = math.sqrt(area / n) * max(0.05, float(cell_factor))
        # 防止单条超长线段横跨海量格子：任何线段最多横跨 64 格（每维）
        cell = max(cell, max_len / 64.0, 1.0)
        self.cell = cell

        for idx, (a, b) in enumerate(self.segments):
            for key in self._keys(a, b):
                self.buckets.setdefault(key, []).append(idx)

    def _keys(self, a, b):
        c = self.cell
        x0 = int(math.floor((min(a[0], b[0]) - self.min_x) / c))
        x1 = int(math.floor((max(a[0], b[0]) - self.min_x) / c))
        y0 = int(math.floor((min(a[1], b[1]) - self.min_y) / c))
        y1 = int(math.floor((max(a[1], b[1]) - self.min_y) / c))
        for i in range(x0, x1 + 1):
            for j in range(y0, y1 + 1):
                yield (i, j)

    def query(self, a, b):
        """返回与目标线段 bbox 有重叠的候选刀刃线段（去重）。"""
        seen = set()
        out = []
        for key in self._keys(a, b):
            for idx in self.buckets.get(key, ()):
                if idx not in seen:
                    seen.add(idx)
                    out.append(self.segments[idx])
        return out


# 单次裁剪允许生成的线段上限，防止误框选整板后长时间无响应
MAX_PIECES = 20000


def clip_items(board, blade_contours, target_items, delete="none",
               max_pieces=MAX_PIECES):
    """把选中的线条在刀刃交点处切断（默认保留两侧，不删除）。

    Args:
        board: BOARD
        blade_contours: 刀刃轮廓（开放折线或闭合环都可以）→ 展开成线段
        target_items: 被裁的线条（PCB_TRACK / PCB_ARC / PCB_SHAPE）
        delete: "none" 仅切断 / "inside" 删掉边界内 / "outside" 删掉边界外
        max_pieces: 生成线段上限，超过直接报错放弃（保护主线程）

    Returns:
        (新生成的图形对象列表, 统计 dict)。原对象已被删除。
    """
    import time as _time

    started = _time.time()
    segs = blade_segments(blade_contours)
    if not segs:
        raise BoardEdgeError("刀刃为空：没有可用于裁剪的线段。")

    _clog("入口: 刀刃 {} 段折线 -> {} 线段 / 目标 {} 个 / delete={}".format(
        len(blade_contours), len(segs), len(target_items), delete))

    grid = SegmentGrid(segs)
    _clog("索引建立 {:.3f}s (cell={:.0f}IU)".format(
        _time.time() - started, grid.cell))

    # mutated: [(被原地改造的原对象, 改造前的几何快照)]，供撤销恢复
    stats = {"cut": 0, "skipped": 0, "pieces": 0, "elapsed": 0.0,
             "mutated": []}
    pieces = []
    for ti, item in enumerate(target_items):
        t_item = _time.time()
        # 敷铜 / 封装 / 过孔不参与裁剪：删掉原对象换成线段是破坏性的
        try:
            if isinstance(item, (pcbnew.ZONE, pcbnew.FOOTPRINT,
                                 pcbnew.PCB_VIA)):
                stats["skipped"] += 1
                continue
        except Exception:
            stats["skipped"] += 1
            continue

        polys, _ = collect_selection_polylines([item])
        if not polys:
            stats["skipped"] += 1
            continue

        subs = []
        for poly in polys:
            subs.extend(split_polyline(poly, segs, index=grid))

        _clog("目标[{}] {} : {} 折线 / {} 顶点 -> {} 子段, {:.3f}s".format(
            ti, type(item).__name__, len(polys),
            sum(len(p) for p in polys), len(subs), _time.time() - t_item))

        if len(subs) <= 1:
            # 没交点，原样保留，不动它
            stats["skipped"] += 1
            continue

        if len(pieces) + sum(len(s) - 1 for s in subs) > max_pieces:
            stats["elapsed"] = _time.time() - started
            raise BoardEdgeError(
                "裁剪结果超过上限（{} 段）。\n\n"
                "通常是误框选了整块板子（含敷铜、密集走线）。\n"
                "请缩小选择范围，只框选真正要裁的线条后重试。".format(max_pieces))

        # 先筛掉要删除的子段
        keep = []
        for sub in subs:
            if delete != "none":
                mid = _polyline_midpoint(sub)
                inside = _point_in_rings(mid, blade_contours)
                if delete == "inside" and inside:
                    continue
                if delete == "outside" and not inside:
                    continue
            keep.append(sub)

        if not keep:
            # 整条都在要删的一侧：用户明确要求删除，只能 Remove
            _clog("目标[{}] 整条删除（delete={}）".format(ti, delete))
            try:
                board.Remove(item)
            except Exception:
                stats["skipped"] += 1
                continue
            stats["cut"] += 1
            continue

        seg_list = []
        for sub in keep:
            for i in range(len(sub) - 1):
                if _dist(sub[i], sub[i + 1]) > 0:
                    seg_list.append((sub[i], sub[i + 1]))
        if not seg_list:
            stats["skipped"] += 1
            continue

        # 首段复用原对象：原地改端点（绝不 Remove 被选中的对象）
        snap = _snapshot_shape(item)
        mutated = False
        for k, (a, b) in enumerate(seg_list):
            if k == 0 and _reshape_in_place(item, a, b):
                pieces.append(item)
                stats["mutated"].append((item, snap))
                mutated = True
            else:
                pieces.append(_new_piece(board, item, a, b))
        _clog("目标[{}] 首段{} 共 {} 段, {:.3f}s".format(
            ti, "原地改造" if mutated else "删除重建", len(seg_list),
            _time.time() - t_item))
        stats["cut"] += 1
    stats["pieces"] = len(pieces)
    stats["elapsed"] = _time.time() - started
    _clog("完成: cut={} skipped={} pieces={} 总耗时 {:.3f}s".format(
        stats["cut"], stats["skipped"], stats["pieces"], stats["elapsed"]))
    return pieces, stats


def _snapshot_shape(item):
    """记录对象改造前的几何，供撤销时恢复。"""
    info = {"shape": None, "start": None, "end": None, "poly": None}
    try:
        info["start"] = _pt(item.GetStart())
        info["end"] = _pt(item.GetEnd())
    except Exception:
        pass
    if isinstance(item, pcbnew.PCB_SHAPE):
        try:
            info["shape"] = int(item.GetShape())
        except Exception:
            pass
        try:
            if info["shape"] == pcbnew.S_POLYGON:
                info["poly"] = [_pt(p)
                                for p in item.GetPolyShape().Outline(0).CPoints()]
        except Exception:
            pass
    return info


def _reshape_in_place(item, a, b):
    """把原对象**原地**改成一段直线。改不了返回 False。

    ⚠️ 关键：被裁剪的对象通常是**当前选中**的。直接 ``board.Remove()``
    会让 KiCad 的 Selection 工具留下悬空指针，随后任何视图刷新
    （``pcbnew.Refresh()`` / 鼠标交互）都会卡死整个 KiCad。
    所以首段必须复用原对象（原地改端点 + 必要时改形状类型），
    只把多出来的段才新建。参考官方插件 CutTracksAtLine 的写法。
    """
    try:
        # 圆弧走线 PCB_ARC 无法原地改成直线段 → 交给调用方删除重建
        if isinstance(item, pcbnew.PCB_ARC):
            return False
        if isinstance(item, pcbnew.PCB_SHAPE):
            item.SetShape(pcbnew.S_SEGMENT)
        item.SetStart(pcbnew.VECTOR2I(int(round(a[0])), int(round(a[1]))))
        item.SetEnd(pcbnew.VECTOR2I(int(round(b[0])), int(round(b[1]))))
        return True
    except Exception:
        return False


def restore_shape(item, info):
    """撤销：把被改造过的对象恢复成原形状。"""
    try:
        if info.get("shape") is not None:
            item.SetShape(info["shape"])
        if info.get("shape") == pcbnew.S_POLYGON and info.get("poly"):
            item.SetPolyPoints([pcbnew.VECTOR2I(int(x), int(y))
                                for x, y in info["poly"]])
        elif info.get("start") is not None:
            item.SetStart(pcbnew.VECTOR2I(info["start"][0], info["start"][1]))
            item.SetEnd(pcbnew.VECTOR2I(info["end"][0], info["end"][1]))
    except Exception:
        pass


def _new_piece(board, src, a, b):
    """按源对象类型造一段新线条（保留线宽 / 层 / 网络）。"""
    if isinstance(src, pcbnew.PCB_TRACK):
        item = pcbnew.PCB_TRACK(board)
        try:
            item.SetNet(src.GetNet())
        except Exception:
            pass
    else:
        item = pcbnew.PCB_SHAPE(board)
        item.SetShape(pcbnew.S_SEGMENT)
    item.SetStart(pcbnew.VECTOR2I(int(round(a[0])), int(round(a[1]))))
    item.SetEnd(pcbnew.VECTOR2I(int(round(b[0])), int(round(b[1]))))
    item.SetWidth(src.GetWidth())
    item.SetLayer(src.GetLayer())
    board.Add(item)
    return item


def generate_outline(board, layers, offset_mm, corner_attr,
                     line_width_mm=0.05, replace_edge_cuts=False,
                     allow_infer=False, max_error_mm=0.001,
                     source="selection", selection=None, discrete=False):
    """模式一：描画轮廓 —— 对来源图形做等距偏移，输出闭合多边形。

    Args:
        discrete: True 时输出**离散线段**（每段独立可删减），
            而不是一条闭合多边形。等同「描画 + 从选区创建图线」两步并一步。
    """
    contours = get_source_contours(board, source, selection)
    offset = offset_poly(build_poly_from_contours(contours), offset_mm,
                         corner_attr, max_error_mm)

    items = []
    for layer in layers:
        if layer == pcbnew.Edge_Cuts and replace_edge_cuts:
            remove_layer_shapes(board, pcbnew.Edge_Cuts)
        for pts in all_contours(offset):
            items += build_items(board, layer, [pts], True, line_width_mm,
                                 discrete=discrete)
    return items


def generate_band(board, layers, band_mm, inset_mm=0.0, corner_attr=None,
                  gap_boxes_mm=None, step_mm=0.3, tol_mm=0.02,
                    allow_infer=False, max_error_mm=0.001,
                    source="selection", selection=None, discrete=False):
    """模式二：沿来源图形描画等宽露铜带（阻焊开窗带），可分段。

    band_mm  = 带宽（= 图形线宽）
    inset_mm = 带外沿距来源图形的距离，0 表示贴着来源图形
    discrete = True 时无断开区的整圈也逐段输出离散线段
    """
    if band_mm <= 0:
        raise BoardEdgeError("露铜带宽度必须大于 0。")

    corner_attr = corner_attr or CORNER_STRATEGIES[0][1]
    gap_boxes_mm = gap_boxes_mm or []

    contours = get_source_contours(board, source, selection)
    offset = offset_poly(build_poly_from_contours(contours),
                         -(inset_mm + band_mm / 2.0), corner_attr,
                         max_error_mm)

    items = []
    for layer in layers:
        for pts in all_contours(offset):
            runs, closed = split_runs(pts, gap_boxes_mm, step_mm)
            if not closed:
                for i, run in enumerate(runs):
                    runs[i] = simplify(run, tol_mm)
            items += build_items(board, layer, runs, closed, band_mm,
                                 discrete=discrete)
    return items
