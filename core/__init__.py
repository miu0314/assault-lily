"""核心框架：ADB 控制、图像识别、任务基类。"""

# 项目内 deps 目录（不随 Codex 运行环境更新被清）优先于运行时安装的库。
try:
    import dep_bootstrap

    dep_bootstrap.add_local_deps()
except Exception:  # 导入失败时保持原行为，让后续 import 报出真正原因
    pass
