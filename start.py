# -*- coding: utf-8 -*-
"""V15 Plus+ 启动入口 — 一键启动增强版 GUI。"""
import sys
import os

# 加 V15Plus 自己的 _plus_ 到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 然后正常启动 GUI（复用 stock_gui_V14.py）
if __name__ == "__main__":
    from scripts.stock_gui_V14 import StockProUI
    app = StockProUI()
    app.title("V15 Plus+ 大赢家")
    app.mainloop()
