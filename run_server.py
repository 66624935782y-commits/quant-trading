"""启动单进程本机服务，使用系统文件锁防止重复运行和多进程订单竞争。"""
import os
import sys
import uvicorn
from trader.config import ROOT, load_settings
from trader.api import create_app, configure_logging


def main():
    """验证配置，获取独占锁，再固定绑定 127.0.0.1:8000。"""
    (ROOT / 'runtime').mkdir(exist_ok=True)
    lock = open(ROOT / 'runtime/server.lock', 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            lock.write(b'0')
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print('已有助手进程运行，请勿重复启动。')
        return 1
    try:
        settings = load_settings()
    except (ValueError, OSError):
        print('配置无效：请检查 .env，设置至少 16 位非默认管理员密码、正确风控金额及交易开关。')
        return 1
    configure_logging(settings)
    app = create_app(settings, instance_id=os.environ.get('AQ_STARTUP_INSTANCE'))
    uvicorn.run(app, host='127.0.0.1', port=8000, workers=1, access_log=False, log_level='warning')
    lock.close()
    return 0

if __name__ == '__main__':
    sys.exit(main())
