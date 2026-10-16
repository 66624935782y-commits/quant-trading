"""由双击脚本传入用户选定的 .env 路径，仅执行只读诊断。"""
from pathlib import Path
import sys
from trader.diagnostics import main

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('请双击 check_api.bat 并选择原程序文件夹，不需要粘贴密钥。')
        raise SystemExit(1)
    raise SystemExit(main(Path(sys.argv[1])))
