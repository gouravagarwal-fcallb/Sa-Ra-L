# BRAHMASTRA_v1 — Complete System Blueprint

> *"The weapon that never misses. Named in the honour of Lord Brahma, the Creator.  
> It must never be used recklessly, and it must never miss."*

---

## VISION STATEMENT

BRAHMASTRA_v1 is not a trading strategy.  
It is a **market intelligence platform** — a living system that reads every market on Earth,  
every second it runs, builds an evolving picture of reality, and acts only when certainty is  
so high that losing becomes the exception, not the rule.

**Core principle:**  
Ignore all noise. Take only the trades the market hands you on a silver plate.  
No two days are the same. No two expiries are the same. No fixed benchmarks.  
Only live, adaptive, relentless analysis — swift as a blink of eyes.

**Financial goal it serves:**  
Rs. 10,000 compounding at 40–80% monthly through high-conviction, precision trades.  
No gambling. No chasing. Only surgical strikes.

---

## WHAT MAKES THIS DIFFERENT FROM EVERY OTHER STRATEGY IN Sa-Ra-L

| Existing Strategies | BRAHMASTRA_v1 |
|---|---|
| One instrument (Nifty or Sensex) | Any instrument, any market globally |
| Fixed time windows | Reads market every second, adapts continuously |
| Single timeframe logic | 6 simultaneous timeframes (1m → 1W) |
| One indicator set | 25+ indicators from Western + Japanese philosophy |
| Binary signal (buy/sell) | 3 live scenarios with evolving confidence % |
| Fixed SL/target | SL + target + probability revised every tick |
| Expiry-specific or intraday-specific | Runs 8:00 AM to 3:30 PM, no pre-assigned role |
| No pre-market intelligence | Full pre-market briefing with global context |
| No UI | Rich real-time dashboard with all analysis visible |

---

## SYSTEM ARCHITECTURE — 9 LAYERS

```
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 9 — USER INTERFACE (React + FastAPI)                         │
│  Real-time dashboard · Trade monitor · Scenario visualizer          │
├─────────────────────────────────────────────────────────────────────┤
│  LAYER 8 — TRADE EXECUTION ENGINE                                   │
│  Entry gate · Position sizing · Order management · P&L tracking     │
├─────────────────────────────────────────────────────────────────────┤
│  LAYER 7 — SCENARIO ENGINE (The Brain)                              │
│  3 parallel scenarios · Confidence scoring · Continuous revision    │
├─────────────────────────────────────────────────────────────────────┤
│  LAYER 6 — SIGNAL CONFLUENCE MATRIX                                 │
│  All indicators vote · Weighted aggregate · Trade readiness score   │
├─────────────────────────────────────────────────────────────────────┤
│  LAYER 5 — OPTIONS INTELLIGENCE                                     │
│  Max pain · PCR · IV percentile · OI buildup · Theta model          │
├─────────────────────────────────────────────────────────────────────┤
│  LAYER 4 — MULTI-TIMEFRAME INDICATOR ENGINE                         │
│  BB · Ichimoku · VWAP · RSI · MACD · ATR · ADX · Fibonacci · more  │
├─────────────────────────────────────────────────────────────────────┤
│  LAYER 3 — PRICE ACTION ENGINE                                      │
│  ORB · Gap analysis · Candlestick patterns · S/R levels             │
├─────────────────────────────────────────────────────────────────────┤
│  LAYER 2 — REAL-TIME DATA PIPELINE                                  │
│  Kite Connect WebSocket · Tick processor · Bar builder              │
├─────────────────────────────────────────────────────────────────────┤
│  LAYER 1 — PRE-MARKET INTELLIGENCE                                  │
│  Global cues · VIX · FII/DII · News events · Bias score            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## LAYER 1 — PRE-MARKET INTELLIGENCE (8:00 AM – 9:14 AM)

### Data Sources — Free vs Paid

| Data | Source | Cost | How |
|---|---|---|---|
| SGX Nifty futures | Investing.com (scrape/API) | **Free** | HTTP fetch |
| US Dow/S&P/Nasdaq close | Alpha Vantage | **Free** (25 req/day) | REST API |
| Asian markets (Nikkei, Hang Seng) | Alpha Vantage / Yahoo Finance | **Free** | REST API |
| Crude Oil price | Yahoo Finance (`CL=F`) | **Free** | yfinance |
| USD/INR | Yahoo Finance (`USDINR=X`) | **Free** | yfinance |
| Gold price | Yahoo Finance (`GC=F`) | **Free** | yfinance |
| India VIX | NSE website | **Free** | HTTP fetch |
| NSE option chain (PCR, Max Pain) | NSE website | **Free** | HTTP fetch |
| FII/DII provisional data | NSE website | **Free** | HTTP fetch |
| Economic calendar (RBI/Fed dates) | Investing.com | **Free** | HTTP scrape |
| Real-time tick data (9:15 onwards) | **Kite Connect** | **Already have** | WebSocket |
| Historical bars (backtest) | Kite Historical API | **Already have** | REST API |
| News events | NewsAPI.org | **Free** (100/day) | REST API |

**Conclusion: Zero additional cost. Kite Connect (already subscribed) is the primary data source.  
All pre-market global data comes from free sources.**

### Pre-Market Output: BIAS SCORE

```
Input factors and their weights:
  SGX Nifty direction vs prev close     : 25 pts  (strongest predictor)
  US markets close (direction + size)   : 20 pts
  India VIX (level + trend)             : 15 pts
  Asian markets direction               : 10 pts
  PCR from previous day close           : 10 pts
  FII net (buy = bullish)               : 10 pts
  Crude oil direction                   :  5 pts
  USD/INR (rupee strength = bullish)    :  5 pts

Total: 100 pts
  +60 to +100 = STRONGLY BULLISH open
  +30 to +59  = MILDLY BULLISH
  -29 to +29  = NEUTRAL — watch ORB carefully
  -59 to -30  = MILDLY BEARISH
  -100 to -60 = STRONGLY BEARISH open

News event flag:
  If RBI / Fed / Budget / major earnings today → EXTREME_CAUTION
  Reduce position sizes by 50%, widen SL by 50%
```

---

## LAYER 2 — REAL-TIME DATA PIPELINE

### Tick Processor Design

```
Kite Connect WebSocket → Tick Receiver → Bar Builder → Indicator Engine

Instruments subscribed:
  NIFTY 50 index (spot)
  SENSEX index (spot)
  NIFTY ATM CE + PE (current + next strike)
  NIFTY next-strike CE + PE (buffer)
  SENSEX ATM CE + PE
  India VIX
  USD/INR
  Top 5 FII-heavy stocks (Reliance, HDFC, ICICI, Infosys, TCS)
    — these lead index moves before they appear in Nifty

Bar Building:
  For each instrument, build bars in parallel:
  1m, 5m, 15m, 1h, 1D, 1W
  Each bar: OHLCV + VWAP-so-far

Tick frequency: ~1-3 ticks/second per instrument on Kite WebSocket
Processing latency target: < 100ms from tick receipt to indicator update
```

---

## LAYER 3 — PRICE ACTION ENGINE

### Opening Range Breakout (9:15 – 9:30 AM)

```
ORB High = max(all 1m candle highs in first 15 minutes)
ORB Low  = min(all 1m candle lows in first 15 minutes)
ORB Range = ORB High - ORB Low

Interpretation:
  Spot breaks above ORB High + holds for 2 bars = BULL BREAKOUT
  Spot breaks below ORB Low  + holds for 2 bars = BEAR BREAKDOWN
  Spot stays inside ORB after 9:45 AM = RANGE DAY — different playbook

Gap Analysis (computed at 9:15:01):
  Gap % = (Today open - Yesterday close) / Yesterday close × 100

  Gap +0.5% to +1.5% + sustains = GAP_AND_GO (buy any dip to VWAP)
  Gap +1.5%+           = EXTENDED_GAP (high fade risk, wait for ORB)
  Gap -0.5% to -1.5%  + sustains = GAP_DOWN_BREAKDOWN (sell bounce)
  Gap fills intraday  = GAP_FILL (mean reversion — trade the fill)
  Flat open (±0.2%)   = INDECISION — highest weight to ORB
```

### Candlestick Pattern Detector (20 patterns)

```
Single-bar patterns:
  Doji           : Open ≈ Close (±0.1%) — indecision, reversal warning
  Hammer         : Lower wick ≥ 2× body, small upper wick — bullish reversal at support
  Hanging Man    : Same shape as Hammer at resistance — bearish reversal
  Shooting Star  : Upper wick ≥ 2× body at resistance — bearish
  Marubozu Bull  : Full body, no wicks — strong bull candle
  Marubozu Bear  : Full body, no wicks — strong bear candle
  Spinning Top   : Small body, equal wicks — indecision

Two-bar patterns:
  Bullish Engulfing : Large bull candle swallows previous bear — strong bullish signal
  Bearish Engulfing : Large bear candle swallows previous bull — strong bearish signal
  Bullish Harami    : Small bull inside previous bear — potential reversal
  Bearish Harami    : Small bear inside previous bull — potential reversal
  Piercing Line     : Bear then bull closing above 50% of previous bear — bullish
  Dark Cloud Cover  : Bull then bear closing below 50% of previous bull — bearish

Three-bar patterns:
  Morning Star      : Bear + Doji + Bull — strong bullish reversal
  Evening Star      : Bull + Doji + Bear — strong bearish reversal
  Three White Soldiers : 3 consecutive bull candles — strong uptrend confirmation
  Three Black Crows   : 3 consecutive bear candles — strong downtrend confirmation
  Three Inside Up     : Harami + confirmation — bullish
  Three Inside Down   : Harami + confirmation — bearish
  Abandoned Baby      : Gap + Doji + Gap other side — rarest, strongest reversal

Each pattern output:
  Direction: BULL / BEAR
  Reliability: HIGH / MEDIUM / LOW  (based on 20-year backtested win rate)
  Context required: YES/NO (some patterns only valid at support/resistance)
  Weight in confluence score: 5–15 pts depending on reliability
```

---

## LAYER 4 — MULTI-TIMEFRAME INDICATOR ENGINE

All indicators computed on ALL 6 timeframes simultaneously: 1m, 5m, 15m, 1h, 1D, 1W

### Indicator Stack (25 indicators)

**Group A — Trend Direction (answer: which way?)**

```
1. Bollinger Bands (20, 2.0)
   Upper, Middle (SMA20), Lower
   %B = (price - lower) / (upper - lower)  → 0=lower band, 1=upper band
   Bandwidth = (upper - lower) / middle × 100
   Squeeze = bandwidth < 0.5% (compression before explosive move)
   Signal:
     Price > upper  → overbought or strong breakout
     Price < lower  → oversold or strong breakdown
     %B crossing 0.5 from below → bullish momentum building
     Squeeze + directional tick → imminent large move

2. VWAP (Volume Weighted Average Price)
   Resets every day at 9:15 AM
   VWAP + 1σ, VWAP + 2σ (standard deviation bands)
   Above VWAP = institutional net buyers today
   Below VWAP = institutional net sellers today
   VWAP acts as magnet — price returns to it in range days

3. EMA 9 / EMA 21 / EMA 50 / EMA 200
   EMA9 > EMA21 > EMA50 > EMA200 = Perfect bull alignment
   EMA9 < EMA21 < EMA50 < EMA200 = Perfect bear alignment
   Golden Cross (EMA50 > EMA200): major bull signal
   Death Cross  (EMA50 < EMA200): major bear signal
   EMA9 cross EMA21 on 5m: short-term trade signal

4. Ichimoku Kinko Hyo — "equilibrium at a glance" (Japanese)
   Tenkan-sen (9)   = (9-high + 9-low) / 2  — conversion line
   Kijun-sen  (26)  = (26-high + 26-low) / 2 — base line
   Senkou A   = (Tenkan + Kijun) / 2, plotted 26 bars ahead
   Senkou B   = (52-high + 52-low) / 2, plotted 26 bars ahead
   Chikou    = current close, plotted 26 bars behind

   Interpretation:
     Price above cloud  = bullish regime
     Price below cloud  = bearish regime
     Cloud is thin      = weak support/resistance
     Cloud is thick     = strong support/resistance
     TK Cross (Tenkan crosses above Kijun) = BUY signal
     Price crosses Kijun from below = strong buy
     Chikou above historical price = confirms bull
     Kumo Twist (A crosses B ahead) = regime change coming

5. Supertrend (10, 3.0)
   ATR-based trend follower
   Flips direction cleanly
   Used as secondary trend confirmation
```

**Group B — Momentum (answer: how strong?)**

```
6. RSI (14)
   Above 60 = bullish momentum
   Below 40 = bearish momentum
   Above 80 = overbought (in strong trends, can stay here)
   Below 20 = oversold
   Divergence: price makes new high but RSI lower = BEARISH DIVERGENCE (reversal warning)
   Divergence: price makes new low but RSI higher = BULLISH DIVERGENCE (reversal warning)

7. MACD (12, 26, 9)
   MACD line > Signal line = bullish momentum
   MACD line < Signal line = bearish momentum
   MACD Histogram growing = momentum accelerating
   MACD Histogram shrinking = momentum fading
   Zero line cross = trend change
   Divergence with price = powerful reversal signal

8. Stochastic RSI (14, 14, 3, 3)
   Combines RSI + Stochastic for more sensitivity
   %K > %D with %K crossing above 20 = bullish entry
   %K < %D with %K crossing below 80 = bearish entry

9. Rate of Change (ROC, 10)
   Measures speed of price movement
   Rising ROC + rising price = accelerating bull
   Falling ROC + rising price = decelerating (topping signal)
```

**Group C — Volatility (answer: how much movement?)**

```
10. ATR (14) — Average True Range
    Measures market volatility
    Stop loss = 1.5 × ATR below entry (adjusts automatically to market volatility)
    ATR expanding = big moves expected
    ATR contracting = quiet period (often before big move)

11. Bollinger Band Width (already in BB above)
    Squeeze phase = low bandwidth
    Expansion phase = high bandwidth

12. IV Percentile (options-specific)
    IV Rank = (current IV - 52w low) / (52w high - 52w low) × 100
    < 25 = cheap options — prefer BUYING
    > 75 = expensive options — prefer SELLING
    > 90 = extreme fear/greed event
```

**Group D — Volume (answer: who is behind the move?)**

```
13. Volume vs 20-bar Average
    Current volume > 2x average = institutional participation = trust the move
    Current volume < 0.5x average = low conviction = reduce position size

14. On-Balance Volume (OBV)
    Rising OBV + rising price = confirmed uptrend
    Falling OBV + rising price = divergence = rally is suspect

15. Volume Profile (Value Area)
    Point of Control (POC) = price level with highest volume
    Value Area High/Low (VAH/VAL) = 70% of volume traded here
    Price above VAH = breakout territory
    Price at POC = high chance of reversal or continuation
```

**Group E — Support / Resistance (answer: where are the walls?)**

```
16. Pivot Points — Standard (Daily, Weekly)
    R3, R2, R1, Pivot, S1, S2, S3
    Calculated from previous day/week OHLC
    These are widely watched by institutions

17. Camarilla Pivots
    Tighter than standard pivots
    R3/S3 = intraday reversal zones
    R4/S4 = breakout zones

18. Fibonacci Retracement / Extension
    From day's high to low (or swing high to swing low):
    23.6%, 38.2%, 50.0%, 61.8%, 78.6% retracements
    127.2%, 161.8%, 261.8% extensions as targets
    61.8% = golden ratio = most respected retracement level

19. Support / Resistance from prior day OHLC
    Previous Day High, Low, Close = key levels every new day

20. Round Numbers
    NIFTY: every 50 points (24000, 24050, 24100...)
    SENSEX: every 100 points (79000, 79100...)
    These act as magnets — price pauses, reverses, or accelerates here
```

**Group F — Trend Strength (answer: is there a real trend?)**

```
21. ADX (14) — Average Directional Index
    > 25 = trending market (trade in direction)
    < 20 = ranging market (trade reversals)
    > 40 = strongly trending (avoid counter-trend)
    ADX rising = trend strengthening
    ADX falling = trend weakening

22. Aroon (25)
    Aroon Up > 70 + Aroon Down < 30 = strong uptrend
    Aroon Down > 70 + Aroon Up < 30 = strong downtrend
    Both near 50 = no trend

23. Linear Regression Slope (20)
    Measures direction and steepness of current trend
    Positive slope + steepening = bull accelerating
    Negative slope + steepening = bear accelerating
```

**Group G — Market Structure (answer: what type of day is this?)**

```
24. Market Profile (statistical)
    p-shaped profile = rally with sellers above = potential reversal
    b-shaped profile = selloff with buyers below = potential bounce
    D-shaped (normal distribution) = balanced day, expect range

25. Heikin Ashi Candles (overlay on standard candles)
    Smoothed candles that filter noise
    HA bull candle = clean uptrend with no lower wicks
    HA bear candle = clean downtrend with no upper wicks
    HA doji = trend losing momentum
```

---

## LAYER 5 — OPTIONS INTELLIGENCE

```
Max Pain Calculation (updated every 5 minutes):
  Max Pain = strike where total option seller profit is maximised
  Formula: For each strike, calculate total loss of all PUT + CALL buyers
           The strike with minimum total buyer profit = Max Pain
  Market gravitates to Max Pain in last 30 min of expiry
  Distance from current spot to Max Pain = "magnetic pull" factor

Put-Call Ratio (PCR):
  PCR = Total Put OI / Total Call OI (across all strikes)
  PCR > 1.3  = more puts = bears are hedged = BULLISH (contrarian)
  PCR < 0.7  = more calls = complacency = BEARISH (contrarian)
  PCR 0.9–1.1 = balanced = wait for direction from other signals

  PCR CHANGE is more important than absolute level:
  PCR rising fast = put buying increasing = bearish sentiment building
  PCR falling fast = call buying increasing = bullish sentiment building

OI Analysis per strike:
  CE OI increasing above spot = call writers adding resistance = bearish
  CE OI decreasing above spot = call writers covering = resistance weakening = bullish
  PE OI increasing below spot = put writers adding support = bullish
  PE OI decreasing below spot = support weakening = bearish

IV (Implied Volatility) per strike:
  IV Skew: if far OTM puts have much higher IV than equivalent OTM calls
           → market fears a sharp downside move
  IV Crush: after events (RBI, results), IV drops sharply
            → never buy options just before event IV crush

Theta Decay Model (for open positions):
  Option theta accelerates in last 2 hours of expiry day
  At 3:00 PM on expiry: theta burn = roughly 15% of premium per 10 minutes
  System outputs: "₹X per minute being lost to theta" for each open position
  This forces disciplined exits — not emotional ones
```

---

## LAYER 6 — SIGNAL CONFLUENCE MATRIX

Every indicator produces a vote every tick: +1 (bullish), -1 (bearish), 0 (neutral)  
Each vote is weighted by reliability and current market regime:

```
Category           | Max weight | Indicators included
─────────────────────────────────────────────────────────
Trend direction    |    30 pts  | Ichimoku, EMA alignment, VWAP, Supertrend
Momentum           |    20 pts  | RSI, MACD, Stoch RSI, ROC
Price action       |    15 pts  | Candlestick patterns, ORB break, Gap
Support/Resistance |    15 pts  | Pivots, Fibonacci, Round numbers, Prev OHLC
Volume             |    10 pts  | OBV, Volume vs average, Volume profile
Options signal     |    10 pts  | PCR direction, OI buildup, Max Pain pull
─────────────────────────────────────────────────────────
TOTAL              |   100 pts  |

Regime adjustment:
  If ADX > 25 (trending): momentum indicators weighted higher
  If ADX < 20 (ranging):  support/resistance indicators weighted higher
  If VIX > 20: all signals reduced by 20% (high uncertainty day)

Confluence Score output:
  +70 to +100 = STRONG BUY signal
  +40 to +69  = MODERATE BUY
  -39 to +39  = NEUTRAL (no trade zone)
  -69 to -40  = MODERATE SELL
  -100 to -70 = STRONG SELL signal
```

---

## LAYER 7 — SCENARIO ENGINE (The Core Brain)

This is what makes BRAHMASTRA different from every other system.  
Three scenarios run in parallel. The system never sleeps between trades.

```
SCENARIO STRUCTURE (each has all of these, updated every tick):

Scenario A (current Bull case):
  label           : "BULL"
  entry_zone      : spot range where entry is valid
  entry_option    : specific CE strike + expiry
  stop_loss_spot  : spot level that INVALIDATES this scenario
  stop_loss_prem  : option premium SL (auto-calculated)
  target_1_spot   : first price target (50% booking)
  target_2_spot   : second price target (30% booking)
  target_3_spot   : runner target (20% remaining)
  target_1_prem   : option premium at T1
  target_2_prem   : option premium at T2
  target_3_prem   : option premium at T3
  probability     : % chance this scenario plays out (from backtested patterns)
  confidence      : % current market is confirming this scenario (live)
  time_to_target1 : estimated minutes to T1 (based on momentum)
  risk_reward     : (T1_prem - entry_prem) / (entry_prem - SL_prem)
  invalidation    : specific conditions that kill this scenario
  status          : WATCHING / ARMED / ACTIVE / INVALIDATED / COMPLETE

Scenario B (current Bear case):
  [identical structure, puts instead of calls]

Scenario C (current Sideways/Range case):
  [buy at support, sell at resistance — range trading]

Confidence Scoring (every tick):
  Start at 50% at session start (equal probability)
  
  For each indicator signal:
    Agrees with scenario  → confidence += weight × indicator_reliability
    Disagrees            → confidence -= weight × indicator_reliability
    Neutral              → confidence unchanged
  
  Confidence adjustment factors:
    Volume confirmation  → ×1.5 multiplier on any signal
    Multiple timeframes agree → +10% bonus
    Conflicting timeframes   → -10% penalty
    Pattern at key level     → +15% bonus
  
  Trade ARM condition: confidence ≥ 75% AND confluence score ≥ +70
  Trade ENTRY condition: confidence ≥ 85% for 2 consecutive ticks
  Trade ABORT condition: confidence drops below 55% while ARMED
  Scenario DEAD condition: confidence below 30%
  
  When scenario is completed (target hit or SL hit):
    System immediately begins scanning for NEW scenarios
    → BRAHMASTRA never rests between trades
    → The search continues until market close at 3:30 PM
    → But ONLY high-confidence (≥85%) trades are taken
    → If no clear scenario forms, cash is the position
```

---

## LAYER 8 — TRADE EXECUTION ENGINE

```
Position Sizing — Kelly Criterion (Conservative):
  Full Kelly: f* = (p × b - q) / b
    p = win probability (from backtest + live confidence)
    b = expected reward / risk ratio
    q = 1 - p
  
  We use Half-Kelly for safety: actual_f = f* × 0.5
  Capital risked per trade = actual_f × total capital
  Maximum cap: 15% of capital per single trade
  Premium cost cap: 3% of capital (protects against 33 consecutive losses)

Risk Management Gates (ALL must pass before entry):
  ✓ Scenario confidence ≥ 85%
  ✓ Confluence score ≥ +70 (or ≤ -70 for short)
  ✓ Volume ≥ 1.5× 20-bar average (institutional participation)
  ✓ Risk-reward ≥ 2.0:1 (minimum)
  ✓ Not within 5 bars of major S/R level (avoid buying at resistance)
  ✓ VIX not spiking >5% in last 30 minutes (event risk)
  ✓ Daily loss limit not breached (3× ATR × lot size)
  ✓ Maximum trades today not breached (configurable)
  ✓ Cooldown after last trade not active (configurable minutes)
  ✓ No major economic event in next 30 minutes

Entry Execution:
  Limit order at (market price + 0.3%) for options
  If not filled within 3 ticks → cancel, re-evaluate
  If scenario still ARMED → retry once at market
  If scenario weakening → abort

Trade Management (every tick from entry):
  Initial SL  : entry_premium - (1.5 × ATR_premium)
  Breakeven   : when unrealised P&L > entry_premium × 0.3 → move SL to entry
  Trailing SL : when unrealised P&L > entry_premium × 0.6 → trail at 40% below peak
  Time SL     : if in trade > 20 bars with < 5% gain → exit (time waste)
  Momentum SL : if 3 consecutive bars going against position → exit regardless

Every tick outputs for open trade:
  Current premium
  Unrealised P&L (₹)
  Distance to SL (₹ and %)
  Distance to T1/T2/T3
  Theta decay rate (₹/minute being lost)
  Scenario confidence (is trade still valid?)
  "Action recommendation" : HOLD / REDUCE / EXIT / ADD

Booking Strategy:
  T1 hit → book 40% quantity, move SL to entry
  T2 hit → book 40% quantity, trail SL tightly
  T3     → trail with 30% below peak until stopped
  EOD    → exit 100% by 3:20 PM regardless
```

---

## LAYER 9 — USER INTERFACE

### Dashboard Layout (React + WebSocket)

```
┌──────────────────────────────────────────────────────────────────────┐
│  BRAHMASTRA_v1  |  NIFTY 24,235  SENSEX 79,410  |  VIX 13.2  10:42 │
├────────────────────────────┬─────────────────────────────────────────┤
│  PRE-MARKET BIAS: +72 BULL │  SCENARIO ENGINE                        │
│  SGX Nifty: +0.42%         │  ┌──────────────────────────────────┐   │
│  US close: +0.31%          │  │ SCENARIO A (BULL)  Confidence:82%│   │
│  VIX: 13.2 (falling)       │  │ Entry: CE 24250  @ ₹145–155      │   │
│  FII: +₹1,240 Cr           │  │ SL: 24,180  T1:24,350  T2:24,450 │   │
│  PCR: 1.18 (bullish)       │  │ P(T1): 71%  R:R = 2.8:1          │   │
│  Max Pain: 24,200          │  │ ETA to T1: ~18 min               │   │
│                            │  └──────────────────────────────────┘   │
│                            │  ┌──────────────────────────────────┐   │
│                            │  │ SCENARIO B (BEAR)  Confidence:24%│   │
│  OPEN POSITION             │  │ Entry: PE 24200  @ ₹120–130      │   │
│  ─────────────────         │  │ SL: 24,310  T1:24,100  T2:24,000 │   │
│  CE 24250  qty=75          │  │ P(T1): 32%  R:R = 2.1:1          │   │
│  Entry: ₹148               │  │ Status: WATCHING (low confidence)│   │
│  Current: ₹167             │  └──────────────────────────────────┘   │
│  P&L: +₹1,425              │  ┌──────────────────────────────────┐   │
│  SL: ₹148 (BE)             │  │ SCENARIO C (RANGE) Confidence:18%│   │
│  T1: ₹173  T2: ₹188        │  │ Status: INACTIVE                 │   │
│  Theta: -₹3.2/min          │  └──────────────────────────────────┘   │
│  Rec: HOLD                 │                                          │
├────────────────────────────┴─────────────────────────────────────────┤
│  SIGNAL CONFLUENCE                                                     │
│  Trend:████████████████████ +28/30    Bull alignment confirmed         │
│  Momentum: ████████████░░░ +16/20    RSI=58, MACD hist growing        │
│  Price action: ██████████░ +12/15    Hammer at VWAP, above ORB High   │
│  S/R: ████████░░░░░░ +9/15           Broken R1, next R2 at 24,350     │
│  Volume: █████████░░ +8/10           Vol 1.8× avg — institutional     │
│  Options: ████████░░ +7/10           CE OI unwinding above spot       │
│  TOTAL:  ████████████████████ +80    ⬛ STRONG BUY                     │
├─────────────────────────────────────────────────────────────────────-┤
│  MULTI-TIMEFRAME STATUS                                                │
│  1m   : BULL (EMA9>21, above BB mid, RSI=61)                         │
│  5m   : BULL (TK cross, above Kijun, MACD +ve)                       │
│  15m  : BULL (above cloud, ADX=28 trending)                          │
│  1h   : BULL (above VWAP, above EMA50)                               │
│  1D   : NEUTRAL (below EMA200, above EMA50)                          │
│  1W   : NEUTRAL (inside BB, above EMA21)                             │
│  ALIGNMENT: 4/6 BULL, 2/6 NEUTRAL                                    │
├────────────────────────────────────────────────────────────────────--┤
│  LIVE ANALYSIS FEED                                   TRADE HISTORY   │
│  10:42 Bull conf↑ to 82%: RSI crossed 60 on 5m       09:47 ENTRY     │
│  10:41 CE OI at 24300 unwinding — resistance weak     10:02 T1 HIT   │
│  10:40 Ichimoku: Chikou above price hist on 5m        10:02 PARTIAL  │
│  10:38 Volume spike 2.1× avg — institutional buy      [details...]   │
│  10:35 ORB High broken + held for 3 bars              P&L: +₹2,850   │
└──────────────────────────────────────────────────────────────────────┘
```

### Mobile Alert System
```
Push notifications for:
  Trade ARMED (confidence ≥ 80%)
  Trade ENTRY placed
  T1/T2/T3 hit
  SL approaching (within 20%)
  SL hit
  Scenario invalidated
  Daily loss limit approaching
  Market anomaly detected (VIX spike, flash crash pattern)
```

---

## BACKTEST PLAN — 16 YEARS (2008–2024)

### Why 16 years, not 20:
```
NSE options data (reliable, with OI):    2008 onwards  → 16 years
NSE spot data (Nifty/Sensex):           2000 onwards  → 24 years
We backtest the FULL SYSTEM on 16 years (options + all indicators)
We backtest the SPOT-ONLY components on 24 years
```

### Market Regimes covered in 16 years:
```
2008–2009  : Global Financial Crisis (extreme bear, VIX > 60)
2010–2012  : Recovery + Euro debt crisis
2013–2014  : Election rally (Modi 1)
2015–2016  : China slowdown, demonetisation shock
2017–2018  : Bull market + IL&FS crisis
2019       : Slowdown + Modi 2 election
2020       : COVID crash (fastest -40% in history) + V-recovery
2021       : Bull market, all-time highs
2022       : Ukraine war, rate hikes, correction
2023       : Recovery, new highs
2024       : Continued bull + global uncertainty
→ BRAHMASTRA must work across ALL these regimes
```

### Backtest Report Will Include:
```
Overall Performance:
  Total trades, Win rate %, Average R:R
  Net P&L (% return on capital), CAGR
  Maximum drawdown (%, duration)
  Sharpe ratio, Sortino ratio, Calmar ratio

By Market Regime:
  Performance in each of the 11 regimes above
  Win rate in trending vs ranging markets
  VIX < 15 vs VIX 15–25 vs VIX > 25

By Instrument:
  Nifty CE trades, Nifty PE trades
  Sensex CE trades, Sensex PE trades
  Best instrument/direction combination

By Time of Day:
  9:15–10:00 trades, 10:00–12:00, 12:00–2:00, 2:00–3:20
  Expiry day vs non-expiry day performance

Scenario Engine Performance:
  How often confidence ≥ 85% actually won
  Calibration: was 85% confidence actually right 85% of the time?
  False positive rate (high confidence → losing trade)

Monthly P&L Distribution:
  Best month, worst month, average month
  Months with >40% return, months with >20% loss
  Streak analysis (consecutive wins/losses)
```

---

## PHASE-WISE DEVELOPMENT PLAN

### Phase 0 — Foundation (Week 1, ~5 days)
```
What gets built:
  TimescaleDB setup for tick storage
  Redis cache for real-time state
  Docker container configuration
  Project structure (all directories + empty modules)
  Configuration system (BRAHMASTRA config.yaml)
  Logging framework (ANALYSIS level, JSON logs)

Deliverable: System skeleton runs, connects to Kite WebSocket,
             stores ticks, nothing breaks.
Time estimate: 4–5 days
```

### Phase 1 — Data Pipeline (Week 1–2, ~5 days)
```
What gets built:
  Kite WebSocket tick receiver
  Multi-instrument bar builder (1m/5m/15m/1h/1D/1W parallel)
  Pre-market data fetcher (global indices, VIX, FII, PCR)
  Pre-market BIAS score calculator
  Historical bar backfill (Kite Historical API)
  NSE option chain parser (existing, enhance)
  Max Pain calculator
  PCR real-time tracker

Deliverable: All data flowing. BIAS score printed at 8:15 AM.
             Bars updating every minute.
Time estimate: 4–5 days
```

### Phase 2 — Indicator Engine (Week 2–3, ~7 days)
```
What gets built:
  All 25 indicators (some exist, many new)
  Ichimoku calculator (new — complex)
  Candlestick pattern detector (20 patterns, new)
  Multi-timeframe indicator manager
  Signal vote generator per indicator
  Confluence score calculator

Deliverable: Every bar shows complete indicator state.
             Confluence score updating live.
Time estimate: 6–8 days (Ichimoku + candlestick = most complex)
```

### Phase 3 — Scenario Engine (Week 3–4, ~5 days)
```
What gets built:
  Scenario data structure
  Confidence score updater (tick-by-tick)
  Bull/Bear/Range scenario generators
  Scenario ARM/ENTRY/ABORT/COMPLETE state machine
  Continuous rescan after each trade completes

Deliverable: 3 scenarios running live with confidence % visible.
             System arms itself when confidence ≥ 75%.
Time estimate: 4–6 days
```

### Phase 4 — Trade Engine (Week 4–5, ~5 days)
```
What gets built:
  Entry gate (all filters)
  Kelly position sizer
  Order placement (Kite, with paper mode)
  Trade manager (tick-by-tick SL/target updates)
  Theta decay tracker
  Booking logic (T1/T2/T3)
  EOD forced exit

Deliverable: Complete paper trading cycle.
             Enters, manages, exits trades automatically.
Time estimate: 4–5 days
```

### Phase 5 — Backtest Framework (Week 5–7, ~10 days)
```
What gets built:
  16-year historical data downloader
  Bar simulation engine (replays historical bars as if live)
  Full strategy simulation on historical data
  Performance report generator
  All statistics from backtest plan above
  Equity curve, drawdown chart, monthly P&L heatmap

Deliverable: Complete 16-year backtest report.
             All metrics computed and saved.
Time estimate: 8–12 days (data download alone = 2–3 days)
```

### Phase 6 — Live Paper Validation (Week 7–8, ~5 days)
```
What gets built:
  Paper trading for minimum 5 full market days
  Compare live paper results vs backtest expectations
  Calibrate confidence thresholds based on live data
  Log all decisions for review

Deliverable: Confirmed the system works live as expected.
             No surprises between backtest and live.
Time estimate: 5 trading days minimum
```

### Phase 7 — UI/Frontend (Week 8–10, ~10 days)
```
What gets built:
  FastAPI backend with WebSocket
  React frontend (dashboard as designed above)
  Mobile-responsive layout
  Push notification system
  Historical P&L charts
  Scenario confidence visualizer (live updating)
  Trade log with full entry/exit analysis

Deliverable: Full visual dashboard accessible from browser.
             All data visible in real-time.
Time estimate: 8–12 days
```

### Phase 8 — Live Deployment (Week 10+)
```
Gradual deployment:
  Week 1 live: 10% of capital only, 2 trades max/day
  Week 2 live: 25% of capital, observe
  Week 3 live: 50% of capital if Week 1+2 match backtest
  Week 4+: Full capital deployment

Never skip paper validation. Never rush to full capital.
```

---

## PERPETUAL MAINTENANCE FRAMEWORK

### What Changes in Markets (and needs tracking):
```
Regulatory changes:
  SEBI margin rules (change frequently — impact position sizing)
  NSE/BSE expiry day changes (recently changed to Tuesday + Thursday)
  New instrument introductions (new indices, new contracts)
  Tax law changes affecting options trading

Technology changes:
  Kite API updates (breaking changes)
  New data sources becoming available
  Better ML/AI models for prediction

Market regime changes:
  Algorithmic trading % increasing (patterns change)
  New participants (foreign retail, new institutions)
  New correlations emerging (crypto ↔ equity, etc.)
```

### Maintenance System:
```
Automated:
  Weekly: Re-run last 30 days through backtest
          Alert if live performance deviates > 15% from backtest
  Monthly: Full strategy parameter re-optimisation
  Quarterly: Regime analysis — is the strategy still calibrated?

Manual review triggers:
  Any month with drawdown > 20%
  Any regulatory announcement affecting options
  Any API deprecation notice from Kite
  Major market structure change (new expiry days, contract changes)

Version control:
  All strategy changes in git with dated branches
  BRAHMASTRA_v1, BRAHMASTRA_v2, etc.
  Old versions kept — never deleted
  Can roll back to any previous version in <5 minutes

Backup:
  All trade logs: daily backup to cloud
  All config files: committed to git after every change
  Database: daily snapshot
  Code: GitHub (already set up)
```

---

## TOTAL TIMELINE ESTIMATE

```
Phase 0 (Foundation)          :  Week 1       (5 days)
Phase 1 (Data Pipeline)       :  Week 1–2     (5 days)
Phase 2 (Indicator Engine)    :  Week 2–3     (7 days)
Phase 3 (Scenario Engine)     :  Week 3–4     (5 days)
Phase 4 (Trade Engine)        :  Week 4–5     (5 days)
Phase 5 (Backtest)            :  Week 5–7     (10 days)
Phase 6 (Paper Validation)    :  Week 7–8     (5 trading days)
Phase 7 (UI/Frontend)         :  Week 8–10    (10 days)
Phase 8 (Live Deployment)     :  Week 10+     (ongoing)
─────────────────────────────────────────────────────
Total to first live trade     :  ~10 weeks
Total to full deployment      :  ~12 weeks

Time can be saved by:
  - Running Phase 5 (backtest download) in background while Phase 3–4 build
  - Running Phase 6 (paper trading) in background while Phase 7 (UI) builds
  - Parallel development of frontend + backend after Phase 4
```

---

## WHAT WE START WITH (Phase 0)

When you confirm, the first commit will create:

```
strategies/BRAHMASTRA_v1/
  BLUEPRINT.md          ← this document
  config.yaml           ← all parameters
  
src/brahmastra/
  __init__.py
  data/
    fetchers/
      kite_stream.py      ← WebSocket tick receiver
      premarket_fetch.py  ← global data at 8 AM
      nse_options.py      ← option chain + max pain + PCR
    bar_builder.py        ← builds 1m/5m/15m/1h/1D/1W bars
    tick_store.py         ← stores ticks to TimescaleDB
  indicators/
    bollinger.py
    ichimoku.py           ← complex — week 2 priority
    vwap.py
    rsi.py
    macd.py
    atr.py
    adx.py
    supertrend.py
    volume_profile.py
    pivot_points.py
    fibonacci.py
    candlestick_patterns.py  ← 20 patterns
    confluence_scorer.py     ← weights + final score
  scenarios/
    scenario_engine.py
    confidence_scorer.py
  trading/
    entry_gate.py
    position_sizer.py
    trade_manager.py
    risk_manager.py
  backtest/
    simulator.py
    report_generator.py
  ui/
    api/
      main.py             ← FastAPI
      websocket.py
    frontend/             ← React (Phase 7)
  brahmastra_live.py      ← main runner
  brahmastra_backtest.py  ← backtest runner
```

---

## AUTONOMY LAYER — FULLY SELF-OPERATING

This is a core design requirement, not an add-on.  
**BRAHMASTRA operates autonomously by default. Humans intervene by choice, not necessity.**

### Autonomy Modes

```
MODE 1 — FULL AUTO (default):
  System does everything:
    Pre-market analysis at 8:00 AM → generates BIAS score
    9:15 AM → begins live scanning
    Entry condition met → places order automatically
    Trade management → adjusts SL/targets every tick
    T1/T2/T3 → books automatically
    EOD → closes all positions automatically
    Sends push notification at each event (entry/exit/T1/SL)
  Human does: nothing. Just reviews the report at end of day.

MODE 2 — ARMED + CONFIRM (semi-auto):
  System does all analysis and arms trades
  Before executing: sends alert to user with:
    "BRAHMASTRA ARMED: CE 24250 @ ₹148  SL=₹118  T1=₹173
     Confidence: 87%  R:R=2.8:1  Conviction: HIGH
     ⏱ 30 seconds to auto-execute. Reply SKIP to cancel."
  If no response in 30 seconds → executes automatically
  User can override by replying SKIP within the window

MODE 3 — ALERT ONLY (human executes):
  System does all analysis
  Sends alerts but never places orders
  User manually executes on Kite
  System tracks the trade if user confirms entry price

MODE 4 — SHADOW (analysis only, no trades):
  Full analysis running, all scenarios scored
  No orders placed, no alerts for entry
  Used for: testing a new configuration before going live
            learning phase for new users
            after a losing streak (cooldown)
```

### Mode Switching
```
User can switch mode at any time via:
  1. UI dashboard toggle (one click)
  2. Config file change (mode: "full_auto" / "armed_confirm" / "alert_only" / "shadow")
  3. Emergency STOP button on dashboard → immediately exits all positions + goes to shadow

Auto mode-downgrade safety rules:
  If daily loss > 2× ATR × lot_size → auto-downgrade to MODE 2 for rest of day
  If consecutive losses = 3 → pause 30 minutes, then resume MODE 2
  If VIX spikes >10% in 1 hour → auto-downgrade to MODE 2
  If market circuit breaker (15% upper/lower) → immediate FULL STOP

Human override always wins:
  Dashboard PAUSE button  → pauses all new entries (does NOT exit open trades)
  Dashboard STOP button   → exits all open positions at market, halts for day
  Dashboard EXIT button   → exits specific position immediately
  These overrides are logged with timestamp for review
```

### Autonomous Daily Lifecycle
```
8:00 AM  : Pre-market fetch begins (global data, VIX, PCR)
8:15 AM  : BIAS score calculated, push notification sent to user:
           "📊 BRAHMASTRA Pre-Market: BIAS=+72 BULLISH
            SGX+0.4% | VIX=13.2↓ | FII=+₹1,240Cr | PCR=1.18
            Recommended: BUY CE bias. Watching ORB at 9:15."
9:14 AM  : Final pre-market check. Indicators initialised.
9:15 AM  : ORB building begins. Scenarios initialised.
9:30 AM  : ORB locked. Scenario confidence scoring begins.
[ongoing] : Every tick → indicators updated → scenarios scored
           → if ARMED → waiting for confirmation bar
           → if ENTRY → order placed → trade managed
           → if T1 hit → partial booked → SL moved to BE
           → if EOD → full exit
3:20 PM  : All positions squared. No new entries.
3:25 PM  : EOD report generated, push notification:
           "📈 BRAHMASTRA EOD: +₹3,450 (+23.0%)
            Trades: 2 | Wins: 2 | W/L: 2/0
            Max drawdown today: ₹420
            Best trade: CE 24250 +₹2,850"
3:30 PM  : System enters sleep mode until 8:00 AM next day.
```

### Audit Trail (every autonomous action logged)
```
Every decision the system takes autonomously is logged with:
  Timestamp (millisecond precision)
  Reason for decision (which indicators, which scenario, confidence %)
  Alternative that was considered (what it chose NOT to do)
  Confidence at time of decision
  Outcome (filled at what price, P&L)

This log serves dual purpose:
  1. Human review: understand WHY the system did what it did
  2. Strategy improvement: find patterns in wrong decisions
```

---

## ONE FINAL PRINCIPLE

> *In the Mahabharata, the Brahmastra was invoked only when all other options were exhausted  
> and the situation demanded absolute certainty of outcome.*
>
> *This system trades the same way:*  
> *When analysis reaches 85% confidence — it strikes.*  
> *All other times — it watches, learns, and waits.*
>
> *Patience is not weakness. It is the weapon being aimed.*

---

*Blueprint version 1.0 — 25-Jun-2026*  
*Sa-Ra-L | BRAHMASTRA_v1*
