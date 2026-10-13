"""Plot validated candles in the user's Shanghai timezone."""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def candlestick_figure(frame: pd.DataFrame, title: str) -> go.Figure:
    times = frame["timestamp"].dt.tz_convert("Asia/Shanghai")
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True,
                           row_heights=[0.75, 0.25], vertical_spacing=0.05)
    figure.add_trace(go.Candlestick(
        x=times, open=frame["open"], high=frame["high"], low=frame["low"],
        close=frame["close"], name="K 线", increasing_line_color="#16a085",
        decreasing_line_color="#e74c3c",
    ), row=1, col=1)
    figure.add_trace(go.Bar(
        x=times, y=frame["volume"], name="成交量",
        marker_color=["#16a085" if close >= opened else "#e74c3c"
                      for close, opened in zip(frame["close"], frame["open"])],
    ), row=2, col=1)
    figure.update_layout(title=title, height=600, showlegend=False,
                         template="plotly_white", margin=dict(l=10, r=10, t=55, b=20),
                         xaxis_rangeslider_visible=False)
    figure.update_yaxes(title_text="价格（USDT）", row=1, col=1)
    figure.update_yaxes(title_text="成交量", row=2, col=1)
    figure.update_xaxes(title_text="北京时间（UTC+8）", row=2, col=1)
    return figure
