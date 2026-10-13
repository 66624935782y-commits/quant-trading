"""Phase 1: offline PAPER learning page."""

import json
from decimal import Decimal

import streamlit as st

from quant_trading.config import SafetyError, SafetyPolicy
from quant_trading.paper import PaperBroker

st.set_page_config(page_title="量化交易学习平台 · PAPER", page_icon="📊")
st.title("量化交易学习平台")

try:
    policy = SafetyPolicy.from_environment()
except SafetyError as exc:
    st.error(f"安全检查未通过：{exc}")
    st.stop()

st.success("PAPER 模式：仅使用虚拟资金")
st.caption("真实下单、提现、杠杆、资金转账均已关闭。本版本无需交易所 API 密钥。")
st.info("第一阶段：离线演示。BTC/USDT 固定演示价格为 50,000 USDT，不是币安实时行情。")

if "paper_broker" not in st.session_state:
    st.session_state.paper_broker = PaperBroker(policy=policy)
broker = st.session_state.paper_broker
mark = Decimal("50000")

st.subheader("练习一笔虚拟交易")
st.write("手续费：0.1%；滑点：0.05%；买入后持仓最多占账户权益的 25%。仅支持现货虚拟买卖。")
with st.form("paper_order", clear_on_submit=False):
    side = st.selectbox("交易方向", ["虚拟买入", "虚拟卖出"])
    quantity = st.number_input("BTC 数量", min_value=0.0001, max_value=1.0,
                               value=0.01, step=0.001, format="%.4f")
    submitted = st.form_submit_button("提交虚拟订单")
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
st.download_button("保存本次模拟记录（JSON）",
                   json.dumps(broker.snapshot(mark), ensure_ascii=False, indent=2),
                   file_name="paper-demo.json", mime="application/json")
st.caption("账户暂存在浏览器会话中。重启或新建会话会重新开始；离开前请下载记录。后续阶段增加自动保存与恢复。")
st.write("下一阶段：接入无需密钥的公共行情，保存原始 K 线，再展示图表。")
