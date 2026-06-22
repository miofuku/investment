# -*- coding: utf-8 -*-
"""共享:零依赖加载 .env(KEY=VALUE)。已存在的环境变量优先,不覆盖。
支持 # 注释、export 前缀与引号。被 agent_step1 / push_to_sheets 复用。"""
import os


def load_dotenv(path=None):
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)
