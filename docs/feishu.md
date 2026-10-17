# BoardEdgeTools（描画 / 裁剪工具）

KiCad PCB 编辑器的 ActionPlugin，补齐两个 AD 有、KiCad 没有的功能：

- **描画外形**：框选任意图形（线条 / 圆弧 / 多边形），生成一条与之等距（内缩或外扩）的新轮廓。
- **裁剪线条（Trim）**：画一条线当刀刃，把走线 / 图形在交点处**切断但不删除**。

当前版本 **v1.4.0**，在 **KiCad 10.0.6** 上开发并实测。

---

# 1、安装

## 1.1 PCM「从文件安装」（推荐）

1. 到 [Releases](https://github.com/Skewec1/BoardEdgeTools/releases) 下载 `BoardEdgeTools-1.4.0-pcm.zip`（或自己跑 `python build_release.py` 生成）
2. KiCad → **插件和内容管理器 (PCM)** → **从文件安装** → 选 zip → **重启 KiCad**。

PCM 会把包里 `plugins/` 下的文件装到（实测 KiCad 10.0.6）：

```
C:\Users\<你>\Documents\KiCad\10.0\3rdparty\plugins\com_github_kicad-user_board-edge-tools\
```

> ⚠️ **包内目录层级不能多一层**。PCM 是把 `plugins/` 的内容**原样**拷进 `<identifier>/` 目录的，
> 所以 `plugins/` 下必须直接是 `__init__.py`；写成 `plugins/BoardEdgeTools/__init__.py`
> 会导致 KiCad 扫不到、工具栏不出现按钮（已踩过一次）。

## 1.2 手动放置（开发用）

把运行期文件复制到**用户数据目录**下的 scripting/plugins：

| 系统 | 路径 |
| --- | --- |
| Windows | `C:\Users\<你>\Documents\KiCad\<版本>\scripting\plugins\BoardEdgeTools\` |
| Linux | `~/Documents/KiCad/<版本>/scripting/plugins/BoardEdgeTools/` |
| macOS | `~/Documents/KiCad/<版本>/scripting/plugins/BoardEdgeTools/` |

> ⚠️ **别用 `%APPDATA%\kicad\scripting\plugins\`**，那是 KiCad 5 的遗留路径。
> KiCad 6 以后已改成 `Documents\KiCad\<版本>\scripting\plugins\`，装在那里不会生效。

## 1.3 排查

- 以上两种方法**不要同时用**，否则可能重复加载。
- 装完必须**重启 KiCad**，插件扫描只在启动时做。
- 启动日志：`%APPDATA%\kicad\BoardEdgeTools\plugin_load.log`（每启动一次追加一行，有堆栈说明加载失败）。
- 运行日志：`%APPDATA%\kicad\BoardEdgeTools\run.log`（GUI 打开后每次点击按钮都会记录，带毫秒时间戳，异常也写这里）。

---

# 2、用法

窗口是**非模态**的（置顶），可以一边在画布上框选 / 画矩形，一边点按钮取数据。

## 2.1 描画页：选来源

默认来源＝**选中图形**，流程：

1. 在 PCB 编辑器里**框选**要描画的图形（可以是一整条闭合图形，也可以是多条首尾相连的线段）
2. 点「**拾取当前选中图形**」→ 状态栏显示已拾取的段数
3. 设好参数 → 点「生成」

> 拾取后**不要清空选择**，直接点「生成」即可（插件保存的是对象引用）。
> 若只想描板框，把「来源」切到「板框」就行。

支持的对象类型：`PCB_SHAPE` 的 segment / arc / polygon / rect / circle、`PCB_TRACK` / `PCB_ARC` 走线、`PCB_ZONE`（取敷铜轮廓）、`PCB_FOOTPRINT`（取包围盒矩形）。端点不重合会自动按 0.002 mm 容差拼接。

## 2.2 模式一：描画轮廓

| 参数 | 说明 |
| --- | --- |
| 偏移量 | **负数=内缩，正数=外扩**（单位 mm） |
| 目标层 | 任意层；选 `Edge.Cuts` 时勾选「替换现有板框」＝直接把板框改成偏移后的形状 |
| 角点处理 | 倒角 / 圆角 / 保留尖角（决定内缩时转角怎么收） |

典型用途：把选中图形内缩 0.5 mm 画到 `User.1` 当**禁布参考线**。

## 2.3 离散图线（可逐段删减）

原生流程要先「**从选区创建 → 从选区创建图线**」把闭合轮廓拆成离散线段才能逐段删减；而 KiCad 图线只能在当前层创建，所以还得先切层再创建——详见《KiCad 进阶操作》27.2。

勾选本插件的「**输出为离散图线**」一步到位：

- 描画结果直接是**逐段独立的 `S_SEGMENT`**，可像普通线条一样逐段选中删除
- 在**任意指定层**生成，源对象保留
- 总长与多边形模式**完全一致**（只是把闭合环拆成一条条线段）

## 2.4 模式二：描画露铜带（阻焊开窗带）

| 参数 | 说明 |
| --- | --- |
| 带宽 W | 露铜带宽度（= 图形线宽） |
| 外沿距来源图形 | 0 = 贴着来源图形；0.2 = 离来源图形 0.2 mm 才开始露铜 |
| 原理 | 轮廓自动偏移 `-(inset + W/2)`，线宽取 `W`，开窗区间正好落在 `[inset, inset+W]` |
| 镜像 | 勾选「同时生成另一面」则 Top/Bottom 一起出 |

**铜怎么来**：F.Mask 只是开窗，底下必须有铜。推荐画一个 GND **敷铜（zone）**让它超出板框，KiCad 会自动裁剪到板边（内缩量 = Board Setup 里的 Copper to board edge clearance）。

**断开区三种取法**：

1. `选中对象取包围盒` —— 框选器件/图形，取其包围盒
2. `读取标记层矩形` —— 先在标记层（默认 `User.9`）画矩形，再点这个按钮；默认会顺手删掉标记图形
3. `手动添加…` —— 直接输入 `x0,y0,x1,y1`（mm，板坐标）

## 2.5 裁剪线条页（Trim）

对应 AD 的 `Edit → Trim`（只切断，不删除任何东西）。

> **为什么不用现成的 CutTracksAtLine**：那个插件的「被裁对象」只认走线 `PCB_TRACK`，
> 而我们描画生成的轮廓是图形 `PCB_SHAPE` 多边形，不在它的处理范围里——**生成出来的图形切不动**。

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

走线 `PCB_TRACK`、圆弧走线 `PCB_ARC`、图形 `PCB_SHAPE`（segment / arc / polygon / rect / circle / 贝塞尔 curve）。
**敷铜与封装会被跳过**——把敷铜删掉换成线段是破坏性的，插件不会做。新生成的线段保留原对象的**层、线宽、网络**。

---

# 3、关键设计：为什么绝不删除原对象

裁剪时被裁对象通常是**当前选中**的。若直接 `board.Remove()`，KiCad 的 Selection 工具会留下**悬空指针**，随后 `pcbnew.Refresh()` 或任何画布交互都会**卡死整个 KiCad**。

逐阶段日志实测确认（v1.3.1）：几何计算只花 **0.002s**，卡死 100% 发生在 `pcbnew.Refresh()`：

```
[core] 完成: cut=1 pieces=2 总耗时 0.002s
[clip] 调用 pcbnew.Refresh()…
        ← 卡死，再无输出
```

因此 v1.4.0 的对策：

- **首段复用原对象**（原地改端点，必要时 `SetShape(S_SEGMENT)`），只新建多出来的段
  —— 与官方插件 CutTracksAtLine 的做法一致
- 圆弧走线 `PCB_ARC` 无法原地改成直线，才会退回删除重建
- 视图刷新改走 **`wx.CallAfter`**：让按钮事件先走完（KiCad 处理完 mouse-up 与 Selection 更新）再重绘
- 「撤销」也相应变化：删掉新建的段 + 把原线条**恢复成裁剪前的形状**（而不是全删）

> 附带一条认知：`pcbnew.GetCurrentSelection()` 返回的是 **by-value 拷贝**，
> 对它 `.clear()` 是**空操作**，清不掉真机内部的 Selection，别指望它兜底。

---

# 4、原理与坑

- **F.Mask / B.Mask 是负片层**：画上去就是开窗，没有任何办法用另一个图形"补回绿油"。所以断开必须在生成时就分段，不能先画整圈再想办法打断。
- **被切断的段不能用多边形输出**：多边形会自动首尾相连，缺口处会多出一条露铜带。因此插件在分段模式下逐条输出 `S_SEGMENT`。
- **闭合图形的首末点不重合，靠隐式闭合**：矩形 / 圆 / 多边形 / 敷铜 / 封装都是这样。裁剪时若不显式补回首点，最后一条闭合边（末点→首点）会被漏切；描画时会 `chain_polylines` 判成开放折线丢掉。统一在 `shape_to_polyline` 里用 `_close_ring()` 补回首点。
- **切断要保留中间顶点**：切开的两点之间原有的顶点一个都不能丢，否则周长会缩水（矩形被一刀切完只剩 10 mm 的坑已修）。
- **内外判定要用弧长中点，不是首尾连线中点**：圆环上一段弧的首尾连线中点可能落在环外，判内外会出错。
- **RDP 简化对闭合环会退化**：首尾同一基准线让所有距离恒为 0，整圈塌缩。离散输出因此直接用原始顶点，不做 `simplify`。
- **模态对话框必须 `Destroy()`**：`wx.MessageDialog(...).ShowModal()` 这种一次性写法不销毁对话框，会卡住 wx 事件循环 → 整个 KiCad 无响应。
- **描画生成的圆角轮廓顶点可能很多**（实测一条 = 539 顶点），裁一刀会得到 539 条独立线段。要控制对象数量就先简化来源图形。
- **板内挖槽（cutout）同样处理**：插件会把板框的 holes 一起偏移并生成。
- **铜层图形无网络**：在 F.Cu 直接生成图形铜，DRC 会报未连接铜皮。需要带电网络的铜请用 zone。
- 参数自动存到插件目录的 `config.json`，下次打开沿用。

---

# 5、开发与维护

## 5.1 源码结构

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 插件注册入口 + 加载日志 |
| `plugin.py` | ActionPlugin 与 wx GUI（Notebook 双页：描画 / 裁剪） |
| `core.py` | 几何核心：来源提取、偏移、分段、裁剪、生成（零 GUI 依赖） |
| `config.json` | 运行时自动生成，保存上次参数 |
| `icon.png` | 工具栏图标 |
| `build_release.py` | 打包 PCM 安装包 + 必填项校验 |
| `release/metadata.json` | PCM v2 元数据（改版本只需改这里再跑打包脚本） |
| `tests/` | 见下 |

## 5.2 测试

```bat
python tests\test_core_geom.py                              :: 纯几何（不需要 KiCad）
D:\Application\KiCAD\bin\python.exe tests\test_clip_kicad.py        :: 裁剪端到端
D:\Application\KiCAD\bin\python.exe tests\test_clip_gui_flow.py     :: 裁剪 GUI 事件链路
D:\Application\KiCAD\bin\python.exe tests\test_discrete.py          :: 离散图线输出
```

## 5.3 脚本调用（无 GUI）

```python
import sys
sys.path.insert(0, r"E:\4_EDA\100_Tools\3_KiCad\1_KiCad插件")
import pcbnew
from BoardEdgeTools import core

board = pcbnew.GetBoard()
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

# 裁剪：返回 (生成的对象列表, 统计)；首段会原地改造原对象
polys, closed, _ = core.collect_blade(selection[:1])
core.clip_items(board, polys, selection[1:], delete="none")
```

---

# 6、版本记录

| 版本 | 内容 |
| --- | --- |
| v1.0.0 | 板框内缩/外扩 + 板边分段露铜带 |
| v1.1.x | 来源改为「描画选中对象的外形」；走线拾取根因修复 |
| v1.2.0 | 新增「裁剪线条（Trim）」页 |
| v1.2.1 | 修弹窗未 `Destroy` 导致卡死；刀刃求交加空间索引 |
| v1.3.0 | 描画新增「输出为离散图线」 |
| v1.3.1 | 逐阶段诊断日志（毫秒时间戳） |
| **v1.4.0** | **裁剪卡死根因修复：绝不 Remove 当前选中对象；刷新改延迟；撤销语义调整** |
