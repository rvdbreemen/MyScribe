"""python-xlib, pystray's backend on Linux (TASK-112.11, ADR-038).

LGPL-2.1-or-later, so it is collected the way hook-pystray.py collects
pystray: as loose .py files somebody can replace, with its licence beside it.
"""

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

hiddenimports = collect_submodules("Xlib")
module_collection_mode = "py"
datas = copy_metadata("python-xlib")
