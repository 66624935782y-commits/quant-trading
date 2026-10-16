"""仅监听本机的认证 API；除登录外所有 /api 路由均验证会话。"""
from collections import deque
import hmac
import logging
import os
from logging.handlers import RotatingFileHandler
import secrets
import threading
import time
from typing import Literal
from uuid import UUID
import ccxt
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import ROOT
from .db import Database
from .exchange import Binance, UserError
from .service import Trader

class Model(BaseModel):
    """禁止未知请求字段，以免用户误以为额外参数生效。"""
    model_config = ConfigDict(extra='forbid')

class Login(Model):
    """单管理员登录，只收密码，不存请求正文日志。"""
    password: str = Field(min_length=1, max_length=1024)

class OpenOrder(Model):
    """受限开仓请求；保证金模式固定逐仓，金额是 USDT 名义价值。"""
    request_id: UUID
    symbol: Literal['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    direction: Literal['long', 'short']
    leverage: int = Field(default=5, ge=1, le=20, strict=True)
    kind: Literal['limit', 'market']
    margin_mode: Literal['isolated'] = 'isolated'
    notional: str = Field(max_length=32)
    price: str | None = Field(default=None, max_length=32)

class CloseOrder(Model):
    """按合约与持仓方向精确选择全部或部分平仓。"""
    request_id: UUID
    symbol: str = Field(min_length=1, max_length=64, pattern=r'^[A-Z0-9]+/USDT:USDT$')
    side: Literal['long', 'short']
    percent: str = Field(max_length=16)


def create_app(settings, exchange=None, database=None):
    """构建可注入交易所替身的应用；生产配置仅由 .env 读取。"""
    db = database or Database(settings.database)
    trader = Trader(settings, db, exchange or Binance(settings))
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.trader = trader
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', 'testserver'])
    attempts = deque()
    login_lock = threading.Lock()

    @app.middleware('http')
    async def protect(request: Request, call_next):
        """验证来源、非简单请求标头及 HttpOnly 会话，阻止跨站控制本机。"""
        if request.url.path.startswith('/api/'):
            origin = request.headers.get('origin')
            allowed = {'http://localhost:8000', 'http://127.0.0.1:8000'}
            if origin and origin not in allowed:
                return JSONResponse({'error': '请求来源不受信任。'}, status_code=403)
            if request.method != 'GET' and request.headers.get('x-trader-request') != '1':
                return JSONResponse({'error': '缺少安全请求标记，请通过本机网页操作。'}, status_code=403)
            if request.url.path != '/api/login':
                token = request.cookies.get('aq_session', '')
                if not token or not db.valid_session(token):
                    return JSONResponse({'error': '请先登录，或登录已过期。'}, status_code=401)
        try:
            response = await call_next(request)
        except Exception:
            trader.audit('服务异常', '已隐藏原始异常，请检查配置与交易所状态')
            response = JSONResponse({'error': '操作未完成，请刷新并核对账户和订单状态，不要重复提交。'}, status_code=500)
        if request.url.path.startswith('/api/') and request.url.path != '/api/login':
            trader.audit('接口访问', request.method + ' ' + request.url.path + ' ' + str(response.status_code))
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; style-src 'self' 'unsafe-inline'; connect-src 'self' https://cdn.tailwindcss.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(UserError)
    async def business_error(request, exc):
        """只返回程序定义的中文错误。"""
        trader.audit('操作被拒绝', str(exc))
        return JSONResponse({'error': str(exc)}, status_code=400)

    @app.exception_handler(ccxt.BaseError)
    async def exchange_error(request, exc):
        """交易所网络错误不得泄露签名 URL 或原始响应。"""
        trader.audit('币安连接失败', type(exc).__name__)
        return JSONResponse({'error': '币安连接超时或不可用，请检查网络、地区限制和 IP 白名单。订单若未确认，请先核对，不要重发。'}, status_code=502)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        """屏蔽包含原始输入的英文校验错误。"""
        return JSONResponse({'error': '输入格式不正确，请检查交易对、杠杆、请求编号及数字。'}, status_code=422)

    @app.get('/')
    def index():
        """返回唯一的 HTML 页面，页面内不包含账户或密钥。"""
        return FileResponse(ROOT / 'web/index.html', headers={'X-AQ-Pid': str(os.getpid())})

    @app.post('/api/login')
    def login(data: Login):
        """常量时间校验密码并签发高熵随机令牌，限制一分钟内登录次数。"""
        with login_lock:
            now = time.monotonic()
            while attempts and attempts[0] < now-60:
                attempts.popleft()
            if len(attempts) >= 5:
                return JSONResponse({'error': '登录尝试过多，请一分钟后再试。'}, status_code=429)
            attempts.append(now)
            if not hmac.compare_digest(data.password.encode(), settings.password.encode()):
                trader.audit('登录失败')
                return JSONResponse({'error': '管理员密码错误。'}, status_code=401)
        token = secrets.token_urlsafe(32)
        db.create_session(token, settings.session_hours*3600)
        response = JSONResponse({'ok': True})
        # 仅限本机 HTTP，故不能设置 Secure；不得暴露到公网或局域网。
        response.set_cookie('aq_session', token, httponly=True, samesite='strict', max_age=settings.session_hours*3600, path='/')
        trader.audit('登录成功')
        return response

    @app.post('/api/logout')
    def logout(request: Request):
        """服务端撤销会话并删除浏览器 Cookie。"""
        db.logout(request.cookies.get('aq_session', ''))
        response = JSONResponse({'ok': True})
        response.delete_cookie('aq_session', path='/')
        trader.audit('退出登录')
        return response

    @app.get('/api/status')
    def status():
        """返回交易开关和后端风控状态。"""
        return trader.status()

    @app.get('/api/account')
    def account():
        """读取真实账户概览。"""
        return trader.account()

    @app.get('/api/quotes')
    def quotes():
        """读取真实公开行情，串行使用同一 CCXT 客户端。"""
        with trader.lock:
            return trader.ex.quotes()

    @app.get('/api/orders')
    def orders():
        """读取实际普通挂单并脱敏。"""
        with trader.lock:
            return [trader.clean_order(o) for o in trader.ex.open_orders()]

    @app.post('/api/order')
    def order(data: OpenOrder):
        """只在用户提交后执行开仓校验与发单。"""
        return trader.open(data.model_dump(mode='json'))

    @app.post('/api/close')
    def close(data: CloseOrder):
        """验证百分比后执行受保护的减仓。"""
        from .exchange import number
        percent = number(data.percent)
        if percent <= 0 or percent > 100:
            raise UserError('平仓比例必须大于 0 且不超过 100%。')
        return trader.close(data.model_dump(mode='json'))

    @app.post('/api/cancel-all')
    def cancel():
        """请求撤销所有普通未成交挂单。"""
        return trader.cancel_all()

    @app.post('/api/emergency')
    def emergency():
        """执行持久停止、撤单与平仓，并返回逐项结果。"""
        return trader.emergency()

    @app.post('/api/resume')
    def resume():
        """管理员明确操作才解除紧急锁，日亏损限制仍由后端执行。"""
        with trader.lock:
            trader.ensure_clear()
            db.halt(False)
            trader.audit('管理员解除紧急锁')
            return trader.status()

    @app.post('/api/reconcile')
    def reconcile():
        """按原客户端编号核对未确认订单，不重新提交。"""
        return trader.reconcile()

    @app.get('/api/history')
    def history():
        """显示本地订单意图和真实成交，二者明确区分。"""
        return trader.history()

    @app.post('/api/history/sync')
    def sync():
        """管理员手动同步币安历史成交。"""
        return trader.sync()

    return app


def configure_logging(settings):
    """只启用本应用无敏感信息的轮转日志，不输出 CCXT 请求调试信息。"""
    settings.logfile.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('private_trader')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(settings.logfile, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
