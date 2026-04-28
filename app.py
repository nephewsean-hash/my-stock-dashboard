# -*- coding: utf-8 -*-
"""
관심종목 대시보드 (Streamlit).

[실행 방법]
    터미널에서:
    streamlit run app.py --server.address 0.0.0.0 --server.port 8501

[핸드폰 접속]
    1. PC에서 터미널 열고 IP 확인: (Windows) ipconfig / (Mac) ifconfig
    2. 예: 192.168.0.10 이면, 같은 Wi-Fi의 핸드폰 브라우저에서
       http://192.168.0.10:8501 접속
"""

import time
from datetime import datetime

import pandas as pd
import streamlit as st

import config
from data_fetcher import (
    get_ohlcv_cached, is_market_open, is_valid_ticker,
    search_stock_by_name, get_stock_info, classify_sector,
    get_target_price, get_stock_news, get_realtime_price,
)
from indicators import generate_signal

# =========================================================================
# 페이지 설정
# =========================================================================
st.set_page_config(
    page_title="나의 관심종목",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# 커스텀 CSS (다크 테마 + 모바일 반응형)
st.markdown(
    """
    <style>
    /* 섹터 헤더 */
    .sector-header {
        background-color: #ff8c00;
        color: white;
        padding: 6px 12px;
        font-weight: bold;
        border-radius: 4px;
        margin: 12px 0 4px 0;
        font-size: 16px;
    }

    /* ============================================ */
    /* 모바일 반응형 (iPhone 12 Pro: 390px 기준)    */
    /* ============================================ */

    /* 전체 패딩 축소 */
    @media (max-width: 768px) {
        .block-container {
            padding: 0.5rem 0.8rem !important;
            max-width: 100% !important;
        }

        /* 제목 크기 축소 */
        h1 {
            font-size: 1.4rem !important;
        }
        h3 {
            font-size: 1.1rem !important;
        }
        h4 {
            font-size: 1rem !important;
        }

        /* 데이터프레임 스크롤 가능하게 */
        [data-testid="stDataFrame"] {
            overflow-x: auto !important;
            -webkit-overflow-scrolling: touch;
        }
        [data-testid="stDataFrame"] table {
            font-size: 11px !important;
            min-width: 600px;
        }
        [data-testid="stDataFrame"] th,
        [data-testid="stDataFrame"] td {
            padding: 4px 6px !important;
            white-space: nowrap;
        }

        /* 버튼 크기 모바일 최적화 */
        .stButton > button {
            font-size: 12px !important;
            padding: 4px 8px !important;
            min-height: 32px !important;
        }

        /* 시그널 카드 텍스트 */
        .stExpander {
            font-size: 14px;
        }

        /* 컬럼 간격 축소 */
        [data-testid="column"] {
            padding: 0 4px !important;
        }

        /* 캡션 텍스트 */
        .stCaption, small {
            font-size: 11px !important;
        }

        /* 삭제 버튼 줄바꿈 */
        [data-testid="column"] .stButton > button {
            width: 100% !important;
            margin-bottom: 4px !important;
        }
    }

    /* 아이폰 SE ~ 12 Pro 세로 모드 */
    @media (max-width: 430px) {
        .block-container {
            padding: 0.3rem 0.5rem !important;
        }
        h1 {
            font-size: 1.2rem !important;
        }
        [data-testid="stDataFrame"] table {
            font-size: 10px !important;
        }

        /* 시그널 패널 세로 배치 */
        [data-testid="column"] {
            min-width: 100% !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =========================================================================
# 캐싱: 5분마다 데이터 갱신
# =========================================================================
@st.cache_data(ttl=config.CACHE_TTL, show_spinner=False)
def load_stock_data(ticker: str):
    """종목 하나의 시그널 계산."""
    df = get_ohlcv_cached(ticker, config.LOOKBACK_DAYS)
    if df is None or df.empty:
        return None
    return generate_signal(df, config)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_search(query: str):
    """종목 검색 결과 캐싱 (1시간)."""
    return search_stock_by_name(query)


# =========================================================================
# 메인 렌더링
# =========================================================================
st.title("📈 나의 관심종목")

market_status = "🟢 장중" if is_market_open() else "🔴 장외"
col_a, col_b, col_c = st.columns([2, 2, 1])
with col_a:
    st.caption(f"현재: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {market_status}")
with col_b:
    if is_market_open():
        st.caption("📌 현재가: 네이버 금융 (실시간) | 지표: pykrx (장중 15분 갱신)")
    else:
        st.caption("📌 데이터 출처: pykrx (KRX/네이버, 전일 종가 기준)")
with col_c:
    # 삭제 대기열 초기화
    if "pending_deletes" not in st.session_state:
        st.session_state.pending_deletes = set()
    pending = st.session_state.pending_deletes

    if st.button("🔄 새로고침"):
        # 대기열에 있는 종목 실제 삭제
        if pending:
            for tkr in pending:
                config.remove_stock(tkr)
            st.session_state.pending_deletes = set()
        st.cache_data.clear()
        config.WATCHLIST = config.load_watchlist()
        st.rerun()

# =========================================================================
# 종목 검색 & 추가 / 삭제
# =========================================================================
existing_sectors = list(config.WATCHLIST.keys())

with st.expander("🔍 종목 검색 & 추가 / 삭제", expanded=False):
    tab_add, tab_remove = st.tabs(["➕ 종목 추가", "➖ 종목 삭제"])

    with tab_add:
        add_mode = st.radio("추가 방식:", ["종목명 검색", "종목코드 직접 입력"], horizontal=True, key="add_mode")

        sel_name = None
        sel_ticker = None
        stock_info = None

        if add_mode == "종목명 검색":
            search_query = st.text_input("종목명 검색 (예: 더블유씨피, 셀트리온, 카카오)", key="search_input")

            if search_query and len(search_query) >= 2:
                with st.spinner("네이버증권에서 검색 중..."):
                    search_results = cached_search(search_query)

                if search_results:
                    all_existing_tickers = set()
                    for sector_stocks in config.WATCHLIST.values():
                        all_existing_tickers.update(sector_stocks.keys())

                    options = []
                    for ticker, name in search_results:
                        tag = " ✅ (이미 추가됨)" if ticker in all_existing_tickers else ""
                        options.append(f"{name} ({ticker}){tag}")

                    selected = st.selectbox("검색 결과에서 선택:", options, key="search_result")

                    if selected and "이미 추가됨" not in selected:
                        sel_name = selected.split(" (")[0]
                        sel_ticker = selected.split("(")[1].split(")")[0]
                else:
                    st.warning(f"'{search_query}' 검색 결과 없음. 영문명(예: RFHIC)은 **종목코드 직접 입력**을 이용해주세요.")

        else:  # 종목코드 직접 입력
            manual_ticker = st.text_input("종목코드 6자리 입력 (예: 456040)", key="manual_ticker")
            if manual_ticker and len(manual_ticker) == 6 and manual_ticker.isdigit():
                sel_ticker = manual_ticker
            elif manual_ticker:
                st.error("종목코드는 6자리 숫자여야 합니다. (예: 456040)")

        # 종목 정보 조회 & 자동 분류
        if sel_ticker:
            with st.spinner("종목 정보 조회 & 섹터 분류 중..."):
                stock_info = get_stock_info(sel_ticker)

            if stock_info:
                sel_name = stock_info["name"]
                business_desc = f"{stock_info.get('sector_name', '')} {stock_info.get('business', '')}"
                auto_sector = classify_sector(sel_name, business_desc)

                st.success(f"**{sel_name}** ({sel_ticker}) → 자동 분류: **{auto_sector}**")
                if stock_info.get("sector_name") and stock_info["sector_name"] not in ("KOSPI", "KOSDAQ"):
                    st.caption(f"네이버 업종: {stock_info['sector_name']}")

                # 자동 추가 버튼 (기본)
                col_auto, col_manual = st.columns([1, 1])
                with col_auto:
                    if st.button(f"✅ [{auto_sector}]에 추가", key="add_auto_btn"):
                        config.add_stock(sel_ticker, sel_name, auto_sector)
                        config.WATCHLIST = config.load_watchlist()
                        st.cache_data.clear()
                        st.rerun()

                # 섹터 직접 지정 (옵션)
                with col_manual:
                    with st.popover("섹터 직접 지정"):
                        sector_options = existing_sectors + ["➕ 새 섹터 직접 입력"]
                        sector_choice = st.selectbox("섹터:", sector_options, key="sector_select")

                        if sector_choice == "➕ 새 섹터 직접 입력":
                            final_sector = st.text_input("새 섹터명:", key="new_sector")
                        else:
                            final_sector = sector_choice

                        if final_sector and st.button(f"✅ [{final_sector}]에 추가", key="add_manual_btn"):
                            config.add_stock(sel_ticker, sel_name, final_sector)
                            config.WATCHLIST = config.load_watchlist()
                            st.cache_data.clear()
                            st.rerun()
            else:
                st.error("해당 종목을 찾을 수 없습니다.")

    with tab_remove:
        # 삭제할 종목 선택
        remove_options = []
        for sector, stocks in config.WATCHLIST.items():
            for ticker, name in stocks.items():
                remove_options.append(f"[{sector}] {name} ({ticker})")

        if remove_options:
            selected_remove = st.selectbox("삭제할 종목 선택:", remove_options, key="remove_select")
            if st.button("🗑️ 선택 종목 삭제", key="remove_btn"):
                rm_ticker = selected_remove.split("(")[1].split(")")[0]
                rm_name = selected_remove.split("] ")[1].split(" (")[0]
                config.remove_stock(rm_ticker)
                config.WATCHLIST = config.load_watchlist()
                st.cache_data.clear()
                st.success(f"'{rm_name}' ({rm_ticker})을(를) 삭제했습니다!")
                st.rerun()
        else:
            st.info("등록된 종목이 없습니다.")

# -----------------------------------------------------------------
# 1단계: 모든 종목 시그널 계산
# -----------------------------------------------------------------
all_results = {}
todo_list = []
error_list = []

watchlist = config.load_watchlist()

progress_bar = st.progress(0, text="데이터 불러오는 중...")
total_count = sum(len(tickers) for tickers in watchlist.values())
counter = 0

for sector, tickers in watchlist.items():
    all_results[sector] = {}
    for ticker, name in tickers.items():
        counter += 1
        progress_bar.progress(counter / total_count, text=f"{name} 계산 중...")

        if not is_valid_ticker(ticker):
            # TODO_VERIFY 종목
            todo_list.append((sector, ticker, name))
            all_results[sector][ticker] = {"name": name, "status": "TODO"}
            continue

        signal_data = load_stock_data(ticker)
        if signal_data is None:
            error_list.append((sector, ticker, name))
            all_results[sector][ticker] = {"name": name, "status": "ERROR"}
            continue

        # 증권사 목표가 조회
        target_info = get_target_price(ticker)
        target_price = target_info["target_price"] if target_info else None
        target_opinion = target_info.get("opinion", "") if target_info else ""
        target_broker_count = target_info.get("broker_count", 0) if target_info else 0
        target_date = target_info.get("report_date", "") if target_info else ""

        # 최신 뉴스 조회
        news = get_stock_news(ticker, count=3)

        # 장중이면 네이버에서 실시간 현재가 반영
        if is_market_open():
            rt = get_realtime_price(ticker)
            if rt:
                signal_data["current_price"] = rt["price"]
                signal_data["change_pct"] = rt["change_pct"]

        all_results[sector][ticker] = {
            "name": name, "status": "OK",
            "target_price": target_price, "target_opinion": target_opinion,
            "target_broker_count": target_broker_count, "target_date": target_date,
            "news": news,
            **signal_data,
        }

progress_bar.empty()

# -----------------------------------------------------------------
# 2단계: 상단 시그널 알림 패널
# -----------------------------------------------------------------
# 섹터별로 시그널 그룹핑
buy_by_sector = {}
sell_by_sector = {}
for sector, stocks in all_results.items():
    for ticker, info in stocks.items():
        if info.get("status") != "OK":
            continue
        entry = info.copy()
        entry["ticker"] = ticker
        entry["sector"] = sector
        sig = info.get("signal", "")
        if sig in ("STRONG_BUY", "BUY"):
            buy_by_sector.setdefault(sector, []).append(entry)
        elif sig in ("SELL", "REDUCE"):
            sell_by_sector.setdefault(sector, []).append(entry)


def render_signal_card(info: dict):
    """시그널 카드 한 종목 렌더링."""
    name = info["name"]
    tkr = info["ticker"]
    cp = info.get("current_price")
    try:
        cp = int(cp) if cp is not None else None
    except (ValueError, TypeError):
        cp = None
    raw_change = info.get("change_pct")
    try:
        change = float(raw_change) if raw_change is not None else 0.0
    except (ValueError, TypeError):
        change = 0.0
    reason = info.get("signal_reason", "")

    # 전일가 계산
    prev_price = int(cp / (1 + change / 100)) if cp and change else None

    # 목표가 정보
    tp = info.get("target_price")
    try:
        tp = int(tp) if tp is not None else None
    except (ValueError, TypeError):
        tp = None
    tp_count = info.get("target_broker_count", 0)
    tp_date = info.get("target_date", "")
    tp_opinion = info.get("target_opinion", "")

    # 헤더: 종목명 (코드) + 목표가
    header = f"**{name}** ({tkr})"
    if tp:
        tp_tag = f" | 목표가 **{tp:,}원**"
        tp_details = []
        if tp_count:
            tp_details.append(f"{tp_count}개 증권사")
        if tp_opinion:
            tp_details.append(tp_opinion)
        if tp_date:
            tp_details.append(f"{tp_date} 기준")
        if tp_details:
            tp_tag += f" ({', '.join(tp_details)})"
        header += tp_tag
    st.markdown(header)

    # 첫줄: 전일가 / 현재가 / 등락률
    if cp:
        change_color = "🔴" if change > 0 else ("🔵" if change < 0 else "⚪")
        prev_str = f"{prev_price:,}" if prev_price else "?"
        price_line = f"{change_color} 전일 {prev_str}원 → 현재 **{cp:,}원** ({change:+.2f}%)"
        if tp and cp:
            gap = ((tp - cp) / cp) * 100
            price_line += f" | 목표가 대비 {gap:+.1f}%"
        st.caption(price_line)

    # 시그널 사유
    st.markdown(f"{reason}")

    # 뉴스 1건 (핵심만)
    news = info.get("news", [])
    if news:
        n = news[0]
        date_tag = f" ({n['date']})" if n.get("date") else ""
        source_tag = f" {n['source']}" if n.get("source") else ""
        if n.get("url"):
            st.markdown(f"📰 [{n['title']}]({n['url']}){date_tag}{source_tag}")
        else:
            st.caption(f"📰 {n['title']}{date_tag}")

    st.markdown("---")


if buy_by_sector or sell_by_sector:
    st.markdown("### 🚨 오늘의 시그널")
    col_buy, col_sell = st.columns(2)

    with col_buy:
        st.markdown("#### 🟢 매수 관심")
        if buy_by_sector:
            for sec, items in buy_by_sector.items():
                sec_display = sec.replace("_", " ")
                with st.expander(f"**{sec_display}** ({len(items)}종목)", expanded=False):
                    for info in items:
                        render_signal_card(info)
        else:
            st.caption("해당 없음")

    with col_sell:
        st.markdown("#### 🔴 매도 관심")
        if sell_by_sector:
            for sec, items in sell_by_sector.items():
                sec_display = sec.replace("_", " ")
                with st.expander(f"**{sec_display}** ({len(items)}종목)", expanded=False):
                    for info in items:
                        render_signal_card(info)
        else:
            st.caption("해당 없음")

    st.markdown("---")

st.warning(
    "⚠️ 이 시그널은 **기술적 지표(RSI, 이동평균)의 기계적 계산 결과**일 뿐입니다. "
    "투자 결정과 그 결과에 대한 책임은 전적으로 본인에게 있습니다."
)

# -----------------------------------------------------------------
# 3단계: 섹터별 종목 테이블
# -----------------------------------------------------------------
sector_keys = list(all_results.keys())
for sect_idx, sector in enumerate(sector_keys):
    stocks = all_results[sector]
    sector_display = sector.replace("_", " ")

    # 섹터 헤더 + 순서 변경 버튼
    hdr_col1, hdr_col2, hdr_col3 = st.columns([10, 1, 1])
    with hdr_col1:
        st.markdown(f'<div class="sector-header">{sector_display}</div>', unsafe_allow_html=True)
    with hdr_col2:
        if sect_idx > 0:
            if st.button("⬆", key=f"up_{sector}", help="위로 이동"):
                config.move_sector(sector, "up")
                config.WATCHLIST = config.load_watchlist()
                st.rerun()
    with hdr_col3:
        if sect_idx < len(sector_keys) - 1:
            if st.button("⬇", key=f"down_{sector}", help="아래로 이동"):
                config.move_sector(sector, "down")
                config.WATCHLIST = config.load_watchlist()
                st.rerun()

    rows = []
    for ticker, info in stocks.items():
        empty_row = {
            "종목명": "", "코드": "", "목표가": "-", "현재가": "-", "등락률": "-",
            "RSI": "-", "매물대": "-", "괴리율": "-",
            "시그널": "", "사유": "",
        }
        if info.get("status") == "TODO":
            rows.append({**empty_row, "종목명": info["name"], "코드": "⚠️확인필요",
                         "시그널": "TODO", "사유": "종목코드 확인 필요"})
        elif info.get("status") == "ERROR":
            rows.append({**empty_row, "종목명": info["name"], "코드": ticker,
                         "현재가": "데이터없음", "시그널": "ERROR", "사유": "KRX 조회 실패"})
        else:
            signal = info["signal"]
            badge = {
                "STRONG_BUY": "🟢 강력매수",
                "BUY": "🔵 매수",
                "HOLD": "🟡 보유",
                "REDUCE": "🟠 비중축소",
                "SELL": "🔴 매도",
            }.get(signal, "⚪ 중립")
            cp = info.get("current_price")
            vp_pos = info.get("vp_position", "")
            vp_poc_val = info.get("vp_poc")
            if vp_pos == "above" and vp_poc_val:
                vp_label = f"돌파↑ 지지{vp_poc_val:,}"
            elif vp_pos == "below" and vp_poc_val:
                vp_label = f"이탈↓ 저항{vp_poc_val:,}"
            elif vp_pos == "within" and vp_poc_val:
                if cp and cp >= vp_poc_val:
                    vp_label = f"상단→ 중심{vp_poc_val:,}"
                else:
                    vp_label = f"하단→ 중심{vp_poc_val:,}"
            else:
                vp_label = "-"

            # 목표가 & 괴리율
            tp = info.get("target_price")
            tp_date = info.get("target_date", "")
            if tp and cp:
                gap_pct = ((tp - cp) / cp) * 100
                tp_parts = [f"{tp:,}"]
                if tp_date and len(tp_date) >= 10:
                    # 2026-04-21 → (26.04.21)
                    short_date = f"({tp_date[2:4]}.{tp_date[5:7]}.{tp_date[8:10]})"
                    tp_parts.append(short_date)
                target_str = " ".join(tp_parts)
                gap_str = f"{gap_pct:+.1f}%"
            else:
                target_str = "-"
                gap_str = "-"

            rows.append({
                "종목명": info["name"],
                "코드": ticker,
                "목표가": target_str,
                "현재가": f"{cp:,}",
                "등락률": f"{info['change_pct']:+.2f}%",
                "RSI": f"{info['rsi']:.1f}" if info.get("rsi") else "-",
                "매물대": vp_label,
                "괴리율": gap_str,
                "시그널": badge,
                "사유": info.get("signal_reason", ""),
            })

    df_display = pd.DataFrame(rows)
    st.dataframe(
        df_display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "목표가": st.column_config.TextColumn("목표가", help="증권사 컨센서스 평균 목표가 (업데이트 날짜)"),
            "RSI": st.column_config.TextColumn("RSI", help="RSI(14): 30이하=과매도(매수기회), 70이상=과매수(매도고려)"),
            "매물대": st.column_config.TextColumn("매물대", help="돌파↑: 현재가가 거래량 밀집 구간 위 (지지선 확보)\n이탈↓: 거래량 밀집 구간 아래 (지지선 붕괴)\n상단/하단→: 구간 내 위치\n숫자: POC(최대 거래량 가격대)"),
            "괴리율": st.column_config.TextColumn("괴리율", help="+값: 현재가가 목표가보다 낮음 (상승 여력)\n-값: 현재가가 목표가 초과 (고평가 가능성)"),
            "시그널": st.column_config.TextColumn("시그널", help="🟢강력매수 🔵매수 🟡보유 🟠비중축소 🔴매도\nRSI+EMA+VWAP+매물대 종합 분석"),
        },
    )

    # 빠른 삭제 — 클릭하면 삭제 대기열에 추가, 새로고침 시 실제 반영
    stock_names = [(t, i["name"]) for t, i in stocks.items() if is_valid_ticker(t)]
    pending = st.session_state.get("pending_deletes", set())
    if stock_names:
        cols = st.columns(min(len(stock_names), 6))
        for idx, (tkr, nm) in enumerate(stock_names):
            col_idx = idx % min(len(stock_names), 6)
            with cols[col_idx]:
                if tkr in pending:
                    st.button(f"↩ ~~{nm}~~", key=f"undel_{sector}_{tkr}",
                              on_click=lambda t=tkr: st.session_state.pending_deletes.discard(t))
                else:
                    st.button(f"🗑 {nm}", key=f"del_{sector}_{tkr}",
                              on_click=lambda t=tkr: st.session_state.pending_deletes.add(t))
        if pending:
            st.info(f"🗑️ {len(pending)}개 종목 삭제 대기 중 — 상단 **새로고침** 버튼을 누르면 반영됩니다.")

    # 종목별 최신뉴스 (펼치기)
    news_items = []
    for ticker, info in stocks.items():
        if info.get("news"):
            for n in info["news"]:
                news_items.append((info["name"], n))

    if news_items:
        with st.expander(f"📰 {sector_display} 최신뉴스"):
            for stock_name, n in news_items:
                date_tag = f" ({n['date']})" if n.get("date") else ""
                source_tag = f" — {n['source']}" if n.get("source") else ""
                if n.get("url"):
                    st.markdown(f"**{stock_name}** | [{n['title']}]({n['url']}){date_tag}{source_tag}")
                else:
                    st.markdown(f"**{stock_name}** | {n['title']}{date_tag}{source_tag}")

# -----------------------------------------------------------------
# 하단 안내
# -----------------------------------------------------------------
st.markdown("---")
with st.expander("ℹ️ TODO 종목 (코드 확인 필요)"):
    if todo_list:
        st.info(f"아래 {len(todo_list)}개 종목은 `config.py`에서 종목코드를 직접 입력해주세요.")
        for sector, ticker, name in todo_list:
            st.markdown(f"- **{name}** (`{sector}`) — 현재 `{ticker}`")
        st.markdown(
            "👉 종목코드 확인: [KRX 정보데이터시스템](https://data.krx.co.kr/) | "
            "또는 네이버/다음 증권에서 종목명 검색"
        )
    else:
        st.success("TODO 종목 없음 ✅")

with st.expander("ℹ️ 시그널 기준 설명"):
    st.markdown(f"""
    **종합 스코어 시스템** (5가지 지표 가중 합산)

    | 지표 | 매수 (+) | 매도 (-) | 가중치 |
    |------|---------|---------|--------|
    | RSI(14) | ≤ {config.RSI_OVERSOLD} 과매도 | ≥ {config.RSI_OVERBOUGHT} 과매수 | 2 |
    | SMA 5/20 크로스 | 골든크로스 / 정배열 | 데드크로스 / 역배열 | 2 |
    | EMA(9) | 현재가 > EMA9 & 상승세 | 현재가 < EMA9 & 하락세 | 2 |
    | VWAP(20일) | 현재가 > VWAP (매수세) | 현재가 < VWAP (매도세) | 2 |
    | 매물대분석(10단계) | 매물대 돌파 | 매물대 이탈 | 2 |

    - 🟢 **강력매수**: 점수 ≥ +4 (다수 지표 동시 매수 신호)
    - 🔵 **매수**: +2 ≤ 점수 < +4
    - 🟡 **보유**: -1 ≤ 점수 < +2 (관망/현 포지션 유지)
    - 🟠 **비중축소**: -3 ≤ 점수 < -1 (일부 매도 고려)
    - 🔴 **매도**: 점수 < -3

    **리스크 보정 (보수적 접근):**
    - RSI 80 이상 → 심각한 과열 (-3점)
    - VWAP 대비 +20% 이상 → 급락 리스크 (-2점)
    - VWAP 대비 +10% 이상 → 눌림목 대기 권장 (-1점)
    - 20일선 대비 +15% 이상 → 평균 회귀 압력 (-1점)

    **VWAP**: 거래량 가중 평균가. 현재가 > VWAP이면 매수세 우위.
    **EMA(9)**: 지수이동평균 9일 — 토스증권 기본 설정과 동일.

    ---

    **📊 매물대(Volume Profile) 용어 설명**

    매물대란 특정 가격대에서 얼마나 많은 거래가 이루어졌는지를 분석한 것입니다.
    거래가 많았던 가격대는 **지지선**(아래서 받쳐줌) 또는 **저항선**(위에서 막아줌) 역할을 합니다.

    | 용어 | 의미 | 해석 |
    |------|------|------|
    | **POC** | Point of Control. 최대 거래량이 발생한 가격 | 가장 강한 지지/저항 가격. 이 가격 근처에서 매매가 가장 활발했음 |
    | **상단** | 매물대 구간의 윗부분 | 현재가가 상단 근처 → 저항 구간에 진입, 돌파하면 상승 여력 |
    | **하단** | 매물대 구간의 아랫부분 | 현재가가 하단 근처 → 지지 구간, 이탈하면 하락 위험 |
    | **중심** | 매물대 구간의 가운데 (≈ POC) | 가장 거래가 많았던 핵심 가격대 |
    | **돌파** | 현재가가 매물대 상단을 **위로** 뚫음 | ✅ 긍정적. 저항을 뚫고 올라감 → 지지선 확보, 추가 상승 기대 |
    | **이탈** | 현재가가 매물대 하단을 **아래로** 뚫음 | ⚠️ 부정적. 지지선 붕괴 → 추가 하락 가능성 |

    **읽는 법 예시:**
    - `돌파→ 중심106,907` → 매물대를 돌파함, POC(중심가)는 106,907원. 이 가격이 이제 지지선이 됨
    - `상단→ 중심78,512` → 매물대 상단 근처에 있음, POC는 78,512원. 돌파 직전이거나 저항 구간
    - `하단→ 중심41,625` → 매물대 하단 근처에 있음, POC는 41,625원. 이탈 주의
    - `이탈→ 중심128,970` → 매물대 아래로 빠짐, POC 128,970원이 이제 저항선. 반등 시 이 가격에서 막힐 수 있음

    **한 줄 요약:** 돌파 = 좋은 신호 (지지 확보), 이탈 = 위험 신호 (지지 붕괴), POC = 핵심 가격
    """)
