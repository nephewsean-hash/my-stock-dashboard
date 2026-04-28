# -*- coding: utf-8 -*-
"""미국 주식 한글명 매핑 및 검색."""

# 한글명 → (티커, 영문명)
US_STOCK_MAP = {
    # 빅테크
    "엔비디아": ("NVDA", "NVIDIA"),
    "테슬라": ("TSLA", "Tesla"),
    "애플": ("AAPL", "Apple"),
    "마이크로소프트": ("MSFT", "Microsoft"),
    "아마존": ("AMZN", "Amazon"),
    "구글": ("GOOGL", "Alphabet"),
    "알파벳": ("GOOGL", "Alphabet"),
    "메타": ("META", "Meta Platforms"),
    "넷플릭스": ("NFLX", "Netflix"),
    # 반도체
    "AMD": ("AMD", "AMD"),
    "인텔": ("INTC", "Intel"),
    "ASML": ("ASML", "ASML"),
    "브로드컴": ("AVGO", "Broadcom"),
    "마이크론": ("MU", "Micron"),
    "퀄컴": ("QCOM", "Qualcomm"),
    "ARM": ("ARM", "ARM Holdings"),
    "TSMC": ("TSM", "TSMC"),
    "대만반도체": ("TSM", "TSMC"),
    # AI/소프트웨어
    "팔란티어": ("PLTR", "Palantir"),
    "스노우플레이크": ("SNOW", "Snowflake"),
    "크라우드스트라이크": ("CRWD", "CrowdStrike"),
    "서비스나우": ("NOW", "ServiceNow"),
    "세일즈포스": ("CRM", "Salesforce"),
    "어도비": ("ADBE", "Adobe"),
    "오라클": ("ORCL", "Oracle"),
    # 전기차/에너지
    "리비안": ("RIVN", "Rivian"),
    "루시드": ("LCID", "Lucid"),
    "니오": ("NIO", "NIO"),
    "엔페이즈": ("ENPH", "Enphase"),
    # 금융/기타
    "JP모건": ("JPM", "JPMorgan"),
    "버크셔": ("BRK-B", "Berkshire Hathaway"),
    "비자": ("V", "Visa"),
    "코스트코": ("COST", "Costco"),
    "월마트": ("WMT", "Walmart"),
    # ETF
    "SPY": ("SPY", "S&P 500 ETF"),
    "QQQ": ("QQQ", "Nasdaq 100 ETF"),
    "SOXL": ("SOXL", "반도체 3배 레버리지"),
    "TQQQ": ("TQQQ", "나스닥 3배 레버리지"),
}

# 티커 → 한글명 역방향 매핑
_TICKER_TO_KR = {}
for kr_name, (ticker, eng_name) in US_STOCK_MAP.items():
    if ticker not in _TICKER_TO_KR:
        _TICKER_TO_KR[ticker] = kr_name


def is_us_ticker(ticker: str) -> bool:
    """미국 주식 티커인지 판별 (알파벳 1~5자 또는 BRK-B 형식)."""
    if not ticker:
        return False
    clean = ticker.replace("-", "")
    return clean.isalpha() and 1 <= len(clean) <= 5


def search_us_stock(query: str) -> list[tuple[str, str]]:
    """미국 주식 검색 (한글명/영문명/티커)."""
    query = query.strip().upper()
    q_lower = query.lower()
    results = []

    # 티커 직접 매칭
    for kr_name, (ticker, eng_name) in US_STOCK_MAP.items():
        if ticker.upper() == query:
            results.insert(0, (ticker, f"{kr_name} ({eng_name})"))

    # 한글명/영문명 부분 매칭
    for kr_name, (ticker, eng_name) in US_STOCK_MAP.items():
        entry = (ticker, f"{kr_name} ({eng_name})")
        if entry in results:
            continue
        if q_lower in kr_name.lower() or q_lower in eng_name.lower():
            results.append(entry)

    return results[:20]


def get_us_korean_name(ticker: str) -> str:
    """미국 티커 → 한글명 반환."""
    return _TICKER_TO_KR.get(ticker.upper(), ticker)
