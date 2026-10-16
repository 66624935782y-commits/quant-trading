"""订单串行化、持久幂等、超时核对与后端强制风控。"""
import json
import logging
import threading
import time
import uuid
from decimal import Decimal
import ccxt
from .exchange import SYMBOLS, UserError, RejectedOrder, number

LOG = logging.getLogger('private_trader')

class Trader:
    def __init__(self, settings, db, exchange):
        """共享单进程锁，所有开仓、平仓和紧急操作串行执行。"""
        self.settings, self.db, self.ex = settings, db, exchange
        self.lock = threading.RLock()

    def audit(self, action, detail=''):
        """同时记录文件及 SQLite 审计，内容不包含密钥或原始异常。"""
        self.db.audit(action, detail)
        LOG.info('%s %s', action, detail)

    def status(self):
        """展示交易模式和后端有效风控参数。"""
        return {'live': self.settings.live, 'halted': self.db.halted(),
                'max_notional': str(self.settings.max_notional), 'daily_loss': str(self.settings.daily_loss),
                'default_leverage': self.settings.leverage, 'pending': len(self.db.pending())}

    def position_view(self, row):
        """仅选取持仓显示字段，原始币安响应不返回给浏览器。"""
        info = row.get('info') or {}
        return {'symbol': row['symbol'], 'side': row['side'], 'quantity': row['contracts'],
                'entry_price': row.get('entryPrice'), 'mark_price': row.get('markPrice'),
                'unrealized_pnl': row.get('unrealizedPnl'), 'leverage': row.get('leverage') or info.get('leverage'),
                'margin_mode': row.get('marginMode') or info.get('marginType')}

    def account(self):
        """组合真实余额、未平持仓及今日收益；任一失败不返回假数据。"""
        with self.lock:
            return {'balance': self.ex.balance(), 'positions': [self.position_view(p) for p in self.ex.positions()],
                    'pnl': self.ex.daily_pnl()}

    def clean_order(self, order):
        """提取订单状态、成交量及均价；挂单不能被误报为已成交。"""
        return {'order_id': str(order.get('id') or ''), 'symbol': order.get('symbol'),
                'category': order.get('category', 'ordinary'), 'status': order.get('status') or 'unknown', 'filled': order.get('filled'),
                'timestamp': order.get('timestamp'), 'side': order.get('side'), 'quantity': order.get('amount'),
                'remaining': order.get('remaining'), 'average': order.get('average'), 'price': order.get('price')}

    def reconcile(self):
        """按原客户端编号查询未确认订单；查不到也保留锁，绝不再次发送。"""
        with self.lock:
            for row in self.db.pending():
                try:
                    order = self.ex.call('fetch_order', None, row['symbol'], {'origClientOrderId': row['id']})
                    self.db.finish(row['id'], 'confirmed', self.clean_order(order))
                    self.db.save_orders([self.clean_order(order)])
                    self.audit('订单核对成功', row['id'])
                except (ccxt.BaseError, UserError, OSError):
                    self.audit('订单仍未确认', row['id'])
            return {'pending': len(self.db.pending())}

    def key(self, supplied):
        """使用 UUID 客户端编号，不允许客户端构造任意查询参数。"""
        try:
            return 'aq' + uuid.UUID(supplied).hex
        except (ValueError, AttributeError):
            raise UserError('请求编号无效，请刷新页面重新操作。') from None

    def replay(self, key, request):
        """相同编号只返回原结果，不同内容复用编号时明确拒绝。"""
        row = self.db.intent(key)
        if not row:
            return None
        if row['request'] != request:
            raise UserError('该请求编号已用于其他操作，请核对历史记录。')
        if row['status'] == 'pending':
            self.reconcile()
            row = self.db.intent(key)
        if row['status'] == 'pending':
            raise UserError('订单结果尚未确认，已锁住新订单。请在币安核对，并点击核对订单；不要重复下单。')
        return {'intent': key, 'state': row['status'], **json.loads(row['result'])}

    def ensure_clear(self):
        """恢复重启或超时遗留订单，未查清前阻止继续发单。"""
        if self.db.pending():
            self.reconcile()
        if self.db.pending():
            raise UserError('存在未确认订单，禁止继续发单；请先在币安核对，再使用核对订单功能。')

    def amount(self, symbol, quantity, price, closing=False, market_order=True):
        """按真实精度向下截断，验证 LOT_SIZE、MARKET_LOT_SIZE、最小名义价值。"""
        self.ex.markets()
        market = self.ex.client.market(symbol)
        try:
            amount = number(self.ex.client.amount_to_precision(symbol, str(quantity)))
        except ccxt.BaseError:
            raise UserError('数量低于该合约的精度要求。') from None
        size = number(market.get('contractSize') or 1)
        if amount <= 0 or amount > quantity:
            raise UserError('数量无法满足合约精度，不能下单。')
        limits = market.get('limits') or {}
        low = (limits.get('amount') or {}).get('min')
        high = (limits.get('amount') or {}).get('max')
        if low is not None and amount < number(low):
            raise UserError(f'数量低于交易所最小值 {low}。')
        if high is not None and amount > number(high):
            raise UserError(f'数量超过交易所最大值 {high}。')
        if price <= 0:
            raise UserError('价格必须大于零。')
        for rule in market.get('info', {}).get('filters', []):
            if not market_order and rule.get('filterType') == 'PRICE_FILTER':
                tick = number(rule.get('tickSize', '0'))
                low_price = number(rule.get('minPrice', '0'))
                high_price = number(rule.get('maxPrice', '0'))
                if price < low_price or (high_price > 0 and price > high_price) or (tick > 0 and price % tick != 0):
                    raise UserError('限价不符合币安价格范围或价格步长。')
            if rule.get('filterType') in (('LOT_SIZE', 'MARKET_LOT_SIZE') if market_order else ('LOT_SIZE',)):
                step = number(rule.get('stepSize', '0'))
                minimum = number(rule.get('minQty', '0'))
                maximum = number(rule.get('maxQty', '0'))
                if amount < minimum or (maximum > 0 and amount > maximum) or (step > 0 and amount % step != 0):
                    raise UserError('数量不满足币安数量步长或数量范围。')
        cost = amount * size * price
        min_cost = (limits.get('cost') or {}).get('min')
        for rule in market.get('info', {}).get('filters', []):
            if rule.get('filterType') in ('MIN_NOTIONAL', 'NOTIONAL'):
                value = rule.get('notional') or rule.get('minNotional')
                if value is not None:
                    min_cost = max(number(min_cost or 0), number(value))
        max_cost = (limits.get('cost') or {}).get('max')
        if max_cost is not None and cost > number(max_cost):
            raise UserError('名义价值超过交易所单笔上限。')
        if not closing and min_cost is not None and cost < number(min_cost):
            raise UserError(f'该交易对最小名义价值为 {min_cost} USDT；当前有效名义价值 {cost} USDT。不会自动增加金额。')
        return amount, cost

    def submit(self, key, payload, symbol, side, amount, kind, price, params):
        """先保存意图，再只发送一次订单；网络失败立即查询原订单。"""
        request = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        old = self.replay(key, request)
        if old:
            return old
        self.db.add_intent(key, request, symbol)
        if not self.settings.live:
            result = {'message': '演练：未发送订单，也未修改币安杠杆或保证金模式。',
                      'symbol': symbol, 'side': side, 'quantity': str(amount), 'price': str(price) if price else None}
            self.db.finish(key, 'dry_run', result)
            self.audit('演练订单', key)
            return {'intent': key, 'state': 'dry_run', **result}
        try:
            order = self.ex.call('create_order', symbol, kind, side, float(amount), float(price) if price else None,
                                 {**params, 'newClientOrderId': key})
        except RejectedOrder as exc:
            result = {'message': str(exc)}
            self.db.finish(key, 'rejected', result)
            self.audit('订单被明确拒绝', key)
            return {'intent': key, 'state': 'rejected', **result}
        except (ccxt.NetworkError, OSError):
            self.audit('订单网络异常，开始查询', key)
            self.reconcile()
            result = self.replay(key, request)
            return result
        except Exception:
            # 包括业务拒绝在内都保守保留待确认状态，避免误判交易所已收到的请求。
            self.audit('订单异常，保留核对锁', key)
            self.reconcile()
            return self.replay(key, request)
        result = self.clean_order(order)
        self.db.finish(key, 'confirmed', result)
        self.db.save_orders([result])
        try:
            self.capture_fills(symbol, int(time.time()*1000)-60000, int(time.time()*1000))
        except (ccxt.BaseError, UserError, OSError):
            result['history_warning'] = '订单已确认，但成交明细同步失败，请点击同步记录。'
        self.audit('交易所确认订单', key)
        return {'intent': key, 'state': 'confirmed', **result}

    def open(self, data):
        """执行完整开仓前校验，金额不足时拒绝，不自动加杠杆或增加数量。"""
        with self.lock:
            payload = {'action': 'open', **data}
            key = self.key(data['request_id'])
            old = self.replay(key, json.dumps(payload, sort_keys=True, ensure_ascii=False))
            if old:
                return old
            self.ensure_clear()
            if self.db.halted():
                raise UserError('紧急停止锁已生效，禁止开新仓。')
            symbol = SYMBOLS[data['symbol']]
            requested = number(data['notional'])
            if requested <= 0 or requested > self.settings.max_notional:
                raise UserError(f'单笔名义价值必须大于零且不能超过 {self.settings.max_notional} USDT。')
            pnl = self.ex.daily_pnl()
            if number(pnl['net']) <= -self.settings.daily_loss:
                raise UserError('已达到 UTC 今日净亏损限制，禁止开新仓；仍可平仓和撤单。')
            self.ex.markets()
            ticker = self.ex.call('fetch_ticker', symbol)
            last = number(ticker.get('last'))
            if last <= 0:
                raise UserError('交易所价格无效。')
            price = None
            if data['kind'] == 'limit':
                raw_price = number(data.get('price'))
                if raw_price <= 0:
                    raise UserError('限价必须大于零。')
                try:
                    price = number(self.ex.client.price_to_precision(symbol, str(raw_price)))
                except ccxt.BaseError:
                    raise UserError('限价不符合交易所精度。') from None
                if price != raw_price:
                    raise UserError('限价不符合交易所价格步长，请修改后重新确认。')
            # 市价按最新价估算；最终成交可能滑点，文档明确金额上限是发单前估算。
            reference = price if price is not None else last
            market = self.ex.client.market(symbol)
            if market.get('active') is False:
                raise UserError('该合约当前不可交易。')
            quantity = requested / reference / number(market.get('contractSize') or 1)
            amount, cost = self.amount(symbol, quantity, reference, market_order=data['kind'] == 'market')
            # 用实际限价与当前价较高值计算保守保证金，并预留 1% 费用/价格缓冲。
            conservative = amount * number(market.get('contractSize') or 1) * max(reference, last)
            margin = conservative / data['leverage'] + conservative * Decimal('0.01')
            if number(self.ex.balance()['free']) < margin:
                raise UserError(f'可用保证金不足，含缓冲至少需要 {margin:.4f} USDT。')
            hedged = self.ex.mode()
            positions = [p for p in self.ex.positions() if p['symbol'] == symbol]
            if any((p.get('marginMode') or p.get('info', {}).get('marginType')) not in ('isolated',) for p in positions):
                raise UserError('该合约存在非逐仓持仓，请先在币安处理；程序不会自动切换已有持仓。')
            if positions:
                # 单向模式反向下单可能平仓而非开仓，拒绝含糊的操作。
                desired = 'long' if data['direction'] == 'long' else 'short'
                if not hedged and any(p['side'] != desired for p in positions):
                    raise UserError('单向持仓模式下存在反向仓位，请先使用平仓功能处理。')
                for p in positions:
                    lev = p.get('leverage') or p.get('info', {}).get('leverage')
                    if number(lev) != data['leverage']:
                        raise UserError('已有持仓的杠杆与所选不同；为避免影响已有仓位，请在币安处理。')
            if any(o['symbol'] == symbol for o in self.ex.open_orders()):
                raise UserError('该合约存在未成交挂单，请先核对或撤单后再开仓。')
            if self.settings.live and not positions:
                # 无持仓、无挂单才设置用户明确选择的逐仓与杠杆；任何失败都不继续下单。
                self.ex.call('set_margin_mode', 'isolated', symbol)
                self.ex.call('set_leverage', data['leverage'], symbol)
            params = {'positionSide': ('LONG' if data['direction'] == 'long' else 'SHORT')} if hedged else {}
            if data['kind'] == 'limit':
                params['timeInForce'] = 'GTC'
            return self.submit(key, payload, symbol, 'buy' if data['direction'] == 'long' else 'sell',
                               amount, data['kind'], price, params)

    def close(self, data):
        """按最新实际持仓数量平仓；部分平仓按比例截断，永不反手开仓。"""
        with self.lock:
            payload = {'action': 'close', **data}
            key = self.key(data['request_id'])
            old = self.replay(key, json.dumps(payload, sort_keys=True, ensure_ascii=False))
            if old:
                return old
            self.ensure_clear()
            rows = [p for p in self.ex.positions() if p['symbol'] == data['symbol'] and p['side'] == data['side']]
            if len(rows) != 1:
                raise UserError('未找到唯一对应仓位，请刷新后核对。')
            position = rows[0]
            quantity = number(position['contracts']) * number(data['percent']) / 100
            price = number(position.get('markPrice'))
            amount, _ = self.amount(data['symbol'], quantity, price, closing=True)
            hedged = self.ex.mode()
            params = {'positionSide': data['side'].upper()} if hedged else {'reduceOnly': True}
            return self.submit(key, payload, data['symbol'], 'sell' if data['side'] == 'long' else 'buy',
                               amount, 'market', None, params)

    def cancel_all(self):
        """逐个合约撤销普通及条件未成交订单，失败逐项报告，不宣称全部成功。"""
        with self.lock:
            orders = self.ex.open_orders()
            targets = sorted({(o['symbol'], o.get('category', 'ordinary')) for o in orders})
            results = []
            for symbol, category in targets:
                if not self.settings.live:
                    results.append({'symbol': symbol, 'state': 'dry_run', 'message': '演练：未撤销真实挂单'})
                    continue
                try:
                    self.ex.call('cancel_all_orders', symbol, {'trigger': True} if category == 'conditional' else {})
                    results.append({'symbol': symbol, 'state': 'cancel_requested'})
                except (ccxt.BaseError, UserError, OSError):
                    results.append({'symbol': symbol, 'state': 'error', 'message': '撤单未确认，请在币安核对'})
            remaining = self.ex.open_orders() if self.settings.live else orders
            self.audit('撤单操作', f'涉及 {len(targets)} 组合约挂单，剩余 {len(remaining)} 笔')
            return {'results': results, 'remaining': len(remaining), 'live': self.settings.live}

    def emergency(self):
        """先落盘禁止开仓，再尝试撤单与逐仓平仓；任何部分失败都明确返回。"""
        with self.lock:
            self.db.halt(True)
            self.audit('紧急停止锁定')
            results = []
            try:
                results.append({'cancel': self.cancel_all()})
            except (ccxt.BaseError, UserError, OSError):
                results.append({'error': '撤单查询失败，请立即在币安检查挂单。'})
            try:
                positions = self.ex.positions()
            except (ccxt.BaseError, UserError, OSError):
                return {'halted': True, 'complete': False, 'results': results, 'message': '无法读取持仓，请立即在币安操作。'}
            for p in positions:
                try:
                    results.append(self.close({'symbol': p['symbol'], 'side': p['side'], 'percent': '100', 'request_id': str(uuid.uuid4())}))
                except (ccxt.BaseError, UserError, OSError):
                    results.append({'symbol': p['symbol'], 'error': '平仓未确认或被未确认订单锁定，请立即在币安核对。'})
            try:
                remaining_positions = len(self.ex.positions())
                remaining_orders = len(self.ex.open_orders())
                complete = self.settings.live and not remaining_positions and not remaining_orders and not self.db.pending()
            except (ccxt.BaseError, UserError, OSError):
                complete = False
            return {'halted': True, 'complete': complete, 'live': self.settings.live, 'results': results,
                    'message': '禁止开仓已持久化。只有 complete=true 才表示复查时无仓位和普通及条件挂单；否则请立即在币安处理。'}

    def capture_fills(self, symbol, start, end):
        """按成交编号完整分页，跨时间窗的记录不冒充本窗口成交。"""
        from_id, count = None, 0
        for _ in range(100):
            params = {'endTime': end} if from_id is None else {'fromId': from_id}
            rows = self.ex.call('fetch_my_trades', symbol, start if from_id is None else None, 1000, params)
            fills = []
            for r in rows:
                if not start <= r['timestamp'] <= end:
                    continue
                info, fee = r.get('info') or {}, r.get('fee') or {}
                fills.append({'id': str(r['id']), 'symbol': symbol, 'timestamp': r['timestamp'],
                    'order_id': str(r['order']), 'side': r['side'], 'quantity': r['amount'], 'price': r['price'],
                    'fee': fee.get('cost'), 'fee_asset': fee.get('currency'),
                    'realized_pnl': info.get('realizedPnl'), 'position_side': info.get('positionSide')})
            self.db.save_fills(fills)
            count += len(fills)
            if len(rows) < 1000 or rows[-1]['timestamp'] > end:
                return count
            next_id = int(rows[-1]['id'])+1
            if from_id is not None and next_id <= from_id:
                raise UserError('币安成交分页未前进，已停止同步，本批已保存。')
            from_id = next_id
        raise UserError('历史成交超过单批范围，本批已保存，请在币安导出核对。')

    def capture_orders(self, symbol, start, end):
        """分页保存币安普通历史订单；已过交易所保留期的订单无法恢复。"""
        order_id, count = None, 0
        for _ in range(100):
            params = {'endTime': end} if order_id is None else {'orderId': order_id}
            rows = self.ex.call('fetch_orders', symbol, start if order_id is None else None, 1000, params)
            safe = [self.clean_order(r) for r in rows if start <= (r.get('timestamp') or 0) <= end]
            self.db.save_orders(safe)
            count += len(safe)
            if len(rows) < 1000 or (rows[-1].get('timestamp') or 0) > end:
                return count
            next_id = int(rows[-1]['id'])+1
            if order_id is not None and next_id <= order_id:
                raise UserError('币安订单分页未前进，已停止同步。')
            order_id = next_id
        raise UserError('订单过多，本批已保存，请在币安导出核对。')

    def history(self):
        """刷新本机非终态订单快照，再返回已保存的历史，不伪造成交。"""
        with self.lock:
            for row in self.db.history()['orders']:
                result = row['result']
                if row['status'] != 'confirmed' or result.get('status') in ('closed', 'canceled', 'rejected', 'expired'):
                    continue
                try:
                    order = self.ex.call('fetch_order', row['exchange_id'], row['symbol'])
                    safe = self.clean_order(order)
                    self.db.finish(row['id'], 'confirmed', safe)
                    self.db.save_orders([safe])
                    self.capture_fills(row['symbol'], max(int(row['created']*1000), int(time.time()*1000)-7*86400000), int(time.time()*1000))
                except (ccxt.BaseError, UserError, OSError):
                    self.audit('历史订单刷新失败', row['id'])
            return self.db.history()

    def sync(self):
        """初次回填九十天，以后重叠一天增量同步，记录和进度在 SQLite 长期保存。"""
        with self.lock:
            self.ex.private()
            self.ex.markets()
            now = int(time.time()*1000)
            fills, orders = 0, 0
            symbols = set(SYMBOLS.values()) | {p['symbol'] for p in self.ex.positions()}
            for symbol in sorted(symbols):
                cursor = int(self.db.state_value('sync:'+symbol, now-90*86400000))
                start = max(now-90*86400000, cursor-86400000)
                while start < now:
                    end = min(start+7*86400000-1, now)
                    orders += self.capture_orders(symbol, start, end)
                    fills += self.capture_fills(symbol, start, end)
                    start = end+1
                self.db.set_state('sync:'+symbol, now)
            self.audit('同步订单与成交完成', f'{orders} 订单，{fills} 成交（含重叠核对）')
            return {'orders': orders, 'fills': fills,
                    'message': '已同步交易所可获取的最近 90 天普通订单与成交；本地记录长期保留，网页显示最近 500 条。币安保留期外及历史 Algo 订单请在币安导出核对。'}
