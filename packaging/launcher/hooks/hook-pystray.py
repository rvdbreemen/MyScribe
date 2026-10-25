"""pystray for the frozen launcher (TASK-112.11, ADR-038).

The backends are chosen at import time, so every one of them is collected, as
pyinstaller-hooks-contrib's own hook-pystray does; this hook takes its place,
because a hook here wins over contrib's. What it adds is the collection mode:
pystray is LGPL-3.0, and as loose .py files in the bundle's _internal folder,
not compiled into the archive inside the executable, it is a library somebody
can replace with a modified version, which the licence asks for.
"""

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

hiddenimports = collect_submodules("pystray")
module_collection_mode = "py"
# The dist-info carries COPYING and COPYING.LGPL: the licence ships with the code.
datas = copy_metadata("pystray")
