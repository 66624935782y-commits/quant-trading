"""按明确白名单构建 Windows ZIP，绝不打包密钥、运行数据库或虚拟环境。"""
import hashlib
from pathlib import Path
import zipfile
from trader.config import ROOT


def build(destination=None):
    """复制程序、说明、示例配置和测试到独立包，返回归档路径。"""
    target = Path(destination) if destination else ROOT.parent / 'downloads/ai-quant-trader-private-windows.zip'
    target.parent.mkdir(parents=True, exist_ok=True)
    paths = [ROOT/name for name in ('.env.example', '.gitignore', 'README.md', 'VALIDATION.md', 'requirements.txt',
             'requirements-dev.txt', 'pytest.ini', 'install.bat', 'start.bat', 'stop.bat',
             'manage.py', 'run_server.py', 'diagnose.bat', 'check_api.bat', 'build_package.py', 'web/index.html')]
    paths += sorted((ROOT/'trader').glob('*.py'))
    paths += sorted((ROOT/'tests').glob('*.py'))
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            data = path.read_bytes()
            # 批处理使用 Windows 原生 CRLF 换行；UTF-8 中文配合 chcp 65001。
            if path.suffix == '.bat':
                data = data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            archive.writestr('AIQuantTraderPrivate/'+path.relative_to(ROOT).as_posix(), data)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest+'  '+target.name+'\n', encoding='utf-8')
    return target

if __name__ == '__main__':
    print(build())
