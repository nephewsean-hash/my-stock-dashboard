# -*- coding: utf-8 -*-
"""
기술적 지표 계산.

[사용 공식 출처]
- RSI: Welles Wilder (1978), "New Concepts in Technical Trading Systems"
  원전의 Wilder's Smoothing 방식 사용 (EMA 유사, alpha=1/period)
- 이동평균: 단순이동평균(SMA), 지수이동평균(EMA)
- 골든/데드 크로스: 단기 이평선이 중기 이평선을 상향/하향 돌파한 당일
- 매물대분석: 가격대별 거래량 분포 (Volume Profile)
"""

import pandas as pd
import numpy as np


def calculate_rsi(close_prices: pd.Series, period: int = 14) -> pd.Series:
    """RSI (Wilder's Smoothing)."""
    delta = close_prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_ma(close_prices: pd.Series, period: int) -> pd.Series:
    """단순이동평균(SMA) 계산."""
    return close_prices.rolling(window=period, min_periods=period).mean()


def calculate_ema(close_prices: pd.Series, period: int) -> pd.Series:
    """지수이동평균(EMA) 계산."""
    return close_prices.ewm(span=period, adjust=False, min_periods=period).mean()


def calculate_vwap(ohlcv_df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    VWAP (Volume Weighted Average Price) 계산.
    최근 period일 기준 누적 VWAP.

    공식: VWAP = Σ(대표가 × 거래량) / Σ(거래량)
    대표가 = (고가 + 저가 + 종가) / 3
    """
    typical_price = (ohlcv_df["고가"] + ohlcv_df["저가"] + ohlcv_df["종가"]) / 3
    volume = ohlcv_df["거래량"]

    tp_vol = typical_price * volume
    cum_tp_vol = tp_vol.rolling(window=period, min_periods=period).sum()
    cum_vol = volume.rolling(window=period, min_periods=period).sum()

    vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
    return vwap


def calculate_volume_profile(ohlcv_df: pd.DataFrame, bins: int = 10) -> dict:
    """
    매물대분석 (Volume Profile).
    최근 데이터의 가격대별 거래량 분포를 계산.

    Returns:
        {
            "poc_price": 최대 거래량 가격대 중심값 (Point of Control),
            "poc_range": (하한, 상한),
            "value_area_low": 가치 영역 하단 (전체 거래량 70% 범위),
            "value_area_high": 가치 영역 상단,
            "position": "above" / "within" / "below" (현재가 기준 매물대 위치),
        }
    """
    close = ohlcv_df["종가"]
    volume = ohlcv_df["거래량"]
    current_price = close.iloc[-1]

    price_min = close.min()
    price_max = close.max()

    if price_max == price_min or volume.sum() == 0:
        return {"poc_price": None, "position": "unknown"}

    # 가격대를 bins개 구간으로 나누기
    bin_edges = np.linspace(price_min, price_max, bins + 1)
    bin_volumes = np.zeros(bins)

    for i in range(bins):
        mask = (close >= bin_edges[i]) & (close < bin_edges[i + 1])
        if i == bins - 1:  # 마지막 구간은 상한 포함
            mask = (close >= bin_edges[i]) & (close <= bin_edges[i + 1])
        bin_volumes[i] = volume[mask].sum()

    # POC (Point of Control) - 최대 거래량 가격대
    poc_idx = bin_volumes.argmax()
    poc_price = (bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2
    poc_low = bin_edges[poc_idx]
    poc_high = bin_edges[poc_idx + 1]

    # Value Area (전체 거래량의 70% 구간)
    total_vol = bin_volumes.sum()
    sorted_indices = bin_volumes.argsort()[::-1]
    cumulative = 0
    va_indices = []
    for idx in sorted_indices:
        va_indices.append(idx)
        cumulative += bin_volumes[idx]
        if cumulative >= total_vol * 0.7:
            break

    va_low = bin_edges[min(va_indices)]
    va_high = bin_edges[max(va_indices) + 1]

    # 현재가의 매물대 대비 위치
    if current_price > va_high:
        position = "above"  # 매물대 위 → 돌파, 상승 추세
    elif current_price < va_low:
        position = "below"  # 매물대 아래 → 지지 이탈, 하락 위험
    else:
        position = "within"  # 매물대 내 → 횡보/눌림

    return {
        "poc_price": int(poc_price),
        "poc_range": (int(poc_low), int(poc_high)),
        "value_area_low": int(va_low),
        "value_area_high": int(va_high),
        "position": position,
    }


def detect_cross(short_ma: pd.Series, long_ma: pd.Series) -> str:
    """골든크로스/데드크로스 감지."""
    if len(short_ma) < 2 or len(long_ma) < 2:
        return "none"

    s_today, s_yesterday = short_ma.iloc[-1], short_ma.iloc[-2]
    l_today, l_yesterday = long_ma.iloc[-1], long_ma.iloc[-2]

    if any(pd.isna([s_today, s_yesterday, l_today, l_yesterday])):
        return "none"

    if s_yesterday <= l_yesterday and s_today > l_today:
        return "golden"
    if s_yesterday >= l_yesterday and s_today < l_today:
        return "dead"
    return "none"


def generate_signal(ohlcv_df: pd.DataFrame, config_module, investor_data: dict | None = None) -> dict:
    """
    종합 시그널 생성.

    분석 지표:
    1. RSI(14) - 과매수/과매도
    2. SMA 5/20일 골든/데드크로스
    3. EMA(9) - 단기 추세 & 가격 위치
    4. 매물대분석(10단계) - 지지/저항 & 현재가 위치

    종합 점수 기반 판단:
    - 각 지표가 매수/매도/중립 점수를 부여
    - 합산하여 최종 시그널 결정
    """
    if ohlcv_df is None or ohlcv_df.empty or len(ohlcv_df) < config_module.MA_LONG + 2:
        return {
            "current_price": None, "change_pct": None,
            "rsi": None, "ma5": None, "ma20": None, "ma60": None,
            "ema9": None, "vwap": None,
            "vp_position": None, "vp_poc": None,
            "cross": "none", "signal": "HOLD", "signal_reason": "데이터 부족",
            "score": 0,
        }

    close = ohlcv_df["종가"]

    # 기본 지표
    rsi_series = calculate_rsi(close, config_module.RSI_PERIOD)
    ma_short = calculate_ma(close, config_module.MA_SHORT)
    ma_long = calculate_ma(close, config_module.MA_LONG)
    ma_trend = calculate_ma(close, config_module.MA_TREND)

    # 추가 지표
    ema9 = calculate_ema(close, 9)
    vwap_series = calculate_vwap(ohlcv_df, period=20)
    volume_profile = calculate_volume_profile(ohlcv_df, bins=10)

    # 최신 값 추출
    latest_rsi = rsi_series.iloc[-1]
    latest_ma5 = ma_short.iloc[-1]
    latest_ma20 = ma_long.iloc[-1]
    latest_ma60 = ma_trend.iloc[-1] if not pd.isna(ma_trend.iloc[-1]) else None
    latest_ema9 = ema9.iloc[-1]
    prev_ema9 = ema9.iloc[-2] if len(ema9) >= 2 else None
    latest_vwap = vwap_series.iloc[-1] if not vwap_series.empty else None
    current_price = close.iloc[-1]
    prev_price = close.iloc[-2]
    change_pct = ((current_price - prev_price) / prev_price) * 100

    cross_signal = detect_cross(ma_short, ma_long)
    vp_position = volume_profile.get("position", "unknown")
    vp_poc = volume_profile.get("poc_price")

    # ================================================================
    # 종합 스코어 시스템 (보수적 접근)
    # 추세 점수와 리스크 점수를 분리하여 과열 시 보수적 판단
    # ================================================================
    score = 0
    reasons = []
    warnings = []  # 리스크 경고

    # VWAP 괴리율 (여러 곳에서 사용)
    vwap_diff_pct = 0
    if latest_vwap is not None and not pd.isna(latest_vwap) and latest_vwap > 0:
        vwap_diff_pct = ((current_price - latest_vwap) / latest_vwap) * 100

    # MA20 괴리율 (과열 판단용)
    ma20_diff_pct = 0
    if not pd.isna(latest_ma20) and latest_ma20 > 0:
        ma20_diff_pct = ((current_price - latest_ma20) / latest_ma20) * 100

    # ① RSI (14) — 과열/침체 핵심 지표
    if not pd.isna(latest_rsi):
        if latest_rsi >= 80:
            score -= 3
            warnings.append(f"🔥 RSI 심각한 과열 ({latest_rsi:.1f}) — 단기 조정 가능성 높음")
        elif latest_rsi >= config_module.RSI_OVERBOUGHT:
            score -= 1.5
            warnings.append(f"⚠️ RSI 과매수 ({latest_rsi:.1f}) — 추가 매수 신중")
        elif latest_rsi >= 60:
            score -= 0.5
        elif latest_rsi <= config_module.RSI_OVERSOLD:
            score += 2
            reasons.append(f"📉 RSI 과매도 ({latest_rsi:.1f}) — 반등 가능성")
        elif latest_rsi <= 40:
            score += 1
            reasons.append(f"📉 RSI 저점 영역 ({latest_rsi:.1f})")

    # ② SMA 5/20 크로스
    if cross_signal == "golden":
        score += 2
        reasons.append("🌟 5/20일 골든크로스 발생")
    elif cross_signal == "dead":
        score -= 2
        reasons.append("💥 5/20일 데드크로스 발생")
    elif not pd.isna(latest_ma5) and not pd.isna(latest_ma20):
        if latest_ma5 > latest_ma20:
            score += 0.5  # 정배열이지만 보수적 가산
        else:
            score -= 1  # 역배열

    # ③ EMA(9) — 단기 추세
    if not pd.isna(latest_ema9):
        ema_status = "위" if current_price > latest_ema9 else "아래"

        if prev_ema9 is not None and not pd.isna(prev_ema9):
            if latest_ema9 > prev_ema9 and current_price > latest_ema9:
                score += 1
                reasons.append(f"⚡ EMA9 상승세, 현재가 EMA9 {ema_status}")
            elif latest_ema9 < prev_ema9 and current_price < latest_ema9:
                score -= 1.5
                reasons.append(f"⚡ EMA9 하락세, 현재가 EMA9 {ema_status}")
            else:
                reasons.append(f"⚡ EMA9 현재가 {ema_status} (방향 혼조)")

    # ④ VWAP (20일) — 괴리율에 따른 보수적 판단
    if latest_vwap is not None and not pd.isna(latest_vwap):
        if vwap_diff_pct > 20:
            score -= 2
            warnings.append(f"🚨 VWAP 대비 +{vwap_diff_pct:.1f}% 과열 — 급락 리스크, 신규 매수 위험")
        elif vwap_diff_pct > 10:
            score -= 1
            warnings.append(f"⚠️ VWAP 대비 +{vwap_diff_pct:.1f}% 괴리 — 눌림목 대기 권장")
        elif vwap_diff_pct > 3:
            score += 0.5
            reasons.append(f"💹 VWAP 상회 +{vwap_diff_pct:.1f}%")
        elif vwap_diff_pct >= -3:
            reasons.append(f"💹 VWAP 근접 ({int(latest_vwap):,}원)")
        elif vwap_diff_pct >= -10:
            score += 1
            reasons.append(f"💹 VWAP 하회 {vwap_diff_pct:.1f}% — 매수 기회 탐색")
        else:
            score -= 1
            warnings.append(f"💹 VWAP 대비 {vwap_diff_pct:.1f}% 급락 — 추세 이탈 주의")

    # ⑤ 매물대분석
    if vp_position == "above":
        score += 1
        reasons.append(f"📊 매물대 상단 돌파 (POC {vp_poc:,}원)")
    elif vp_position == "below":
        score -= 1.5
        warnings.append(f"📊 매물대 이탈 (POC {vp_poc:,}원 아래) — 지지선 붕괴 주의")
    elif vp_position == "within":
        if vp_poc and current_price >= vp_poc:
            reasons.append(f"📊 매물대 내 상단 (POC {vp_poc:,}원)")
        else:
            score -= 0.5
            reasons.append(f"📊 매물대 내 하단 (POC {vp_poc:,}원)")

    # ⑥ MA20 괴리율 과열 체크 (추가 리스크)
    if ma20_diff_pct > 15:
        score -= 1
        warnings.append(f"🌡️ 20일선 대비 +{ma20_diff_pct:.1f}% 이격 — 평균 회귀 압력")
    elif ma20_diff_pct < -10:
        score += 0.5
        reasons.append(f"🌡️ 20일선 대비 {ma20_diff_pct:.1f}% — 반등 기대")

    # ⑦ 외국인/기관 수급 (investor_data가 있을 때만)
    if investor_data:
        f5 = investor_data.get("foreign_net_5d", 0)
        i5 = investor_data.get("inst_net_5d", 0)
        ft = investor_data.get("foreign_net_today", 0)
        it = investor_data.get("inst_net_today", 0)

        # 5일 누적 수급
        if f5 > 0 and i5 > 0:
            score += 1.5
            reasons.append(f"🏦 외국인+기관 5일 쌍끌이 매수 (외 {f5:+,} / 기 {i5:+,})")
        elif f5 > 0:
            score += 0.5
            reasons.append(f"🏦 외국인 5일 순매수 {f5:+,}")
        elif i5 > 0:
            score += 0.5
            reasons.append(f"🏦 기관 5일 순매수 {i5:+,}")
        elif f5 < 0 and i5 < 0:
            score -= 1
            warnings.append(f"🏦 외국인+기관 5일 쌍끌이 매도 (외 {f5:+,} / 기 {i5:+,})")

        # 당일 수급 (보조 지표)
        if ft > 0 and it > 0:
            score += 0.5
            reasons.append(f"📊 당일 외국인+기관 동시 매수")
        elif ft < 0 and it < 0:
            score -= 0.5

    # ================================================================
    # 최종 시그널 결정 (5단계)
    # ================================================================
    if score >= 4:
        signal = "STRONG_BUY"
    elif score >= 2:
        signal = "BUY"
    elif score >= -1:
        signal = "HOLD"
    elif score >= -3:
        signal = "REDUCE"
    else:
        signal = "SELL"

    # 종합 의견 생성
    all_reasons = reasons.copy()
    if warnings:
        all_reasons.append("⸻")
        all_reasons.extend(warnings)

    signal_reason = " / ".join(all_reasons) if all_reasons else "특이사항 없음"

    result = {
        "current_price": int(current_price),
        "change_pct": round(change_pct, 2),
        "rsi": round(latest_rsi, 1) if not pd.isna(latest_rsi) else None,
        "ma5": int(latest_ma5) if not pd.isna(latest_ma5) else None,
        "ma20": int(latest_ma20) if not pd.isna(latest_ma20) else None,
        "ma60": int(latest_ma60) if latest_ma60 is not None else None,
        "ema9": int(latest_ema9) if not pd.isna(latest_ema9) else None,
        "vwap": int(latest_vwap) if latest_vwap is not None and not pd.isna(latest_vwap) else None,
        "vp_position": vp_position,
        "vp_poc": vp_poc,
        "cross": cross_signal,
        "signal": signal,
        "signal_reason": signal_reason,
        "score": round(score, 1),
    }
    if investor_data:
        result["foreign_net_5d"] = investor_data.get("foreign_net_5d", 0)
        result["inst_net_5d"] = investor_data.get("inst_net_5d", 0)
        result["foreign_net_today"] = investor_data.get("foreign_net_today", 0)
        result["inst_net_today"] = investor_data.get("inst_net_today", 0)
        result["trading_value"] = investor_data.get("trading_value", 0)
    return result
