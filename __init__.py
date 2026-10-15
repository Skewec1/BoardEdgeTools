"""KiCad ActionPlugin 入口。

KiCad 启动时加载本文件并注册 ``plugin.py`` 里的 ActionPlugin 子类。
加载失败时把 traceback 写到
``%APPDATA%/kicad/BoardEdgeTools/plugin_load.log``
（Linux/macOS 为 ``~/.config/kicad/BoardEdgeTools/plugin_load.log``），
工具栏按钮不见了就先看这个文件。
"""

import os
import sys
import traceback


def _log_file_path():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        base = os.path.join(base, "kicad", "BoardEdgeTools")
    else:
        base = os.path.join(os.path.expanduser("~"), ".config", "kicad",
                            "BoardEdgeTools")
    try:
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "plugin_load.log")
    except Exception:
        return os.path.join(os.path.expanduser("~"), "board_edge_tools_load.log")


def _log(msg):
    try:
        with open(_log_file_path(), "a", encoding="utf-8") as fh:
            fh.write(msg.rstrip() + "\n")
    except Exception:
        pass


def _register():
    try:
        _log("[bet] plugin load start")
        import pcbnew  # noqa: F401 -- 确认 pcbnew 可用
        from .plugin import BoardEdgeToolsPlugin

        BoardEdgeToolsPlugin().register()
        _log("[bet] plugin registered OK")
    except ImportError:
        _log("[bet] pcbnew not importable, skipping registration")
    except Exception:
        _log("[bet] plugin load failed:\n" + traceback.format_exc())
        raise


if os.environ.get("BOARD_EDGE_TOOLS_CLI_MODE"):
    pass  # 命令行 / 测试模式不注册
else:
    _register()
