# -*- coding: utf-8 -*-
"""
pykrx를 사용해 종목별 OHLCV 데이터를 가져온다.

[주의사항]
- pykrx는 KRX/네이버 스크래핑 방식이므로 실시간이 아닙니다 (15~20분 지연 가능).
- 한꺼번에 많이 호출하면 KRX 서버가 차단할 수 있어 호출 간 0.5~1초 지연을 넣었습니다.
- 일봉 데이터는 하루 1번만 가져오면 되므로 로컬 캐시(parquet)로 저장합니다.
"""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from pykrx import stock
from us_stocks import is_us_ticker

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def is_valid_ticker(ticker: str) -> bool:
    """종목코드가 유효한지 확인 (한국: 6자리 숫자, 미국: 알파벳 1~5자)."""
    return (ticker.isdigit() and len(ticker) == 6) or is_us_ticker(ticker)


def _yf_to_kr_columns(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance DataFrame 컬럼을 한국어로 변환."""
    col_map = {}
    for c in df.columns:
        cl = c.lower()
        if cl == "open":
            col_map[c] = "시가"
        elif cl == "high":
            col_map[c] = "고가"
        elif cl == "low":
            col_map[c] = "저가"
        elif cl in ("close", "adj close", "adj_close"):
            col_map[c] = "종가"
        elif cl == "volume":
            col_map[c] = "거래량"
    df = df.rename(columns=col_map)
    for needed in ["시가", "고가", "저가", "종가", "거래량"]:
        if needed not in df.columns:
            return pd.DataFrame()
    return df[["시가", "고가", "저가", "종가", "거래량"]]


def get_ohlcv_cached(ticker: str, lookback_days: int = 180) -> pd.DataFrame | None:
    """
    종목의 일봉 OHLCV 데이터 가져오기 (캐시 적용).
    한국/미국 주식 모두 지원.
    """
    if not is_valid_ticker(ticker):
        return None

    today_str = datetime.now().strftime("%Y%m%d")
    cache_file = CACHE_DIR / f"{ticker}_{today_str}.parquet"

    # 캐시 확인 — 장중이면 15분 경과 시 재갱신
    if cache_file.exists():
        try:
            cache_age_sec = time.time() - cache_file.stat().st_mtime
            if is_market_open() and cache_age_sec > 900:
                pass
            else:
                return pd.read_parquet(cache_file)
        except Exception:
            pass

    if is_us_ticker(ticker):
        return _get_us_ohlcv(ticker, lookback_days, cache_file)

    # pykrx 호출 (한국 주식)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=lookback_days)

    try:
        df = stock.get_market_ohlcv(
            start_date.strftime("%Y%m%d"),
            end_date.strftime("%Y%m%d"),
            ticker,
        )
        if df is None or df.empty:
            return None
        df.to_parquet(cache_file)
        time.sleep(0.5)
        return df
    except Exception as e:
        print(f"[ERROR] {ticker} 데이터 가져오기 실패: {e}")
        return None


def _get_us_ohlcv(ticker: str, lookback_days: int, cache_file: Path) -> pd.DataFrame | None:
    """yfinance로 미국 주식 일봉 OHLCV 가져오기."""
    try:
        import yfinance as yf
        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback_days)
        df = yf.download(ticker, start=start_date, end=end_date, progress=False)
        if df is None or df.empty:
            return None
        # MultiIndex 컬럼 처리 (yfinance 0.2.30+)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = _yf_to_kr_columns(df)
        if df.empty:
            return None
        df.to_parquet(cache_file)
        return df
    except Exception as e:
        print(f"[ERROR] US {ticker} 일봉 실패: {e}")
        return None


def clear_old_cache(keep_days: int = 3):
    """오래된 캐시 파일 정리 (용량 관리)."""
    cutoff = datetime.now() - timedelta(days=keep_days)
    for f in CACHE_DIR.glob("*.parquet"):
        try:
            # 파일명 패턴: {ticker}_{YYYYMMDD}.parquet
            date_str = f.stem.split("_")[1]
            file_date = datetime.strptime(date_str, "%Y%m%d")
            if file_date < cutoff:
                f.unlink()
        except Exception:
            continue


_STOCK_LIST_CACHE = {}  # {ticker: name} 캐시


def _load_stock_list() -> dict:
    """KOSPI+KOSDAQ 전체 종목 리스트 캐시 로드 (1일 1회)."""
    global _STOCK_LIST_CACHE
    cache_file = CACHE_DIR / "stock_list.json"

    # 메모리 캐시
    if _STOCK_LIST_CACHE:
        return _STOCK_LIST_CACHE

    # 파일 캐시 (오늘 날짜)
    today_str = datetime.now().strftime("%Y%m%d")
    if cache_file.exists():
        try:
            import json
            cache_age = time.time() - cache_file.stat().st_mtime
            if cache_age < 86400:  # 24시간
                with open(cache_file, "r", encoding="utf-8") as f:
                    _STOCK_LIST_CACHE = json.load(f)
                return _STOCK_LIST_CACHE
        except Exception:
            pass

    # pykrx에서 전체 종목 로드
    result = {}
    try:
        for market in ["KOSPI", "KOSDAQ"]:
            tickers = stock.get_market_ticker_list(today_str, market=market)
            for ticker in tickers:
                name = stock.get_market_ticker_name(ticker)
                if name:
                    result[ticker] = name
            time.sleep(0.3)
    except Exception as e:
        print(f"[ERROR] 종목 리스트 로드 실패: {e}")

    if result:
        import json
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        _STOCK_LIST_CACHE = result

    return result


def search_stock_by_name(query: str) -> list[tuple[str, str]]:
    """
    종목명으로 검색 (한국 + 미국 주식).
    (종목코드, 종목명) 리스트 반환.
    """
    from us_stocks import search_us_stock

    query = query.strip()
    if not query:
        return []

    results = []

    # 한국 주식 검색
    stock_list = _load_stock_list()
    q_lower = query.lower()
    for ticker, name in stock_list.items():
        if q_lower == name.lower():
            results.insert(0, (ticker, name))
        elif q_lower in name.lower():
            results.append((ticker, name))
    if query.isdigit():
        for ticker, name in stock_list.items():
            if query in ticker and (ticker, name) not in results:
                results.append((ticker, name))

    # 미국 주식 검색
    us_results = search_us_stock(query)
    for ticker, name in us_results:
        display = f"🇺🇸 {name}"
        if (ticker, display) not in results:
            results.append((ticker, display))

    return results[:20]


def get_stock_name_by_ticker(ticker: str) -> str | None:
    """종목코드로 종목명 조회."""
    info = get_stock_info(ticker)
    return info["name"] if info else None


# 섹터 자동 분류용 키워드 매핑
_SECTOR_KEYWORDS = {
    "반도체": ["반도체", "메모리", "파운드리", "웨이퍼", "칩", "DRAM", "NAND", "HBM", "GPU", "AP설계",
              "반도체장비", "CMP", "식각", "증착", "노광", "EUV", "테스트", "후공정"],
    "반도체_PCB": ["PCB", "기판", "인쇄회로", "패키징", "FC-BGA", "FCCSP", "서브스트레이트"],
    "유리기판": ["유리기판", "글라스", "TGV"],
    "2차전지_ESS": ["2차전지", "배터리", "리튬", "양극재", "음극재", "전해질", "분리막", "ESS", "에너지저장",
                   "전지", "셀", "전극", "집전체", "바인더", "전해액", "습식", "건식", "파우치"],
    "로봇": ["로봇", "로보틱스", "자동화", "모션제어", "서보", "액추에이터", "협동로봇", "감속기", "매니퓰레이터"],
    "우주항공": ["우주", "항공", "위성", "발사체", "방위산업", "방산", "드론", "UAM", "국방"],
    "원전": ["원전", "원자력", "핵", "발전소", "터빈", "SMR", "원자로"],
    "AI_데이터센터_전력": ["AI", "인공지능", "데이터센터", "전력", "변압기", "전기", "송전", "배전", "수배전",
                        "차단기", "개폐기", "UPS", "전력반도체", "GPU서버"],
    "재생에너지": ["태양광", "풍력", "수소", "연료전지", "신재생", "에너지솔루션", "그린", "태양전지", "모듈"],
    "바이오_헬스케어": ["바이오", "제약", "의약", "헬스케어", "진단", "의료기기", "신약", "항체", "CMO", "CDMO",
                     "임상", "백신", "세포", "유전자", "치료제"],
    "자동차": ["자동차", "모빌리티", "전기차", "EV", "자율주행", "ADAS", "차량", "완성차"],
    "IT_소프트웨어": ["소프트웨어", "플랫폼", "클라우드", "SaaS", "게임", "인터넷", "포털", "IT서비스", "SI"],
    "금융": ["은행", "증권", "보험", "금융", "카드", "캐피탈", "자산운용"],
    "화학_소재": ["화학", "소재", "석유화학", "정밀화학", "특수가스", "전자재료"],
    "건설_인프라": ["건설", "시멘트", "건축", "인프라", "플랜트", "엔지니어링"],
    "미디어_엔터": ["엔터", "미디어", "콘텐츠", "방송", "음악", "드라마", "영화", "기획사"],
    "유통_소비재": ["유통", "백화점", "마트", "식품", "음료", "화장품", "뷰티", "소비재"],
    "통신": ["통신", "5G", "네트워크", "텔레콤", "통신장비"],
    "ETF_기타": ["ETF", "KODEX", "TIGER", "ARIRANG", "SOL", "ACE", "HANARO"],
}

# 잘 알려진 기업의 섹터 직접 매핑 (API에서 업종을 못 가져올 때 보완)
_KNOWN_STOCKS = {
    "더블유씨피": "2차전지_ESS",
    "에코프로": "2차전지_ESS",
    "포스코퓨처엠": "2차전지_ESS",
    "엘앤에프": "2차전지_ESS",
    "삼성전자": "반도체",
    "SK하이닉스": "반도체",
    "한미반도체": "반도체",
    "현대차": "자동차",
    "기아": "자동차",
    "셀트리온": "바이오_헬스케어",
    "삼성바이오로직스": "바이오_헬스케어",
    "카카오": "IT_소프트웨어",
    "네이버": "IT_소프트웨어",
    "NAVER": "IT_소프트웨어",
    "하이브": "미디어_엔터",
    "JYP": "미디어_엔터",
    "SM": "미디어_엔터",
}


def classify_sector(stock_name: str, business_desc: str = "") -> str:
    """종목명과 사업내용을 기반으로 섹터를 자동 분류."""

    # 1차: 잘 알려진 기업 직접 매핑
    for known_name, known_sector in _KNOWN_STOCKS.items():
        if known_name in stock_name:
            return known_sector

    text = f"{stock_name} {business_desc}".upper()

    # 2차: ETF는 이름만으로 바로 판별
    for etf_keyword in ["ETF", "KODEX", "TIGER", "ARIRANG", "SOL ", "ACE ", "HANARO"]:
        if etf_keyword in text:
            return "ETF_기타"

    # 3차: 키워드 매칭 (가장 많이 일치하는 섹터)
    best_sector = "기타"
    best_score = 0

    for sector, keywords in _SECTOR_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.upper() in text)
        if score > best_score:
            best_score = score
            best_sector = sector

    return best_sector


def get_stock_info(ticker: str) -> dict | None:
    """
    네이버증권 API에서 종목의 이름, 업종, 사업내용을 가져온다.
    여러 API 엔드포인트를 시도하여 최대한 상세 정보를 수집.
    Returns: {"name": str, "sector_name": str, "business": str} or None
    """
    import requests

    headers = {"User-Agent": "Mozilla/5.0"}
    name = ""
    sector_name = ""
    business = ""

    # 1차: 네이버 모바일 기본 API
    try:
        url = f"https://m.stock.naver.com/api/stock/{ticker}/basic"
        resp = requests.get(url, timeout=5, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            name = data.get("stockName", "")
            sector_name = data.get("industryCodeName", "")
    except Exception:
        pass

    # 2차: 기업 개요 API (사업 설명 포함)
    try:
        url = f"https://m.stock.naver.com/api/stock/{ticker}/integration"
        resp = requests.get(url, timeout=5, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if not name:
                name = data.get("stockName", "")
            if not sector_name or sector_name in ("KOSPI", "KOSDAQ"):
                sector_name = data.get("industryCodeName", sector_name)
            business = data.get("corporationSummary", "") or data.get("description", "")
    except Exception:
        pass

    # 3차: 기업 프로필 API
    try:
        url = f"https://m.stock.naver.com/api/stock/{ticker}/company"
        resp = requests.get(url, timeout=5, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if not sector_name or sector_name in ("KOSPI", "KOSDAQ"):
                sector_name = data.get("industryCodeName", "") or data.get("industryName", sector_name)
            if not business:
                business = data.get("corporationSummary", "") or data.get("businessSummary", "")
    except Exception:
        pass

    if name:
        return {"name": name, "sector_name": sector_name, "business": business}

    # 최후: pykrx fallback
    try:
        pykrx_name = stock.get_market_ticker_name(ticker)
        if pykrx_name:
            return {"name": pykrx_name, "sector_name": "", "business": ""}
    except Exception:
        pass

    return None


def get_stock_news(ticker: str, count: int = 3) -> list[dict]:
    """
    다음 금융 API에서 종목 관련 최신 뉴스 가져오기.
    Returns: [{"title": str, "url": str, "date": str, "source": str}, ...]
    """
    import requests

    results = []

    try:
        url = "https://finance.daum.net/content/news"
        params = {
            "page": 1,
            "perPage": count,
            "category": "stock",
            "searchType": "all",
            "keyword": ticker,
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.daum.net",
        }
        resp = requests.get(url, params=params, timeout=5, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("data", [])[:count]:
                title = item.get("title", "")
                news_id = item.get("newsId", "")
                article_url = f"https://v.daum.net/v/{news_id}" if news_id else ""
                date = item.get("createdAt", "")
                source = item.get("cpKorName", "")
                if title:
                    results.append({
                        "title": title,
                        "url": article_url,
                        "date": date[:10] if date else "",
                        "source": source,
                    })
    except Exception as e:
        print(f"[ERROR] 다음 뉴스 조회 실패: {e}")

    return results


def get_target_price(ticker: str) -> dict | None:
    """
    네이버증권 integration API의 consensusInfo에서 목표가 조회.
    Returns: {"target_price": int, "opinion": str, "report_date": str, "broker_count": int} or None
    """
    import requests

    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        url = f"https://m.stock.naver.com/api/stock/{ticker}/integration"
        resp = requests.get(url, timeout=5, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            consensus = data.get("consensusInfo")
            if consensus:
                target_str = consensus.get("priceTargetMean", "")
                if target_str:
                    # "574,762" → 574762
                    target_price = int(target_str.replace(",", ""))
                    report_date = consensus.get("createDate", "")

                    # 투자의견 점수 → 텍스트 변환
                    recomm = consensus.get("recommMean", "")
                    opinion = ""
                    if recomm:
                        try:
                            score = float(recomm)
                            if score >= 4.0:
                                opinion = "매수"
                            elif score >= 3.0:
                                opinion = "중립"
                            else:
                                opinion = "매도"
                        except ValueError:
                            pass

                    # 증권사 리포트 건수 (totalInfos에서 추출)
                    broker_count = 0
                    for info in data.get("totalInfos", []):
                        if info.get("code") == "consensusCount" or "증권사" in info.get("key", ""):
                            try:
                                broker_count = int(info.get("value", "0").replace(",", ""))
                            except ValueError:
                                pass

                    return {
                        "target_price": target_price,
                        "opinion": opinion,
                        "report_date": report_date,
                        "broker_count": broker_count,
                    }
    except Exception as e:
        print(f"[ERROR] 목표가 조회 실패 ({ticker}): {e}")

    return None


def get_investor_data(ticker: str, days: int = 10) -> dict | None:
    """
    외국인/기관 순매수량 및 거래대금 조회 (pykrx).

    Returns:
        {
            "foreign_net_5d": int,   # 외국인 최근 5일 순매수량
            "inst_net_5d": int,      # 기관 최근 5일 순매수량
            "foreign_net_today": int, # 외국인 당일 순매수량
            "inst_net_today": int,    # 기관 당일 순매수량
            "trading_value": int,    # 최근 거래대금 (백만원)
        }
    """
    if not is_valid_ticker(ticker):
        return None

    # 캐시: 장중 10분, 장외 1시간
    cache_file = CACHE_DIR / f"{ticker}_inv.json"
    if cache_file.exists():
        try:
            cache_age = time.time() - cache_file.stat().st_mtime
            ttl = 600 if is_market_open() else 3600
            if cache_age < ttl:
                import json
                with open(cache_file, "r") as f:
                    return json.load(f)
        except Exception:
            pass

    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days + 10)  # 영업일 확보용 여유

        df = stock.get_market_trading_volume_by_date(
            start_date.strftime("%Y%m%d"),
            end_date.strftime("%Y%m%d"),
            ticker,
        )
        if df is None or df.empty:
            return None

        # 최근 5영업일
        recent_5 = df.tail(5)
        today_row = df.iloc[-1] if len(df) > 0 else None

        # 외국인/기관 컬럼 찾기 (pykrx 버전에 따라 컬럼명 다름)
        foreign_col = None
        inst_col = None
        for col in df.columns:
            if "외국인" in str(col):
                foreign_col = col
            if "기관" in str(col) and "합계" in str(col):
                inst_col = col
            elif "기관" in str(col) and inst_col is None:
                inst_col = col

        foreign_net_5d = int(recent_5[foreign_col].sum()) if foreign_col else 0
        inst_net_5d = int(recent_5[inst_col].sum()) if inst_col else 0
        foreign_net_today = int(today_row[foreign_col]) if foreign_col and today_row is not None else 0
        inst_net_today = int(today_row[inst_col]) if inst_col and today_row is not None else 0

        # 거래대금 (pykrx OHLCV에서 가져오기)
        trading_value = 0
        try:
            ohlcv = stock.get_market_ohlcv(
                (end_date - timedelta(days=3)).strftime("%Y%m%d"),
                end_date.strftime("%Y%m%d"),
                ticker,
            )
            if ohlcv is not None and not ohlcv.empty and "거래대금" in ohlcv.columns:
                trading_value = int(ohlcv["거래대금"].iloc[-1] / 1_000_000)  # 백만원 단위
        except Exception:
            pass

        result = {
            "foreign_net_5d": foreign_net_5d,
            "inst_net_5d": inst_net_5d,
            "foreign_net_today": foreign_net_today,
            "inst_net_today": inst_net_today,
            "trading_value": trading_value,
        }

        # 캐시 저장
        import json
        with open(cache_file, "w") as f:
            json.dump(result, f)

        time.sleep(0.3)
        return result

    except Exception as e:
        print(f"[ERROR] 수급 데이터 실패 ({ticker}): {e}")
        return None


def get_intraday_ohlcv(ticker: str, interval: int = 15) -> pd.DataFrame | None:
    """
    네이버 금융 차트 API에서 분봉(15분/60분) OHLCV 데이터를 가져온다.

    Args:
        ticker: 종목코드 (6자리)
        interval: 분봉 간격 (15 또는 60)

    Returns:
        DataFrame (시가, 고가, 저가, 종가, 거래량) 또는 None
    """
    import requests

    if not is_valid_ticker(ticker):
        return None

    # 캐시: 장중 3분, 장외 1시간
    cache_file = CACHE_DIR / f"{ticker}_m{interval}.parquet"
    if cache_file.exists():
        try:
            cache_age = time.time() - cache_file.stat().st_mtime
            ttl = 180 if is_market_open() else 3600
            if cache_age < ttl:
                return pd.read_parquet(cache_file)
        except Exception:
            pass

    # 미국 주식: yfinance
    if is_us_ticker(ticker):
        try:
            import yfinance as yf
            interval_str = f"{interval}m"
            period = "7d" if interval == 15 else "30d"
            df = yf.download(ticker, period=period, interval=interval_str, progress=False)
            if df is None or df.empty:
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = _yf_to_kr_columns(df)
            if df.empty:
                return None
            df.to_parquet(cache_file)
            return df
        except Exception as e:
            print(f"[ERROR] US {ticker} 분봉 실패: {e}")
            return None

    # 한국 주식: 네이버 차트 API
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        # 15분봉: 7일치, 60분봉: 14일치 (MA20+2 확보)
        lookback = 7 if interval == 15 else 14
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=lookback)

        url = f"https://api.stock.naver.com/chart/domestic/item/{ticker}/minute{interval}"
        params = {
            "startDateTime": start_dt.strftime("%Y%m%d090000"),
            "endDateTime": end_dt.strftime("%Y%m%d153000"),
        }
        resp = requests.get(url, params=params, timeout=5, headers=headers)
        if resp.status_code != 200:
            return None

        candles = resp.json()
        if not isinstance(candles, list) or not candles:
            return None

        rows = []
        for c in candles:
            try:
                rows.append({
                    "날짜": c["localDateTime"],
                    "시가": int(c["openPrice"]),
                    "고가": int(c["highPrice"]),
                    "저가": int(c["lowPrice"]),
                    "종가": int(c["currentPrice"]),
                    "거래량": int(c["accumulatedTradingVolume"]),
                })
            except (ValueError, TypeError, KeyError):
                continue

        if not rows:
            return None

        df = pd.DataFrame(rows)
        df["날짜"] = pd.to_datetime(df["날짜"], format="%Y%m%d%H%M%S", errors="coerce")
        df = df.set_index("날짜").sort_index()

        # 캐시 저장
        df.to_parquet(cache_file)
        time.sleep(0.2)
        return df

    except Exception as e:
        print(f"[ERROR] 분봉 데이터 실패 ({ticker}, {interval}분): {e}")
        return None


def get_realtime_price(ticker: str) -> dict | None:
    """
    현재가/등락률을 실시간으로 가져온다.
    한국: 네이버 금융, 미국: yfinance
    """
    if is_us_ticker(ticker):
        return _get_us_realtime_price(ticker)

    import requests
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        url = f"https://m.stock.naver.com/api/stock/{ticker}/basic"
        resp = requests.get(url, timeout=3, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            price = int(data.get("closePrice", "0").replace(",", ""))
            change_pct = float(data.get("fluctuationsRatio", "0").replace(",", ""))
            high = int(data.get("highPrice", "0").replace(",", ""))
            low = int(data.get("lowPrice", "0").replace(",", ""))
            volume = int(data.get("accumulatedTradingVolume", "0").replace(",", ""))
            if price > 0:
                return {
                    "price": price,
                    "change_pct": change_pct,
                    "high": high,
                    "low": low,
                    "volume": volume,
                }
    except Exception as e:
        print(f"[ERROR] 네이버 실시간 시세 실패 ({ticker}): {e}")
    return None


def _get_us_realtime_price(ticker: str) -> dict | None:
    """yfinance로 미국 주식 현재가 조회."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.fast_info
        price = round(float(info.last_price), 2)
        prev = round(float(info.previous_close), 2)
        change_pct = round((price - prev) / prev * 100, 2) if prev else 0
        return {
            "price": price,
            "change_pct": change_pct,
            "high": round(float(info.day_high), 2) if info.day_high else price,
            "low": round(float(info.day_low), 2) if info.day_low else price,
            "volume": int(info.last_volume) if info.last_volume else 0,
        }
    except Exception as e:
        print(f"[ERROR] US 실시간 시세 실패 ({ticker}): {e}")
    return None


def is_market_open() -> bool:
    """한국 주식 장 시간 확인 (정규장 + 시간외 포함).
    평일 09:00~18:00 (정규장 09:00~15:30, 시간외 15:40~18:00).
    주의: 공휴일은 별도 체크 안 함.
    """
    now = datetime.now()
    if now.weekday() >= 5:  # 토/일
        return False
    hour_minute = now.hour * 100 + now.minute
    return 900 <= hour_minute <= 1800
