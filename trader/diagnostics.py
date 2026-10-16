"""只读币安认证诊断；输出仅包含阶段、状态、错误码及交易所报告的请求 IP。"""
import json
from .exchange import Binance, UserError, friendly_exchange_error, safe_error_details


def run_checks(settings, exchange=None):
    """依次检查本地配置、公开时间及三类账户读取，首个失败后停止，不发交易请求。"""
    checks = []
    report = {'ok': False, 'read_only': True, 'live_trading': settings.live, 'checks': checks}
    if not settings.api_key or not settings.api_secret:
        checks.append({'name': '本地密钥配置', 'status': 'failed',
                       'message': '.env 缺少 API Key 或 Secret；请在自己电脑填写、保存，再重新启动服务。'})
        return report
    if any(value != value.strip() for value in (settings.api_key, settings.api_secret)):
        checks.append({'name': '本地密钥配置', 'status': 'failed',
                       'message': 'Key 或 Secret 包含开头/末尾空白，请在本机核对。诊断不会修改你的密钥。'})
        return report
    checks.append({'name': '本地密钥配置', 'status': 'passed',
                   'message': '两项已填写；不显示值，也不据此判定密钥有效。'})
    ex = exchange or Binance(settings)
    stages = [
        ('币安公共连接与时间同步', lambda: ex.call('load_time_difference')),
        ('USDT 合约账户余额读取', ex.balance),
        ('USDT 合约持仓读取（含杠杆档位）', ex.positions),
        ('合约收益流水读取', lambda: ex.call('fapiPrivateGetIncome', {'limit': 1})),
    ]
    for name, action in stages:
        try:
            # 返回的真实余额、持仓及收益不进入诊断摘要，也不写到日志。
            action()
            checks.append({'name': name, 'status': 'passed', 'message': '只读请求成功，账户内容不输出。'})
        except Exception as exc:
            checks.append({'name': name, 'status': 'failed',
                           'message': friendly_exchange_error(exc), **safe_error_details(exc)})
            return report
    report['ok'] = True
    return report


def main(config_path=None):
    """从本机 .env 新建只读客户端，打印和保存可分享的脱敏诊断。"""
    from .config import ROOT, load_settings
    print('只读币安连接诊断：不会下单、平仓、撤单或修改杠杆。', flush=True)
    print('正在读取本机 .env 并检查连接，通常需要几秒，网络失败可能稍久。', flush=True)
    try:
        settings = load_settings(config_path) if config_path is not None else load_settings()
        report = run_checks(settings)
    except (ValueError, OSError):
        report = {'ok': False, 'read_only': True, 'checks': [{'name': '本地配置', 'status': 'failed',
            'message': '无法读取有效 .env。请检查文件名、管理员密码与风控配置；不要发送配置内容。'}]}
    except Exception:
        report = {'ok': False, 'read_only': True, 'checks': [{'name': '客户端准备', 'status': 'failed',
            'message': '本地客户端未能初始化，请重新运行 install.bat 检查依赖。'}]}
    for item in report['checks']:
        print(('通过' if item['status'] == 'passed' else '未通过') + '：' + item['name'], flush=True)
        print(item['message'], flush=True)
    if report['ok']:
        print('当前 .env 的账户读取已通过。如网页仍认证失败，请 stop.bat 后重新 start.bat，以载入当前密钥。', flush=True)
    else:
        print('已停止后续读取。请按上面失败阶段处理，保持 LIVE_TRADING=false；不要取消 API IP 白名单。', flush=True)
    try:
        base = config_path.parent if config_path is not None else ROOT
        path = base/'runtime/api_diagnosis.txt'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print('已保存 runtime/api_diagnosis.txt；可以发送此摘要，不要发送 .env 或原始账户日志。', flush=True)
    except OSError:
        print('无法保存摘要，请查看本窗口提示。', flush=True)
    return 0 if report['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
