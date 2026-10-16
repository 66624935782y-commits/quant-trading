"""SQLite 持久化会话、风控锁、订单意图和交易成交记录。"""
import contextlib
import hashlib
import json
import sqlite3
import time
from pathlib import Path

class Database:
    def __init__(self, path: Path):
        """创建数据库父目录及表，重复初始化不会清除已有记录。"""
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO state VALUES('halted','false');
            CREATE TABLE IF NOT EXISTS intents(
              id TEXT PRIMARY KEY, created REAL NOT NULL, request TEXT NOT NULL,
              symbol TEXT NOT NULL, status TEXT NOT NULL, exchange_id TEXT, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS exchange_orders(
              id TEXT NOT NULL, symbol TEXT NOT NULL, timestamp INTEGER, payload TEXT NOT NULL,
              PRIMARY KEY(id,symbol));
            CREATE TABLE IF NOT EXISTS fills(
              id TEXT NOT NULL, symbol TEXT NOT NULL, timestamp INTEGER, payload TEXT NOT NULL,
              PRIMARY KEY(id,symbol));
            CREATE TABLE IF NOT EXISTS audit(
              id INTEGER PRIMARY KEY, created REAL NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL);
            ''')

    @contextlib.contextmanager
    def connect(self):
        """每次操作独立连接，事务失败自动回滚。"""
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def audit(self, action, detail=''):
        """记录安全审计事件，只接受程序生成的无敏感内容。"""
        with self.connect() as db:
            db.execute('INSERT INTO audit(created,action,detail) VALUES(?,?,?)', (time.time(), action, detail))

    def create_session(self, token, seconds):
        """仅保存会话令牌的哈希值，删除过期会话。"""
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?)', (hashlib.sha256(token.encode()).hexdigest(), time.time()+seconds))

    def valid_session(self, token):
        """验证会话是否存在且未过期。"""
        with self.connect() as db:
            return db.execute('SELECT 1 FROM sessions WHERE token_hash=? AND expires>?',
                              (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone() is not None

    def logout(self, token):
        """撤销当前令牌，浏览器之外的副本也同时失效。"""
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def halted(self):
        """读取重启后仍有效的紧急停止开仓锁。"""
        with self.connect() as db:
            return db.execute("SELECT value FROM state WHERE key='halted'").fetchone()[0] == 'true'

    def halt(self, enabled):
        """持久化紧急停止状态，不因重启服务自动解除。"""
        with self.connect() as db:
            db.execute("UPDATE state SET value=? WHERE key='halted'", ('true' if enabled else 'false',))

    def intent(self, key):
        """按客户端幂等编号读取已有订单意图。"""
        with self.connect() as db:
            row = db.execute('SELECT * FROM intents WHERE id=?', (key,)).fetchone()
            return dict(row) if row else None

    def pending(self):
        """找出待核对意图，避免超时或崩溃后重复发单。"""
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM intents WHERE status='pending'")]

    def add_intent(self, key, request, symbol):
        """在发送订单前落盘唯一意图；崩溃后按编号查询，绝不直接重试。"""
        with self.connect() as db:
            db.execute('INSERT INTO intents VALUES(?,?,?,?,?,?,?)',
                       (key, time.time(), request, symbol, 'pending', None, '{}'))

    def finish(self, key, status, result):
        """记录脱敏后的订单响应，不持久化交易所原始响应。"""
        with self.connect() as db:
            db.execute('UPDATE intents SET status=?,exchange_id=?,result=? WHERE id=?',
                       (status, result.get('order_id'), json.dumps(result, ensure_ascii=False), key))

    def save_fills(self, fills):
        """按交易对及成交编号去重保存实际成交，包含手续费与已实现盈亏。"""
        with self.connect() as db:
            for fill in fills:
                db.execute('INSERT OR REPLACE INTO fills VALUES(?,?,?,?)',
                           (fill['id'], fill['symbol'], fill['timestamp'], json.dumps(fill, ensure_ascii=False)))

    def history(self):
        """返回最近订单意图与成交记录，分页边界固定为最近五百条。"""
        with self.connect() as db:
            orders = [dict(row) for row in db.execute('SELECT * FROM intents ORDER BY created DESC LIMIT 500')]
            fills = [json.loads(row[0]) for row in db.execute('SELECT payload FROM fills ORDER BY timestamp DESC LIMIT 500')]
            for order in orders:
                order['request'] = json.loads(order['request'])
                order['result'] = json.loads(order['result'])
            remote = [json.loads(row[0]) for row in db.execute('SELECT payload FROM exchange_orders ORDER BY timestamp DESC LIMIT 500')]
            return {'orders': orders, 'fills': fills, 'exchange_orders': remote}

    def save_orders(self, orders):
        """持久化币安实际订单快照，包括尚未成交及已撤销订单。"""
        with self.connect() as db:
            for order in orders:
                db.execute('INSERT OR REPLACE INTO exchange_orders VALUES(?,?,?,?)',
                           (order['order_id'], order['symbol'], order.get('timestamp') or 0,
                            json.dumps(order, ensure_ascii=False)))

    def state_value(self, key, default=None):
        """读取同步进度，避免每次重复下载全部历史。"""
        with self.connect() as db:
            row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
            return row[0] if row else default

    def set_state(self, key, value):
        """整批成功后记录同步水位，部分失败不会跳过未同步记录。"""
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (key, str(value)))
