"""打包 KiCad PCM 安装包（用于「插件和内容管理器 → 从文件安装」）。

用法：
    python build_release.py

输出：
    dist/BoardEdgeTools-<version>-pcm.zip

压缩包结构（PCM v2 要求，metadata.json 必须在根目录）：

    Archive root
    ├── plugins/              # 直接放运行期文件，不要再套一层 BoardEdgeTools/
    │   ├── __init__.py
    │   ├── plugin.py
    │   ├── core.py
    │   └── icon.png
    ├── resources/
    │   └── icon.png          # PCM 列表里显示的包图标
    └── metadata.json
"""

# 注意：PCM 会把包里的 plugins/ 原样拷到
#   <用户数据目录>/3rdparty/plugins/<identifier>/
# 所以 plugins/ 下必须直接是 __init__.py，多套一层目录 KiCad 就扫不到。

import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RELEASE = ROOT / "release"
DIST = ROOT / "dist"
PLUGIN_NAME = "BoardEdgeTools"

RUNTIME_FILES = [
    "__init__.py",
    "plugin.py",
    "core.py",
    "icon.png",
]

EXCLUDE = shutil.ignore_patterns("__pycache__", "*.pyc", "config.json")


def clean_and_copy():
    """把运行期文件同步到 release/plugins/BoardEdgeTools/。"""
    plugins_root = RELEASE / "plugins"
    target = plugins_root          # 运行期文件直接放 plugins/ 根目录
    resources_dir = RELEASE / "resources"

    if plugins_root.exists():
        shutil.rmtree(plugins_root)
    if resources_dir.exists():
        shutil.rmtree(resources_dir)

    target.mkdir(parents=True)
    resources_dir.mkdir(parents=True)

    for name in RUNTIME_FILES:
        src = ROOT / name
        if not src.exists():
            raise FileNotFoundError("缺少运行期文件: {}".format(src))
        shutil.copy2(src, target / name)

    # PCM 包图标
    icon = ROOT / "icon.png"
    if not icon.exists():
        raise FileNotFoundError("缺少图标: {}".format(icon))
    shutil.copy2(icon, resources_dir / "icon.png")

    # metadata.json 保持在 release 根目录
    meta = RELEASE / "metadata.json"
    if not meta.exists():
        raise FileNotFoundError("缺少 metadata.json: {}".format(meta))

    print("已同步运行期文件到 {}".format(target))


def get_version():
    with open(RELEASE / "metadata.json", "r", encoding="utf-8") as fh:
        return json.load(fh)["versions"][0]["version"]


def build_zip(version):
    DIST.mkdir(exist_ok=True)
    zip_name = "{}-{}-pcm.zip".format(PLUGIN_NAME, version)
    zip_path = DIST / zip_name
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(RELEASE.rglob("*")):
            if item.is_file():
                zf.write(item, arcname=item.relative_to(RELEASE).as_posix())

    size_kb = zip_path.stat().st_size / 1024
    print("已生成 PCM 安装包: {} ({:.1f} KB)".format(zip_path, size_kb))
    return zip_path


def sanity_check(zip_path):
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        required = [
            "metadata.json",
            "plugins/__init__.py",   # 必须是 plugins/ 根，不能是 plugins/<name>/
            "plugins/plugin.py",
            "plugins/core.py",
            "plugins/icon.png",
            "resources/icon.png",
        ]
        missing = [n for n in required if n not in names]
        if missing:
            raise AssertionError("压缩包缺少文件: {}".format(missing))
        meta = json.loads(zf.read("metadata.json"))
        # PCM v2 schema 必填项（缺失会导致「无法解析扩展内容包元数据」）
        for key in ("name", "description", "description_full", "identifier",
                    "type", "author", "license", "resources", "versions"):
            assert key in meta, "metadata.json 缺少必填字段: {}".format(key)
        for key in ("version", "status", "kicad_version"):
            assert key in meta["versions"][0], \
                "versions[0] 缺少必填字段: {}".format(key)
        assert meta["versions"][0]["version"], "metadata.json 没写版本号"
    print("校验通过。")


def main():
    clean_and_copy()
    version = get_version()
    zip_path = build_zip(version)
    sanity_check(zip_path)


if __name__ == "__main__":
    main()
