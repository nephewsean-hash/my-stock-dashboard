"""
관심종목 대시보드 설정.
- WATCHLIST는 watchlist.json에서 동적으로 로드합니다.
- 종목 추가/삭제는 대시보드 UI에서 가능합니다.
"""

import json
import shutil
from pathlib import Path

# 기본 watchlist (git에 포함, 초기 템플릿)
_WATCHLIST_DEFAULT = Path(__file__).parent / "watchlist.json"
# 사용자 수정본 (git에 미포함, 삭제/추가가 유지됨)
_WATCHLIST_FILE = Path(__file__).parent / "watchlist_user.json"


def load_watchlist() -> dict:
    """관심종목 로드. 사용자 수정본 우선, 없으면 기본 파일에서 복사."""
    if _WATCHLIST_FILE.exists():
        try:
            with open(_WATCHLIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # 사용자 파일 없으면 기본 파일에서 복사
    if _WATCHLIST_DEFAULT.exists():
        shutil.copy2(_WATCHLIST_DEFAULT, _WATCHLIST_FILE)
        with open(_WATCHLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_watchlist(watchlist: dict):
    """관심종목을 사용자 파일에 저장."""
    with open(_WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(watchlist, f, ensure_ascii=False, indent=4)


def add_stock(ticker: str, name: str, sector: str):
    """종목을 특정 섹터에 추가."""
    wl = load_watchlist()
    if sector not in wl:
        wl[sector] = {}
    wl[sector][ticker] = name
    save_watchlist(wl)


def remove_stock(ticker: str):
    """종목코드로 종목 삭제 (어느 섹터에 있든)."""
    wl = load_watchlist()
    for sector in wl:
        if ticker in wl[sector]:
            del wl[sector][ticker]
            # 섹터가 비었으면 섹터도 삭제
            if not wl[sector]:
                del wl[sector]
            break
    save_watchlist(wl)


def move_sector(sector: str, direction: str):
    """섹터 순서를 위(up) 또는 아래(down)로 이동."""
    wl = load_watchlist()
    keys = list(wl.keys())
    if sector not in keys:
        return
    idx = keys.index(sector)
    if direction == "up" and idx > 0:
        keys[idx], keys[idx - 1] = keys[idx - 1], keys[idx]
    elif direction == "down" and idx < len(keys) - 1:
        keys[idx], keys[idx + 1] = keys[idx + 1], keys[idx]
    else:
        return
    new_wl = {k: wl[k] for k in keys}
    save_watchlist(new_wl)


def rename_sector(old_name: str, new_name: str):
    """섹터명 변경."""
    wl = load_watchlist()
    if old_name not in wl or not new_name or old_name == new_name:
        return
    # 순서 유지하며 키 변경
    new_wl = {}
    for k, v in wl.items():
        if k == old_name:
            new_wl[new_name] = v
        else:
            new_wl[k] = v
    save_watchlist(new_wl)


WATCHLIST = load_watchlist()

# =========================================================================
# 시그널 파라미터 (단기 스윙 - RSI(14) + 5/20일 이평 크로스)
# =========================================================================

# RSI 기준
RSI_PERIOD = 14
RSI_OVERSOLD = 30   # 이 아래면 과매도 (매수 관심)
RSI_OVERBOUGHT = 70 # 이 위면 과매수 (매도 관심)

# 이동평균 (단기 스윙 기준)
MA_SHORT = 5   # 단기 이평
MA_LONG = 20   # 중기 이평
MA_TREND = 60  # 추세 확인용

# 데이터 조회 기간 (이평/RSI 계산용 - 충분한 영업일 확보)
LOOKBACK_DAYS = 180  # 약 6개월치 (영업일 기준 약 120일)

# 캐시 시간 (초) - 5분
CACHE_TTL = 300
