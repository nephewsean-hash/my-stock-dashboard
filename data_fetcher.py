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

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def is_valid_ticker(ticker: str) -> bool:
    """종목코드가 유효한 6자리 숫자인지 확인 (TODO_VERIFY 스킵용)."""
    return ticker.isdigit() and len(ticker) == 6


def get_ohlcv_cached(ticker: str, lookback_days: int = 180) -> pd.DataFrame | None:
    """
    종목의 일봉 OHLCV 데이터 가져오기 (캐시 적용).

    Args:
        ticker: 종목코드 (6자리)
        lookback_days: 과거 며칠치 데이터 (기본 180일)

    Returns:
        DataFrame (날짜, 시가, 고가, 저가, 종가, 거래량) 또는 None
    """
    if not is_valid_ticker(ticker):
        return None

    today_str = datetime.now().strftime("%Y%m%d")
    cache_file = CACHE_DIR / f"{ticker}_{today_str}.parquet"

    # 캐시 확인 — 장중이면 15분 경과 시 재갱신
    if cache_file.exists():
        try:
            cache_age_sec = time.time() - cache_file.stat().st_mtime
            if is_market_open() and cache_age_sec > 900:  # 15분
                pass  # 캐시 만료 → 재호출
            else:
                return pd.read_parquet(cache_file)
        except Exception:
            pass  # 캐시 깨졌으면 다시 가져옴

    # pykrx 호출
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

        # 캐시 저장
        df.to_parquet(cache_file)

        # KRX 부하 방지 (공식 가이드: 1초 지연 권고)
        time.sleep(0.5)
        return df
    except Exception as e:
        print(f"[ERROR] {ticker} 데이터 가져오기 실패: {e}")
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


def search_stock_by_name(query: str) -> list[tuple[str, str]]:
    """
    종목명으로 검색 (한글/영문 모두 지원).
    1차: 네이버 자동완성 API (한글 검색에 강함)
    2차: 네이버 모바일 검색 API (한글)
    3차: 네이버 기본 API로 직접 조회 시도 (영문명 대응)
    (종목코드, 종목명) 리스트 반환.
    """
    import requests

    headers = {"User-Agent": "Mozilla/5.0"}
    results = []

    # 1차: 네이버증권 자동완성 API
    try:
        url = "https://ac.finance.naver.com/ac"
        params = {
            "q": query, "q_enc": "utf-8", "t_koreng": "1",
            "st": "111", "r_lt": "111", "r_format": "json",
            "r_enc": "utf-8", "r_unicode": "0", "r_num": "20",
        }
        resp = requests.get(url, params=params, timeout=5, headers=headers)
        data = resp.json()
        items = data.get("items", [])
        if items:
            for item_list in items:
                for entry in item_list:
                    if len(entry) >= 2:
                        name = entry[0][0] if isinstance(entry[0], list) else entry[0]
                        ticker = entry[1][0] if isinstance(entry[1], list) else entry[1]
                        if isinstance(ticker, str) and ticker.isdigit() and len(ticker) == 6:
                            results.append((ticker, name))
        if results:
            return results
    except Exception:
        pass

    # 2차: 네이버 모바일 검색 API
    try:
        url = f"https://m.stock.naver.com/api/json/search/searchListJson.nhn?keyword={query}"
        resp = requests.get(url, timeout=5, headers=headers)
        data = resp.json()
        items = data.get("result", {}).get("d", [])
        for item in items:
            ticker = item.get("cd", "")
            name = item.get("nm", "")
            if ticker and name and len(ticker) == 6:
                results.append((ticker, name))
        if results:
            return results
    except Exception:
        pass

    # 3차: pykrx 전체 종목 리스트 (영문명 포함 검색)
    try:
        today_str = datetime.now().strftime("%Y%m%d")
        for market in ["KOSPI", "KOSDAQ"]:
            tickers = stock.get_market_ticker_list(today_str, market=market)
            for ticker in tickers:
                name = stock.get_market_ticker_name(ticker)
                if query.lower() in name.lower():
                    results.append((ticker, name))
        if results:
            return results
    except Exception:
        pass

    # 4차: 영문명 직접 매칭 시도 (네이버 basic API로 하나씩 확인은 비효율적이므로
    # 흔한 영문 종목명을 직접 매핑)
    # 사용자가 영문으로 검색 시 → 종목코드 직접 입력을 안내
    return results


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


def get_intraday_ohlcv(ticker: str, interval: int = 15) -> pd.DataFrame | None:
    """
    네이버 금융에서 분봉(15분/60분) OHLCV 데이터를 가져온다.

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

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        # 네이버 차트 API — 분봉 데이터
        url = f"https://m.stock.naver.com/api/stock/{ticker}/chart"
        count = 120 if interval == 15 else 80
        params = {
            "periodType": "minute",
            "period": interval,
            "count": count,
        }
        resp = requests.get(url, params=params, timeout=5, headers=headers)
        if resp.status_code != 200:
            return None

        data = resp.json()
        candles = data if isinstance(data, list) else data.get("priceInfos", data.get("chartDatas", []))
        if not candles:
            return None

        rows = []
        for c in candles:
            try:
                dt = c.get("localDate", "") + c.get("localTime", "")
                rows.append({
                    "날짜": dt,
                    "시가": int(str(c.get("openPrice", 0)).replace(",", "")),
                    "고가": int(str(c.get("highPrice", 0)).replace(",", "")),
                    "저가": int(str(c.get("lowPrice", 0)).replace(",", "")),
                    "종가": int(str(c.get("closePrice", 0)).replace(",", "")),
                    "거래량": int(str(c.get("accumulatedTradingVolume", c.get("tradingVolume", 0))).replace(",", "")),
                })
            except (ValueError, TypeError):
                continue

        if not rows:
            return None

        df = pd.DataFrame(rows)
        if "날짜" in df.columns and df["날짜"].str.len().max() >= 8:
            df["날짜"] = pd.to_datetime(df["날짜"], format="mixed", errors="coerce")
            df = df.set_index("날짜")
        df = df.sort_index()

        # 캐시 저장
        df.to_parquet(cache_file)
        time.sleep(0.2)
        return df

    except Exception as e:
        print(f"[ERROR] 분봉 데이터 실패 ({ticker}, {interval}분): {e}")
        return None


def get_realtime_price(ticker: str) -> dict | None:
    """
    네이버 금융에서 현재가/등락률을 실시간으로 가져온다.
    Returns: {"price": int, "change_pct": float, "high": int, "low": int, "volume": int} or None
    """
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


def is_market_open() -> bool:
    """한국 주식 장 시간 확인 (주말/공휴일 제외는 간이 판정).
    평일 09:00~15:30 만 True.
    주의: 공휴일은 별도 체크 안 함 (과도한 호출 최소화용 간이 판정).
    """
    now = datetime.now()
    if now.weekday() >= 5:  # 토/일
        return False
    hour_minute = now.hour * 100 + now.minute
    return 900 <= hour_minute <= 1530
