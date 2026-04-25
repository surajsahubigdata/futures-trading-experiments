import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
import json
from frontend.config import FASTAPI_BASE_URL
from cachetools import cached, TTLCache
import time # Import time for auto-refresh sleep

st.set_page_config(
    page_title="Crypto Futures AI Assistant",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Streamlit caching for API calls to avoid re-fetching on small widget changes
# Adjust maxsize and ttl based on your expected usage and data update frequency
@cached(cache=TTLCache(maxsize=10, ttl=60)) # Cache results for 60 seconds
def fetch_analysis_from_backend(symbol: str, interval: str):
    try:
        response = requests.get(f"{FASTAPI_BASE_URL}/analyze/{symbol}?interval={interval}", timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        st.error("The request timed out. The backend might be busy or unresponsive.")
        return None
    except requests.exceptions.RequestException as e:
        st.error(f"Error connecting to backend: {e}. Please ensure the FastAPI server is running.")
        if response is not None and response.status_code == 400:
            st.warning(f"Backend message: {response.json().get('detail', 'Bad Request')}")
        return None
    except json.JSONDecodeError:
        st.error("Failed to decode JSON from backend. Response might be invalid.")
        return None

# --- UI Layout ---
st.title("🚀 Crypto Futures AI Trading Assistant")
st.markdown("Get real-time analysis, trading signals, and AI-powered guides for BTC and ETH perpetual futures.")

# Sidebar for controls
st.sidebar.header("Configuration")
selected_symbol = st.sidebar.selectbox("Select Pair", ["BTCUSDT", "ETHUSDT"])
selected_interval = st.sidebar.selectbox("Select Interval", ["15m", "1h", "4h", "1d"])

refresh_button = st.sidebar.button("Fetch Latest Technical Analysis", help="Click to get updated chart data and AI analysis.")

auto_refresh = st.sidebar.checkbox("Auto-Refresh (Experimental)", value=False, help="Automatically refreshes technical data every 60 seconds. May increase API usage.")
if auto_refresh:
    st.sidebar.warning("Auto-refresh is conceptual and only refreshes API calls. Full real-time requires WebSockets.")
    time.sleep(60) # Simulate refresh interval
    st.experimental_rerun() # Rerun the app to fetch new data

st.sidebar.markdown("---")
st.sidebar.header("Influencer Sentiment")
# Input for Twitter handles
twitter_handles_input = st.sidebar.text_area(
    "Enter Twitter handles (one per line, max 5)",
    value="CryptoCapo_\nMicael_D_A\nTheCryptoDog",
    height=100,
    help="Provide Twitter usernames (without '@'). Each handle on a new line."
)
twitter_handles = [h.strip() for h in twitter_handles_input.split('\n') if h.strip()]
fetch_sentiment_button = st.sidebar.button("Analyze Twitter Sentiment")

st.sidebar.markdown("---")
st.sidebar.info("Developed with FastAPI, Google Gemini AI, Plotly, and Streamlit.")


# Main content area
if refresh_button or auto_refresh: # Fetch when button is clicked or auto-refresh is active
    analysis_result = fetch_analysis_from_backend(selected_symbol, selected_interval)
    if analysis_result:
        st.session_state.last_analysis = analysis_result
        st.session_state.last_fetch_time = datetime.now()
    else:
        st.session_state.last_analysis = None
        st.session_state.last_fetch_time = None

# Displaying cached/fetched data
if 'last_analysis' in st.session_state and st.session_state.last_analysis:
    data = st.session_state.last_analysis
    
    st.markdown(f"### Analysis for {data['symbol']} ({data['interval']}) - Last Updated: {st.session_state.last_fetch_time.strftime('%Y-%m-%d %H:%M:%S')}")

    col1, col2 = st.columns([0.7, 1.3]) # Adjust column width for better display

    with col1:
        st.subheader("⚡ Signal & Key Data")
        signal = data['signal']['action']
        reason = data['signal']['reason']
        strength = data['signal']['strength']

        if signal == "BUY":
            st.success(f"**{signal}** (Confidence: {strength*100:.0f}%)")
        elif signal == "SELL":
            st.error(f"**{signal}** (Confidence: {strength*100:.0f}%)")
        else:
            st.warning(f"**{signal}** (Confidence: {strength*100:.0f}%)")
        st.markdown(f"> *{reason}*")
        
        st.markdown("---")
        st.subheader("📊 Technical Data Snapshot")
        tech_data = data['technical_data']
        st.metric(label="Current Price", value=f"${tech_data['current_price']:.2f}")
        
        st.json({
            "EMA 20": f"{tech_data['ema_20']:.2f}" if tech_data['ema_20'] else "N/A",
            "EMA 50": f"{tech_data['ema_50']:.2f}" if tech_data['ema_50'] else "N/A",
            "RSI (14)": f"{tech_data['rsi']:.2f}" if tech_data['rsi'] else "N/A",
            "MACD Histogram": f"{tech_data['macd_hist']:.2f}" if tech_data['macd_hist'] else "N/A",
            "Fib 38.2% Retracement": f"${tech_data['fib_382_retracement']:.2f}" if tech_data['fib_382_retracement'] else "N/A",
            "Fib 61.8% Retracement": f"${tech_data['fib_618_retracement']:.2f}" if tech_data['fib_618_retracement'] else "N/A",
        })

    with col2:
        st.subheader("📈 Interactive Price Chart")
        
        try:
            df_chart = pd.DataFrame(data['chart_data'])
            df_chart['open_time'] = pd.to_datetime(df_chart['open_time'])
            df_chart = df_chart.set_index('open_time').sort_index()

            candlestick_trace = go.Candlestick(
                x=df_chart.index,
                open=df_chart['open'],
                high=df_chart['high'],
                low=df_chart['low'],
                close=df_chart['close'],
                name="Candlesticks",
                increasing_line_color='green', increasing_fillcolor='darkgreen',
                decreasing_line_color='red', decreasing_fillcolor='darkred'
            )

            fig = go.Figure(data=[candlestick_trace])

            tech_data_series = pd.Series(data['technical_data'])
            if tech_data_series.get('ema_20') is not None and 'EMA_20' in df_chart.columns:
                fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['EMA_20'], mode='lines', name='EMA 20', line=dict(color='yellow', width=1)))
            if tech_data_series.get('ema_50') is not None and 'EMA_50' in df_chart.columns:
                fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['EMA_50'], mode='lines', name='EMA 50', line=dict(color='purple', width=1)))

            fib_382 = tech_data['fib_382_retracement']
            fib_618 = tech_data['fib_618_retracement']
            
            if fib_382 is not None and isinstance(fib_382, (int, float)):
                fig.add_hline(y=fib_382, line_dash="dash", line_color="lime", annotation_text=f"Fib 38.2% ({fib_382:.2f})", annotation_position="bottom right")
            if fib_618 is not None and isinstance(fib_618, (int, float)):
                fig.add_hline(y=fib_618, line_dash="dash", line_color="orange", annotation_text=f"Fib 61.8% ({fib_618:.2f})", annotation_position="top right")

            fig.update_layout(
                xaxis_rangeslider_visible=False,
                title=f"{data['symbol']} Price Chart ({data['interval']})",
                height=500,
                template="plotly_dark",
                hovermode="x unified",
                margin=dict(l=20, r=20, t=40, b=20)
            )
            st.plotly_chart(fig, use_container_width=True)
            
            st.subheader("Volume")
            volume_fig = go.Figure(data=[go.Bar(x=df_chart.index, y=df_chart['volume'], name="Volume", marker_color='grey')])
            volume_fig.update_layout(
                height=150,
                template="plotly_dark",
                showlegend=False,
                margin=dict(l=20, r=20, t=20, b=20)
            )
            st.plotly_chart(volume_fig, use_container_width=True)

        except Exception as e:
            st.error(f"Error plotting chart: {e}")
            st.json(data['chart_data'])

    st.markdown("---")
    st.subheader("🤖 AI-Powered Technical Analysis and Trading Guide")
    st.write(data['ai_analysis'])