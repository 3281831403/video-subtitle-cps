"""让 pytest 从任意工作目录启动时都能导入仓库根下的 ``cps`` 包。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
