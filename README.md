# BoardEdgeTools（描画 / 裁剪工具）

KiCad PCB 编辑器的 ActionPlugin，补齐两个 AD 有、KiCad 没有的功能：

- **描画外形**：框选任意图形（线条 / 圆弧 / 多边形），生成一条与之等距（内缩或外扩）的新轮廓。
- **裁剪线条（Trim）**：画一条线当刀刃，把走线 / 图形在交点处**切断但不删除**。

| 来源（source） | 说明 |
| --- | --- |
| **选中图形**（默认） | PCB 上框选的线段 / 圆弧 / 多边形 / 矩形 / 圆 / 敷铜轮廓 / 封装包围盒，自动连成闭合轮廓后描画 |
| 板框 | Edge.Cuts 上所有闭合轮廓（含板内挖槽） |

| 模式（mode） | 说明 |
| --- | --- |
| **描画露铜带** | 沿来源图形描一条等宽带（F.Mask/B.Mask 开窗带），可任意分段 |
| 描画轮廓 | 对来源图形整体等距偏移，输出闭合多边形到任意层 |

可选勾选 **「输出为离散图线」**：不输出整条闭合多边形，而是拆成逐段独立的线段（`S_SEGMENT`），可在任意指定层生成、**源对象保留**，生成结果能像普通图线一样单段删减修改。

在 **KiCad 10.0.6** 上开发并实测。

---

## 安装

### 方法 1：PCM「从文件安装」（推荐）

先跑 `python build_release.py` 生成，再用打包好的 `dist/BoardEdgeTools-1.4.0-pcm.zip`：
KiCad → **插件和内容管理器 (PCM)** → **从文件安装** → 选 zip → 重启 KiCad。

PCM 会把包里 `plugins/` 下的文件装到（实测，KiCad 10.0.6）：

```
C:\Users\<你>\Documents\KiCad\10.0\3rdparty\plugins\com_github_kicad-user_board-edge-tools\
```

> ⚠️ **包内目录层级不能多一层**。PCM 是把 `plugins/` 的内容**原样**拷进 `<identifier>/` 目录的，
> 所以 `plugins/` 下必须直接是 `__init__.py`，写成 `plugins/BoardEdgeTools/__init__.py`
> 会导致 KiCad 扫不到、工具栏不出现按钮（已踩过一次）。

### 方法 2：手动放置（开发用）

把运行期文件复制到 **用户数据目录**下的 scripting/plugins：

| 系统 | 路径 |
| --- | --- |
| Windows | `C:\Users\<你>\Documents\KiCad\<版本>\scripting\plugins\BoardEdgeTools\` |
| Linux | `~/Documents/KiCad/<版本>/scripting/plugins/BoardEdgeTools/` |
| macOS | `~/Documents/KiCad/<版本>/scripting/plugins/BoardEdgeTools/` |

> ⚠️ **别用 `%APPDATA%\kicad\scripting\plugins\`**，那是 KiCad 5 的遗留路径，
> KiCad 6 以后已经改成 `Documents\KiCad\<版本>\scripting\plugins\`，装在那里不会生效。

### 通用排查

- 以上两种方法**不要同时用**，否则可能重复加载。
- 装完必须**重启 KiCad**，插件扫描只在启动时做。
- 启动日志：`%APPDATA%\kicad\BoardEdgeTools\plugin_load.log`
  （每启动一次追加一行，有堆栈说明加载失败）。
- 运行日志：`%APPDATA%\kicad\BoardEdgeTools\run.log`
  （GUI 打开后每次点击按钮都会记录，异常也写这里）。

---

## 用法

### 选来源

默认来源＝**选中图形**。流程：

1. 在 PCB 编辑器里**框选**要描画的图形（线段 / 圆弧 / 多边形都行，可以是一整条闭合图形，也可以是多条首尾相连的线段）
2. 点插件窗口里的「**拾取当前选中图形**」→ 状态栏显示已拾取的对象段数
3. 设好参数，点「生成」

> 拾取后**不要清空选择**，直接点「生成」即可（插件保存的是对象引用）。
> 若只想描板框，把「来源」切到「板框」就行。

支持的对象类型：`PCB_SHAPE` 的 segment / arc / polygon / rect / circle、
`PCB_TRACK` / `PCB_ARC` 走线、`PCB_ZONE`（取敷铜轮廓）、
`PCB_FOOTPRINT`（取包围盒矩形）。端点不重合会自动按 0.002 mm 容差拼接。

### 模式一：描画轮廓

- 偏移量填 **负数=内缩，正数=外扩**（单位 mm）
- 目标层可选任意层；选 `Edge.Cuts` 时勾选「替换现有板框」＝直接把板框改成偏移后的形状
- 角点处理：倒角/圆角/保留尖角（决定内缩时转角怎么收）

典型用途：把选中图形内缩 0.5 mm 画到 `User.1` 当**禁布参考线**。

### 离散图线（可逐段删减）

原生流程（对应《KiCad 进阶操作》27.2）要先「**从选区创建 → 从选区创建图线**」把闭合轮廓拆成离散线段，才能逐段删减；而 KiCad 图线只能在当前层创建，所以还得先切层再创建。

本插件勾选「**输出为离散图线**」后一步到位：

- 描画结果直接是**逐段独立的 `S_SEGMENT`**，可像普通线条一样逐段选中删除
- 在**任意指定层**生成，源对象保留
- 总长与多边形模式**完全一致**（只是把闭合环拆成一条条线段）

> ⚠️ 注意：RDP 简化（`simplify`）对**闭合环**不适用——首尾同一基准线会让所有距离退化，整圈塌缩。所以离散输出改走 `build_items(..., discrete=True)`，闭合环直接用原始顶点逐段输出，不做简化。

### 模式二：描画露铜带（阻焊开窗带）

- 带宽 W：露铜带宽度（= 图形线宽）
- 外沿距来源图形：0 = 贴着来源图形；0.2 = 离来源图形 0.2 mm 才开始露铜
- 轮廓自动偏移 `-(inset + W/2)`，线宽取 `W`，开窗区间正好落在 `[inset, inset+W]`
- 勾选「同时生成另一面」则 Top/Bottom 一起出

**铜怎么来**：F.Mask 只是开窗，底下必须有铜。推荐画一个 GND **敷铜（zone）**让它超出板框，KiCad 会自动裁剪到板边（内缩量 = Board Setup 里的 Copper to board edge clearance）。

**断开区三种取法**（窗口非模态，可以边画边点）：

1. `选中对象取包围盒` —— 在 PCB 上框选器件/图形，取其包围盒
2. `读取标记层矩形` —— 先在标记层（默认 `User.9`）画矩形，再点这个按钮；默认会顺手删掉标记图形
3. `手动添加…` —— 直接输入 `x0,y0,x1,y1`（mm，板坐标）

---

## 裁剪线条（Trim）

窗口第二页「**裁剪线条**」，对应 AD 的 `Edit → Trim`（只切断，不删除任何东西）。

**为什么不用现成的 CutTracksAtLine**：那个插件的「被裁对象」只认走线 `PCB_TRACK`，
而我们描画生成的轮廓是图形 `PCB_SHAPE` 多边形，不在它的处理范围里，所以**生成出来的图形切不动**。
本插件的裁剪同时支持走线和图形。

### 操作步骤

1. 在 PCB 上**画一条线**（线段、圆弧、矩形、圆、多边形都行）当**刀刃**
2. 选中它 → 点「**拾取当前选中图形作刀刃**」
3. **框选**要裁的线条 → 点「**拾取当前选中图形作被裁对象**」
4. 选裁剪后处理 → 点「**执行裁剪**」

### 裁剪后处理

| 选项 | 说明 |
| --- | --- |
| **保留两侧（仅切断）** | 默认。在交点处断开，两段都留着，断点重合、视觉无变化 |
| 删除刀刃内侧部分 | 需要**闭合**刀刃（矩形 / 圆 / 多边形）才有内外概念 |
| 删除刀刃外侧部分 | 同上 |

刀刃是开放线时，「删除内侧 / 外侧」会自动置灰，避免无意义的操作。

### 支持的对象

走线 `PCB_TRACK`、圆弧走线 `PCB_ARC`、图形 `PCB_SHAPE`（segment / arc / polygon /
rect / circle / 贝塞尔 curve）。**敷铜与封装会被跳过**——把敷铜删掉换成线段是破坏性的，插件不会做。

新生成的线段保留原对象的**层、线宽、网络**。

> 提示：描画生成的闭合轮廓顶点数可能很多（实测一条圆角轮廓 = 539 顶点），
> 裁一刀就会得到 539 条独立线段。若要控制对象数量，先简化来源图形再描画。

### 为什么不删除原对象（关键）

裁剪时被裁对象通常是**当前选中**的。若直接 `board.Remove()`，KiCad 的 Selection 工具
会留下**悬空指针**，随后 `pcbnew.Refresh()` 或任何画布交互都会让整个 KiCad 卡死
（逐阶段日志实测确认：几何计算仅 0.002s，卡死 100% 发生在 `pcbnew.Refresh()`）。

因此：

- **首段复用原对象**（原地改端点，必要时 `SetShape(S_SEGMENT)`），只新建多出来的段
  —— 与官方插件 CutTracksAtLine 的做法一致
- 圆弧走线 `PCB_ARC` 无法原地改成直线，才会退回删除重建
- 视图刷新改走 **`wx.CallAfter`**：让按钮事件先走完（KiCad 处理完 mouse-up 与 Selection 更新）再重绘
- 「撤销」也相应变化：删掉新建的段 + 把原线条**恢复成裁剪前的形状**（而不是全删）**。

---

## 原理与坑

- **F.Mask / B.Mask 是负片层**：画上去就是开窗，没有任何办法用另一个图形"补回绿油"。所以断开必须在生成时就分段，不能先画整圈再想办法打断。
- **被切断的段不能用多边形输出**：多边形会自动首尾相连，缺口处会多出一条露铜带。因此插件在分段模式下逐条输出 `S_SEGMENT`。
- **闭合图形的首末点不重合，靠隐式闭合**：矩形 / 圆 / 多边形 / 敷铜 / 封装都是这样。
  裁剪时若不显式补回首点，最后一条闭合边（末点→首点）会被漏切；描画时会被 `chain_polylines` 判成开放折线丢掉。
  统一在 `shape_to_polyline` 里用 `_close_ring()` 补回首点。
- **切断要保留中间顶点**：切开的两点之间原有的顶点一个都不能丢，否则周长会缩水（矩形被一刀切完只剩 10 mm 的坑已修）。
- **内外判定要用弧长中点，不是首尾连线中点**：圆环上一段弧的首尾连线中点可能落在环外，判内外会出错。
- **板内挖槽（cutout）同样处理**：插件会把板框的 holes 一起偏移并生成。
- **铜层图形无网络**：如果在 F.Cu 直接生成图形铜，DRC 会报未连接铜皮。需要带电网络的铜请用 zone。
- 参数会自动存到插件目录的 `config.json`，下次打开沿用。

---

## 脚本调用（无 GUI）

```python
import sys
sys.path.insert(0, r"<BoardEdgeTools 的上一级目录>")
import pcbnew
from BoardEdgeTools import core

board = pcbnew.GetBoard()

# 1) 以「选中图形」为来源：先框选，再拾取
selection = list(pcbnew.GetCurrentSelection())

# 描画轮廓：内缩 1 mm，输出到 User.1 当禁布参考线
core.generate_outline(board, [pcbnew.User_1], -1.0,
                      core.CORNER_STRATEGIES[0][1],
                      source="selection", selection=selection)

# 离散图线：输出逐段独立的 S_SEGMENT，可单段删减
core.generate_outline(board, [pcbnew.User_2], -1.0,
                      core.CORNER_STRATEGIES[0][1],
                      source="selection", selection=selection, discrete=True)

# 描画 1 mm 宽露铜带，x=20~30 处断开
core.generate_band(board, [pcbnew.F_Mask], band_mm=1.0, inset_mm=0.0,
                   gap_boxes_mm=[(20, -100, 30, 100)],
                   source="selection", selection=selection)

# 2) 以「板框」为来源
core.generate_outline(board, [pcbnew.User_1], -1.0,
                      core.CORNER_STRATEGIES[0][1], source="board")

pcbnew.Refresh()
```

---

## 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 插件注册入口 + 加载日志 |
| `plugin.py` | ActionPlugin 与 wx GUI（非模态窗口） |
| `core.py` | 几何核心：板框提取、偏移、分段、生成（无 GUI 依赖） |
| `config.json` | 运行时自动生成，保存上次参数 |
| `icon.png` | 工具栏图标 |
| `build_release.py` | 打包 PCM 安装包 + 必填项校验 |
| `tests/test_core_geom.py` | 纯几何单元测试（不需要 KiCad） |
| `tests/test_clip_kicad.py` | 裁剪功能端到端测试（需 KiCad 自带 python） |
| `tests/test_clip_gui_flow.py` | 裁剪 GUI 事件链路集成测试 |
| `tests/test_discrete.py` | 离散图线输出测试（含源对象保留校验） |

跑测试：

```bat
python tests\test_core_geom.py
D:\Application\KiCAD\bin\python.exe tests\test_clip_kicad.py
D:\Application\KiCAD\bin\python.exe tests\test_clip_gui_flow.py
D:\Application\KiCAD\bin\python.exe tests\test_discrete.py
```
