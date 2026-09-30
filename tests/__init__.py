"""测试包：先挂上项目内 deps（cv2 / OCR / numpy），再加载各测试模块。"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    import dep_bootstrap

    dep_bootstrap.add_local_deps()
except Exception:
    pass
