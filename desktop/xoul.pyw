"""
Xoul Desktop Client — 콘솔 없이 실행하는 런처
.pyw 확장자는 pythonw.exe로 실행되어 콘솔 창이 뜨지 않습니다.

사용법:
    더블클릭 또는: pythonw desktop/xoul.pyw
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import main
main()
