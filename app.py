"""Phase 2: public market analysis and the isolated PAPER practice account."""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

from quant_trading.charts import candlestick_figure
from quant_trading.config import SafetyError, SafetyPolicy
from quant_trading.market_data import (
    BinancePublicSource, CandleRequest, MarketDataError, make_demo_batch,
)
from quant_trading.paper import PaperBroker
from quant_trading.storage import StorageError, list_saved_batches, load_batch, save_batch

DATA_ROOT = Path(__file__).resolve().parent / "data"
MARKETS = {"现货": "spot", "USDT 永续合约（仅行情）": "usdt_futures"}


def show_paper_account(policy: SafetyPolicy) -> None:
    st.subheader("离线虚拟账户练习")
    st.info("此账户使用固定演示价 BTC/USDT = 50,000，不是币安实时行情；行情页的切换不会改变此账户。")
    st.write("手续费 0.1%，滑点 0.05%，买入后仓位最多占权益的 25%。仅支持虚拟现货买卖。")
    if "paper_broker" not in st.session_state:
        st.session_state.paper_broker = PaperBroker(policy=policy)
    broker = st.session_state.paper_broker
    mark = Decimal("50000")
    with st.form("paper_order"):
        side = st.selectbox("交易方向", ["虚拟买入", "虚拟卖出"], key="paper_side")
        quantity = st.number_input("BTC 数量", min_value=0.0001, max_value=1.0,
                                   value=0.01, step=0.001, format="%.4f", key="paper_quantity")
        submitted = st.form_submit_button("提交虚拟订单", key="paper_submit")
    if submitted:
        try:
            broker.submit_order("buy" if side == "虚拟买入" else "sell", f"{quantity:.4f}", mark)
            st.success("虚拟订单已成交。")
        except ValueError as exc:
            st.error(str(exc))
    cash, position, equity = st.columns(3)
    cash.metric("虚拟余额（USDT）", f"{broker.cash:,.2f}")
    position.metric("虚拟持仓（BTC）", f"{broker.quantity:.4f}")
    equity.metric("账户权益（USDT）", f"{broker.equity(mark):,.2f}")
    st.subheader("本次会话的虚拟成交记录")
    if broker.fills:
        st.dataframe([fill.to_dict() for fill in broker.fills], hide_index=True)
    else:
        st.write("尚无虚拟成交。")
    st.download_button("下载模拟账户记录（JSON）",
                       json.dumps(broker.snapshot(mark), ensure_ascii=False, indent=2),
                       file_name="paper-demo.json", mime="application/json", key="paper_export")
    st.caption("账户暂存在浏览器会话中。重启或新会话会重新开始，请先下载记录；后续增加账户保存与恢复。")


st.set_page_config(page_title="Binance 量化分析平台 · PAPER", page_icon="📊", layout="wide")
st.title("Binance 量化分析平台")
try:
    policy = SafetyPolicy.from_environment()
except SafetyError as exc:
    st.error(f"安全检查未通过：{exc}")
    st.stop()
st.success("PAPER 模式：仅使用虚拟资金")
st.caption("第二阶段 · 公开行情分析｜真实下单、提现、杠杆、转账均已关闭，无需 API 密钥。")

if "market_batch" not in st.session_state:
    st.session_state.market_batch = make_demo_batch(CandleRequest())
    st.session_state.market_saved_path = None

with st.sidebar:
    st.header("行情选择")
    with st.form("market_request"):
        market = st.selectbox("市场", list(MARKETS), key="market_kind")
        symbol = st.selectbox("交易对", ["BTC/USDT", "ETH/USDT", "SOL/USDT"], key="market_symbol")
        interval = st.selectbox("K 线周期", ["1m", "5m", "15m", "1h", "4h", "1d"],
                                index=3, key="market_interval")
        limit = st.selectbox("请求 K 线根数", [200, 500, 1000], key="market_limit")
        fetch = st.form_submit_button("拉取公共行情并保存", key="market_fetch", type="primary")
        demo = st.form_submit_button("加载离线演示并保存", key="market_demo")
    st.caption("只读取最近的公开 K 线；剔除尚未收盘的 K 线。合约仅供行情分析。")
    st.write("下一阶段：策略回测、收益、最大回撤与资金曲线。")

if fetch or demo:
    try:
        request = CandleRequest(MARKETS[market], symbol, interval, limit)
        with st.spinner("正在获取并保存数据…"):
            batch = BinancePublicSource().fetch(request) if fetch else make_demo_batch(request)
            saved = save_batch(batch, DATA_ROOT)
        st.session_state.market_batch = batch
        st.session_state.market_saved_path = saved.directory
        st.success("数据已保存，可以在「数据记录」页重新加载。")
    except (MarketDataError, StorageError, OSError) as exc:
        st.error(f"本次获取或保存失败：{exc}")
        st.warning("图表保留上一次的数据，来源和参数见图表说明。未自动切换为演示行情。")

analysis_tab, records_tab, paper_tab = st.tabs(["行情分析", "数据记录", "模拟账户"])
with records_tab:
    st.subheader("本地行情记录")
    st.write("每次保存生成独立记录，包含原始行情、清洗后的 K 线和校验信息。")
    try:
        saved_batches = list_saved_batches(DATA_ROOT)
        if not saved_batches:
            st.info("还没有保存记录。点击左侧「拉取公共行情并保存」或「加载离线演示并保存」。")
        else:
            choices = {str(record.directory): record for record in saved_batches}

            def record_label(value: str) -> str:
                metadata = choices[value].metadata
                source = "币安公共行情" if metadata["source"] == "binance_public" else "离线演示"
                saved_time = datetime.fromisoformat(metadata["fetched_at"]).astimezone(
                    ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
                return (f"{source} | {metadata['market']} | {metadata['symbol']} | "
                        f"{metadata['timeframe']} | {saved_time}（北京时间） | 批次 {Path(value).name[-12:]}")

            selected = st.selectbox("选择已保存行情", list(choices), format_func=record_label,
                                    key="saved_batch_choice")
            if st.button("校验并重新加载", key="saved_load"):
                st.session_state.market_batch = load_batch(choices[selected].directory)
                st.session_state.market_saved_path = choices[selected].directory
                st.success("校验通过，已加载至「行情分析」页。")
            st.caption(f"保存位置：{DATA_ROOT}")
            st.caption("重要数据请另行备份 data 文件夹；它不包含在源码下载包中。")
    except (StorageError, OSError) as exc:
        st.error(f"本地记录无法读取：{exc}")

with analysis_tab:
    batch = st.session_state.market_batch
    if batch.source == "synthetic_demo":
        st.warning("离线演示数据：人工生成，仅用于查看图表，不代表币安真实或历史行情。")
    else:
        st.info("币安公共行情快照：本页面按需拉取，不自动刷新。以下只显示已收盘 K 线。")
    fetched = batch.fetched_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    st.caption(f"当前图表：{batch.request.market} · {batch.request.symbol} · {batch.request.timeframe} · 获取时间 {fetched}（北京时间）")
    for warning in batch.warnings:
        st.warning(warning)
    frame = batch.frame
    first, last = frame.iloc[0], frame.iloc[-1]
    price, change, high, low = st.columns(4)
    price.metric("最新已收盘价（USDT）", f"{last['close']:,.4f}")
    change.metric("所选区间涨跌", f"{(last['close'] / first['open'] - 1) * 100:+.2f}%")
    high.metric("区间最高价（USDT）", f"{frame['high'].max():,.4f}")
    low.metric("区间最低价（USDT）", f"{frame['low'].min():,.4f}")
    st.plotly_chart(candlestick_figure(frame, f"{batch.request.symbol} · {batch.request.timeframe}"),
                    width="stretch", key="market_chart")
    st.caption(f"有效已收盘 K 线：{len(frame)} 根。图表显示北京时间；区间涨跌是行情变化，不是策略收益。")
    saved_path = st.session_state.market_saved_path
    if saved_path:
        st.caption(f"当前记录已保存至：{saved_path}")
    else:
        st.caption("初始演示尚未写入磁盘。点击「加载离线演示并保存」可保留一份记录。")
    with st.expander("查看 K 线表格与下载"):
        st.dataframe(frame, hide_index=True, width="stretch")
        suffix = "demo" if batch.source == "synthetic_demo" else "binance"
        st.download_button("下载已收盘 K 线（CSV，UTC）", frame.to_csv(index=False),
                           file_name=f"{batch.request.market}-{batch.request.symbol.replace('/', '')}-{batch.request.timeframe}-{suffix}.csv",
                           mime="text/csv", key="candles_export")

with paper_tab:
    show_paper_account(policy)
