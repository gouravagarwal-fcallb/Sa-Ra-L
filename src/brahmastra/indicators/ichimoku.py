"""
Ichimoku Kinko Hyo — "Equilibrium at a glance"
The most comprehensive single indicator: trend, momentum, support, resistance, time.

Components:
  Tenkan-sen  (9)   — Conversion line: (9-bar high + 9-bar low) / 2
  Kijun-sen   (26)  — Base line:       (26-bar high + 26-bar low) / 2
  Senkou A         — (Tenkan + Kijun) / 2, plotted 26 bars ahead
  Senkou B    (52)  — (52-bar high + 52-bar low) / 2, plotted 26 bars ahead
  Chikou Span      — Current close plotted 26 bars behind

Cloud (Kumo): Senkou A vs B region. Bullish cloud = A > B. Bearish cloud = A < B.

Signals generated:
  - TK Cross (Tenkan crosses Kijun)
  - Price vs Cloud position
  - Chikou vs price 26 bars ago
  - Cloud twist (future A/B crossover)
"""
from __future__ import annotations

from typing import Optional
from dataclasses import dataclass, field
from collections import deque
from src.brahmastra.data.bar_builder import Bar


@dataclass
class IchimokuResult:
    tenkan:   Optional[float]    # conversion line
    kijun:    Optional[float]    # base line
    senkou_a: Optional[float]    # cloud upper/lower A (current period, not shifted)
    senkou_b: Optional[float]    # cloud B (current period)
    chikou:   Optional[float]    # current close (offset applied externally if charting)

    cloud_bullish: Optional[bool]     # True=A>B, False=A<B, None=equal/unknown
    price_vs_cloud: Optional[str]     # 'ABOVE' | 'BELOW' | 'INSIDE'
    tk_cross:       Optional[str]     # 'BULLISH' | 'BEARISH' | None
    chikou_signal:  Optional[str]     # 'BULLISH' | 'BEARISH' | None (chikou > price 26 ago)
    strength:       int               # 0–6 bullish signals present (6=strongest buy)

    def bias(self) -> str:
        """Consolidated directional bias."""
        if self.strength >= 4:
            return "STRONG_BULL"
        if self.strength >= 3:
            return "BULL"
        bull_signals = sum([
            self.price_vs_cloud == "ABOVE",
            self.cloud_bullish is True,
            self.chikou_signal == "BULLISH",
        ])
        bear_signals = sum([
            self.price_vs_cloud == "BELOW",
            self.cloud_bullish is False,
            self.chikou_signal == "BEARISH",
        ])
        if bear_signals >= 4:
            return "STRONG_BEAR"
        if bear_signals >= 3:
            return "BEAR"
        return "NEUTRAL"


class Ichimoku:
    """
    Full Ichimoku computation.
    Requires 52 bars before Senkou B is available.
    Senkou A requires 26 bars.
    TK cross requires Tenkan (9 bars).
    """

    TENKAN_PERIOD = 9
    KIJUN_PERIOD  = 26
    SENKOU_B_PERIOD = 52
    DISPLACEMENT  = 26

    def __init__(self):
        self._highs: deque[float] = deque(maxlen=self.SENKOU_B_PERIOD)
        self._lows:  deque[float] = deque(maxlen=self.SENKOU_B_PERIOD)
        self._closes: deque[float] = deque(maxlen=self.DISPLACEMENT + 1)

        # History for Chikou comparison
        self._price_history: deque[float] = deque(maxlen=self.DISPLACEMENT + 5)

        self._prev_tenkan: Optional[float] = None
        self._prev_kijun:  Optional[float] = None
        self._result:      Optional[IchimokuResult] = None

    def update(self, bar: Bar) -> Optional[IchimokuResult]:
        self._highs.append(bar.high)
        self._lows.append(bar.low)
        self._closes.append(bar.close)
        self._price_history.append(bar.close)

        n = len(self._highs)

        tenkan = kijun = senkou_a = senkou_b = chikou = None
        tk_cross = chikou_signal = None
        cloud_bullish = price_vs_cloud = None

        # Tenkan-sen
        if n >= self.TENKAN_PERIOD:
            t_highs = list(self._highs)[-self.TENKAN_PERIOD:]
            t_lows  = list(self._lows)[-self.TENKAN_PERIOD:]
            tenkan  = (max(t_highs) + min(t_lows)) / 2

        # Kijun-sen
        if n >= self.KIJUN_PERIOD:
            k_highs = list(self._highs)[-self.KIJUN_PERIOD:]
            k_lows  = list(self._lows)[-self.KIJUN_PERIOD:]
            kijun   = (max(k_highs) + min(k_lows)) / 2

        # Senkou A
        if tenkan is not None and kijun is not None:
            senkou_a = (tenkan + kijun) / 2

        # Senkou B
        if n >= self.SENKOU_B_PERIOD:
            senkou_b = (max(self._highs) + min(self._lows)) / 2

        # Chikou (current close)
        chikou = bar.close

        # Cloud analysis
        if senkou_a is not None and senkou_b is not None:
            if senkou_a > senkou_b:
                cloud_bullish = True
                cloud_top     = senkou_a
                cloud_bottom  = senkou_b
            elif senkou_b > senkou_a:
                cloud_bullish = False
                cloud_top     = senkou_b
                cloud_bottom  = senkou_a
            else:
                cloud_bullish = None
                cloud_top = cloud_bottom = senkou_a

            if bar.close > cloud_top:
                price_vs_cloud = "ABOVE"
            elif bar.close < cloud_bottom:
                price_vs_cloud = "BELOW"
            else:
                price_vs_cloud = "INSIDE"

        # TK Cross
        if (tenkan is not None and kijun is not None and
                self._prev_tenkan is not None and self._prev_kijun is not None):
            if self._prev_tenkan <= self._prev_kijun and tenkan > kijun:
                tk_cross = "BULLISH"
            elif self._prev_tenkan >= self._prev_kijun and tenkan < kijun:
                tk_cross = "BEARISH"

        # Chikou signal: current close vs price 26 bars ago
        ph = list(self._price_history)
        if len(ph) >= self.DISPLACEMENT + 1:
            price_26_ago = ph[-(self.DISPLACEMENT + 1)]
            if chikou > price_26_ago:
                chikou_signal = "BULLISH"
            elif chikou < price_26_ago:
                chikou_signal = "BEARISH"

        # Strength score (bullish signals 0–6)
        strength = sum([
            price_vs_cloud == "ABOVE",
            cloud_bullish is True,
            tk_cross == "BULLISH",
            chikou_signal == "BULLISH",
            (tenkan or 0) > (kijun or 0),
            bar.close > (kijun or 0),
        ])

        self._prev_tenkan = tenkan
        self._prev_kijun  = kijun

        self._result = IchimokuResult(
            tenkan         = round(tenkan, 2)   if tenkan   else None,
            kijun          = round(kijun, 2)    if kijun    else None,
            senkou_a       = round(senkou_a, 2) if senkou_a else None,
            senkou_b       = round(senkou_b, 2) if senkou_b else None,
            chikou         = round(chikou, 2),
            cloud_bullish  = cloud_bullish,
            price_vs_cloud = price_vs_cloud,
            tk_cross       = tk_cross,
            chikou_signal  = chikou_signal,
            strength       = strength,
        )
        return self._result

    @property
    def value(self) -> Optional[IchimokuResult]:
        return self._result

    @property
    def is_ready(self) -> bool:
        return (self._result is not None and
                self._result.senkou_b is not None)
