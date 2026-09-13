"""AIagent 包：模型、工具、人工审批与智能助手。

在包初始化里补一次 sys.path：只要 import 到本包（含其子模块），
就保证项目根目录在搜索路径上，从而能 import 根目录下的 db / tool_store。
"""

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
