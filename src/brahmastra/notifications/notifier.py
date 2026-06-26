"""
BRAHMASTRA Notification System
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sends trade alerts and daily briefings via:
  • Email (SMTP/Gmail — no paid API)
  • Telegram Bot (free — instant push to phone)
  • WhatsApp (Twilio API — optional paid)

All credentials are read from config/settings.local.yaml (gitignored).
NEVER hardcoded here.

Event types and which channels they go to:
  PRE_MARKET_BRIEFING  → all enabled channels (daily 8:00 AM)
  SCENARIO_ARMED       → Telegram only (low priority)
  ENTRY_SIGNAL         → all channels (high priority)
  TRADE_ENTERED        → all channels
  PARTIAL_BOOKING      → Telegram + Email
  TRADE_EXITED         → all channels
  SL_HIT               → all channels (urgent)
  EOD_REPORT           → all channels
  SYSTEM_ALERT         → all channels (errors, connectivity issues)
"""
from __future__ import annotations

import threading
import queue
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))


class NotificationEvent(str, Enum):
    PRE_MARKET_BRIEFING = "PRE_MARKET_BRIEFING"
    SCENARIO_ARMED      = "SCENARIO_ARMED"
    SETUP_BUILDING      = "SETUP_BUILDING"      # score 60–70, rising — watch alert
    SIGNAL_PENDING      = "SIGNAL_PENDING"      # score hit threshold, awaiting human approval
    ENTRY_SIGNAL        = "ENTRY_SIGNAL"
    TRADE_ENTERED       = "TRADE_ENTERED"
    PARTIAL_BOOKING     = "PARTIAL_BOOKING"
    TRADE_EXITED        = "TRADE_EXITED"
    SL_HIT              = "SL_HIT"
    EOD_REPORT          = "EOD_REPORT"
    SYSTEM_ALERT        = "SYSTEM_ALERT"
    WEEKLY_SUMMARY      = "WEEKLY_SUMMARY"
    # Phase 6d Scout Mode events
    SCOUT_ALERT       = "SCOUT_ALERT"         # TREND day confirmed — entering ALERT state
    SCOUT_TRADE_ENTRY = "SCOUT_TRADE_ENTRY"   # momentum entry triggered in scout mode
    SCOUT_TRADE_EXIT  = "SCOUT_TRADE_EXIT"    # scout position closed


# Which events go to which channels
_EMAIL_EVENTS = {
    NotificationEvent.PRE_MARKET_BRIEFING,
    NotificationEvent.ENTRY_SIGNAL,
    NotificationEvent.SIGNAL_PENDING,
    NotificationEvent.TRADE_ENTERED,
    NotificationEvent.TRADE_EXITED,
    NotificationEvent.SL_HIT,
    NotificationEvent.EOD_REPORT,
    NotificationEvent.SYSTEM_ALERT,
    NotificationEvent.WEEKLY_SUMMARY,
    NotificationEvent.SCOUT_TRADE_ENTRY,
    NotificationEvent.SCOUT_TRADE_EXIT,
}
_TELEGRAM_EVENTS = set(NotificationEvent)   # all events including SETUP_BUILDING
_WHATSAPP_EVENTS = {
    NotificationEvent.SIGNAL_PENDING,
    NotificationEvent.ENTRY_SIGNAL,
    NotificationEvent.TRADE_ENTERED,
    NotificationEvent.TRADE_EXITED,
    NotificationEvent.SL_HIT,
    NotificationEvent.EOD_REPORT,
    NotificationEvent.SCOUT_TRADE_ENTRY,
    NotificationEvent.SCOUT_TRADE_EXIT,
}


@dataclass
class Notification:
    event:    NotificationEvent
    subject:  str
    body:     str
    html:     Optional[str] = None
    priority: str = "normal"   # 'normal' | 'high' | 'urgent'
    ts:       datetime = None

    def __post_init__(self):
        if self.ts is None:
            self.ts = datetime.now(IST)


class _EmailSender:
    """SMTP email sender. Works with Gmail App Password."""

    def __init__(self, cfg: dict):
        self._server   = cfg.get("smtp_server", "smtp.gmail.com")
        self._port     = cfg.get("smtp_port", 587)
        self._user     = cfg.get("username", "")
        self._password = cfg.get("password", "")
        self._to       = cfg.get("to_address", "")
        self._from     = cfg.get("from_name", "BRAHMASTRA_v1")

    def send(self, subject: str, body: str, html: Optional[str] = None) -> bool:
        if not self._user or not self._to:
            return False
        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[BRAHMASTRA] {subject}"
            msg["From"]    = f"{self._from} <{self._user}>"
            msg["To"]      = self._to

            msg.attach(MIMEText(body, "plain"))
            if html:
                msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP(self._server, self._port) as server:
                server.ehlo()
                server.starttls()
                server.login(self._user, self._password)
                server.sendmail(self._user, self._to, msg.as_string())
            return True
        except Exception as e:
            print(f"[BRAHMASTRA] Email send failed: {e}")
            return False


class _TelegramSender:
    """Telegram Bot API sender. Instant push to any device."""

    def __init__(self, cfg: dict):
        self._token   = cfg.get("bot_token", "")
        self._chat_id = str(cfg.get("chat_id", ""))

    def send(self, message: str, parse_mode: str = "Markdown") -> bool:
        if not self._token or not self._chat_id:
            return False
        try:
            import urllib.request
            import urllib.parse
            import json

            url  = f"https://api.telegram.org/bot{self._token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id":    self._chat_id,
                "text":       message,
                "parse_mode": parse_mode,
            }).encode()

            with urllib.request.urlopen(url, data=data, timeout=10) as resp:
                result = json.loads(resp.read())
                return result.get("ok", False)
        except Exception as e:
            print(f"[BRAHMASTRA] Telegram send failed: {e}")
            return False


class _WhatsAppSender:
    """WhatsApp via Twilio API (optional paid channel)."""

    def __init__(self, cfg: dict):
        self._sid   = cfg.get("account_sid", "")
        self._token = cfg.get("auth_token", "")
        self._from  = cfg.get("from_number", "")   # whatsapp:+14155238886
        self._to    = cfg.get("to_number", "")     # whatsapp:+91XXXXXXXXXX

    def send(self, message: str) -> bool:
        if not self._sid or not self._to:
            return False
        try:
            from twilio.rest import Client
            client = Client(self._sid, self._token)
            client.messages.create(
                body = message,
                from_= self._from,
                to   = self._to,
            )
            return True
        except ImportError:
            print("[BRAHMASTRA] twilio not installed: pip install twilio")
            return False
        except Exception as e:
            print(f"[BRAHMASTRA] WhatsApp send failed: {e}")
            return False


# ── Message formatters ──────────────────────────────────────────────────────

def format_entry_signal(
    instrument: str,
    hypothesis: str,
    strike: int,
    option_type: str,
    confidence: float,
    entry_price: float,
    sl_price: float,
    target1: float,
    target2: float,
    target3: float,
    lot_size: int,
    lots: int,
) -> str:
    arrow  = "🟢" if hypothesis == "BULL" else "🔴"
    rr1    = round((target1 - entry_price) / (entry_price - sl_price), 2) if hypothesis == "BULL" else round((entry_price - target1) / (sl_price - entry_price), 2)
    return f"""{arrow} *BRAHMASTRA ENTRY SIGNAL*
━━━━━━━━━━━━━━━━━━━━━━
Instrument : {instrument} {strike}{option_type}
Direction  : {hypothesis}
Confidence : {confidence:.1f}%
━━━━━━━━━━━━━━━━━━━━━━
Entry  : Rs.{entry_price:.2f}
SL     : Rs.{sl_price:.2f}  (risk per lot: Rs.{abs(entry_price-sl_price)*lot_size:.0f})
T1     : Rs.{target1:.2f}  (R/R {rr1:.1f}x)
T2     : Rs.{target2:.2f}
T3     : Rs.{target3:.2f}
Lots   : {lots}  |  Qty: {lots * lot_size}
━━━━━━━━━━━━━━━━━━━━━━
⚠️  MIS product — auto-squares at 3:20 PM"""


def format_trade_exit(
    instrument: str,
    hypothesis: str,
    strike: int,
    option_type: str,
    entry_price: float,
    exit_price: float,
    quantity: int,
    reason: str,
    net_pnl: float,
) -> str:
    icon  = "✅" if net_pnl >= 0 else "❌"
    arrow = "▲" if net_pnl >= 0 else "▼"
    return f"""{icon} *BRAHMASTRA EXIT*
━━━━━━━━━━━━━━━━━━━━━━
{instrument} {strike}{option_type}  ({hypothesis})
Entry : Rs.{entry_price:.2f}
Exit  : Rs.{exit_price:.2f}  [{reason}]
Qty   : {quantity}
━━━━━━━━━━━━━━━━━━━━━━
P&L   : Rs.{net_pnl:+.0f}  {arrow}"""


def format_weekly_summary(
    week_label: str,
    trades: list,
    week_pnl: float,
    win_rate: float,
    best_trade: dict,
    worst_trade: dict,
    capital_start: float,
    capital_end: float,
    next_week_events: list,
) -> tuple[str, str]:
    """
    Returns (plain_text, html) for the weekly summary email.
    Sent every Sunday at 18:00 IST.
    """
    total_trades = len(trades)
    wins   = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    losses = total_trades - wins
    returns_pct = ((capital_end - capital_start) / capital_start * 100) if capital_start else 0.0
    icon = "📈" if week_pnl >= 0 else "📉"

    best_line  = (f"{best_trade.get('instrument','?')} {best_trade.get('hypothesis','?')} "
                  f"Rs.{best_trade.get('net_pnl',0):+.0f}")
    worst_line = (f"{worst_trade.get('instrument','?')} {worst_trade.get('hypothesis','?')} "
                  f"Rs.{worst_trade.get('net_pnl',0):+.0f}")

    events_block = "\n".join(f"  • {e}" for e in next_week_events) if next_week_events else "  None scheduled"

    plain = f"""{icon} *BRAHMASTRA WEEKLY SUMMARY*  {week_label}
━━━━━━━━━━━━━━━━━━━━━━━━
Total Trades  : {total_trades}  (W:{wins} / L:{losses})
Win Rate      : {win_rate:.1f}%
Week P&L      : Rs.{week_pnl:+.0f}
Returns       : {returns_pct:+.2f}%
Capital Start : Rs.{capital_start:,.0f}
Capital End   : Rs.{capital_end:,.0f}
━━━━━━━━━━━━━━━━━━━━━━━━
Best Trade    : {best_line}
Worst Trade   : {worst_line}
━━━━━━━━━━━━━━━━━━━━━━━━
Next Week Events:
{events_block}
━━━━━━━━━━━━━━━━━━━━━━━━
BRAHMASTRA resumes Monday 8:00 AM IST."""

    rows = "".join(
        f"<tr style='background:{'#f9f9f9' if i%2 else 'white'}'>"
        f"<td>{t.get('instrument','')}</td>"
        f"<td>{t.get('hypothesis','')}</td>"
        f"<td style='color:{'green' if t.get('net_pnl',0)>=0 else 'red'}'>"
        f"Rs.{t.get('net_pnl',0):+.0f}</td></tr>"
        for i, t in enumerate(trades)
    )
    events_li = "".join(f"<li>{e}</li>" for e in next_week_events) if next_week_events else "<li>None</li>"

    html = f"""<html><body style='font-family:Arial,sans-serif;max-width:600px'>
<h2 style='color:#1a237e'>{icon} BRAHMASTRA Weekly Summary — {week_label}</h2>
<table width='100%' cellpadding='6' cellspacing='0' border='1' style='border-collapse:collapse'>
  <tr style='background:#1a237e;color:white'><th colspan='2'>Metric</th><th>Value</th></tr>
  <tr><td colspan='2'>Total Trades</td><td>{total_trades} (W:{wins}/L:{losses})</td></tr>
  <tr><td colspan='2'>Win Rate</td><td>{win_rate:.1f}%</td></tr>
  <tr><td colspan='2'>Week P&L</td>
      <td style='color:{"green" if week_pnl>=0 else "red"};font-weight:bold'>
          Rs.{week_pnl:+,.0f}</td></tr>
  <tr><td colspan='2'>Returns</td><td>{returns_pct:+.2f}%</td></tr>
  <tr><td colspan='2'>Capital</td><td>Rs.{capital_start:,.0f} → Rs.{capital_end:,.0f}</td></tr>
  <tr><td colspan='2'>Best Trade</td><td>{best_line}</td></tr>
  <tr><td colspan='2'>Worst Trade</td><td>{worst_line}</td></tr>
</table>
<h3>Trade Log</h3>
<table width='100%' cellpadding='4' cellspacing='0' border='1' style='border-collapse:collapse'>
  <tr style='background:#eee'><th>Instrument</th><th>Direction</th><th>P&L</th></tr>
  {rows}
</table>
<h3>Next Week Events</h3>
<ul>{events_li}</ul>
<p style='color:#777;font-size:12px'>Generated by BRAHMASTRA_v1 — Sunday EOD Report</p>
</body></html>"""

    return plain, html


def format_eod_report(
    date_str: str,
    total_trades: int,
    wins: int,
    losses: int,
    win_rate: float,
    session_pnl: float,
    best_trade: float,
    worst_trade: float,
    total_ticks: int,
) -> str:
    icon = "📈" if session_pnl >= 0 else "📉"
    return f"""{icon} *BRAHMASTRA EOD REPORT*  {date_str}
━━━━━━━━━━━━━━━━━━━━━━
Trades     : {total_trades}  (W:{wins} / L:{losses})
Win Rate   : {win_rate:.0f}%
Session P&L: Rs.{session_pnl:+.0f}
Best Trade : Rs.{best_trade:+.0f}
Worst Trade: Rs.{worst_trade:+.0f}
━━━━━━━━━━━━━━━━━━━━━━
Ticks processed: {total_ticks:,}
BRAHMASTRA resumes tomorrow 8:00 AM IST."""


def format_scout_alert(
    instrument:     str,
    adx:            float,
    range_pct:      float,
    vwap_crossings: int,
    time_str:       str,
) -> str:
    return (
        f"👁️ *BRAHMASTRA SCOUT ALERT*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{instrument} — TREND day at {time_str}\n"
        f"ADX       : {adx:.1f}  (min 22)\n"
        f"Range     : {range_pct:.0f}% of daily ATR\n"
        f"VWAP chop : {vwap_crossings} crossings (max 3)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Scanning for momentum entry..."
    )


def format_scout_trade_entry(
    instrument:     str,
    direction:      str,
    entry_price:    float,
    trail_stop:     float,
    momentum_score: float,
    time_str:       str,
) -> str:
    arrow = "🟢" if direction == "BULL" else "🔴"
    risk  = abs(entry_price - trail_stop)
    return (
        f"{arrow} *SCOUT TRADE ENTRY*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{instrument}  {direction}  @ Rs.{entry_price:.0f}  [{time_str}]\n"
        f"Momentum  : {momentum_score:+.1f}\n"
        f"Trail SL  : Rs.{trail_stop:.0f}  (risk/lot: Rs.{risk:.0f})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Exit: ATR trail ÷ reversal signal — no fixed target"
    )


def format_scout_trade_exit(
    instrument:  str,
    direction:   str,
    entry_price: float,
    exit_price:  float,
    exit_reason: str,
    pnl:         float,
    time_str:    str,
) -> str:
    icon  = "✅" if pnl >= 0 else "❌"
    arrow = "▲" if pnl >= 0 else "▼"
    move  = (exit_price - entry_price
             if direction == "BULL"
             else entry_price - exit_price)
    return (
        f"{icon} *SCOUT TRADE EXIT*  {arrow}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{instrument}  {direction}\n"
        f"Entry : Rs.{entry_price:.0f}\n"
        f"Exit  : Rs.{exit_price:.0f}  [{exit_reason}]  [{time_str}]\n"
        f"Move  : {move:+.0f} pts\n"
        f"P&L   : Rs.{pnl:+.0f}  {arrow}"
    )


# ── Main notifier ───────────────────────────────────────────────────────────

class BrahmastraNotifier:
    """
    Thread-safe notification dispatcher.
    Runs a background thread to avoid blocking the trading loop.

    Usage:
        notifier = BrahmastraNotifier(settings)
        notifier.send(NotificationEvent.ENTRY_SIGNAL, subject, body)
        notifier.stop()
    """

    def __init__(self, settings: dict):
        cfg = settings.get("notifications", {})

        self._email_enabled   = cfg.get("email", {}).get("enabled", False)
        self._tg_enabled      = cfg.get("telegram", {}).get("enabled", False)
        self._wa_enabled      = cfg.get("whatsapp", {}).get("enabled", False)

        self._email   = _EmailSender(cfg.get("email", {}))    if self._email_enabled else None
        self._telegram = _TelegramSender(cfg.get("telegram", {})) if self._tg_enabled else None
        self._whatsapp = _WhatsAppSender(cfg.get("whatsapp", {})) if self._wa_enabled else None

        self._q: queue.Queue[Optional[Notification]] = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def send(
        self,
        event:    NotificationEvent,
        subject:  str,
        body:     str,
        html:     Optional[str] = None,
        priority: str = "normal",
    ) -> None:
        """Non-blocking: queues notification for background delivery."""
        n = Notification(event=event, subject=subject, body=body,
                          html=html, priority=priority)
        self._q.put(n)

    def send_setup_building(
        self,
        instrument: str,
        hypothesis: str,
        score: float,
        threshold: float,
        bars_to_entry: Optional[int],
        headline: str,
    ) -> None:
        """Watch-tier alert: setup building but not yet triggered. Telegram only."""
        arrow  = "📈" if hypothesis == "BULL" else "📉"
        eta    = f"~{bars_to_entry*5}m" if bars_to_entry else "unknown"
        body   = (
            f"{arrow} *BRAHMASTRA WATCH ALERT*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{instrument} {hypothesis} setup building\n"
            f"Score: {score:.0f} / {threshold:.0f}  (rising)\n"
            f"Entry window: {eta}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{headline}"
        )
        self.send(NotificationEvent.SETUP_BUILDING, f"{instrument} Setup Building", body)

    def send_signal_pending(self, pending_signal_telegram_text: str) -> None:
        """High-priority alert: signal hit threshold, waiting for human approval."""
        self.send(
            NotificationEvent.SIGNAL_PENDING,
            "Signal Pending Approval",
            pending_signal_telegram_text,
            priority="urgent",
        )

    def send_entry_signal(self, **kwargs) -> None:
        body = format_entry_signal(**kwargs)
        self.send(NotificationEvent.ENTRY_SIGNAL, "Entry Signal", body, priority="high")

    def send_trade_exit(self, **kwargs) -> None:
        body = format_trade_exit(**kwargs)
        event = (NotificationEvent.SL_HIT
                 if "SL" in kwargs.get("reason", "")
                 else NotificationEvent.TRADE_EXITED)
        self.send(event, "Trade Exit", body,
                  priority="urgent" if "SL" in kwargs.get("reason", "") else "high")

    def send_eod_report(self, **kwargs) -> None:
        body = format_eod_report(**kwargs)
        self.send(NotificationEvent.EOD_REPORT, "EOD Report", body)

    def send_premarket_briefing(self, briefing_text: str) -> None:
        self.send(
            NotificationEvent.PRE_MARKET_BRIEFING,
            "Pre-Market Briefing",
            briefing_text,
        )

    def send_weekly_summary(
        self,
        week_label: str,
        trades: list,
        week_pnl: float,
        win_rate: float,
        best_trade: dict,
        worst_trade: dict,
        capital_start: float,
        capital_end: float,
        next_week_events: list,
    ) -> None:
        """Send weekly P&L summary email every Sunday at 18:00 IST."""
        plain, html = format_weekly_summary(
            week_label       = week_label,
            trades           = trades,
            week_pnl         = week_pnl,
            win_rate         = win_rate,
            best_trade       = best_trade,
            worst_trade      = worst_trade,
            capital_start    = capital_start,
            capital_end      = capital_end,
            next_week_events = next_week_events,
        )
        self.send(
            NotificationEvent.WEEKLY_SUMMARY,
            f"Weekly Summary {week_label}",
            plain,
            html=html,
        )

    def send_scout_alert(
        self,
        instrument:     str,
        adx:            float,
        range_pct:      float,
        vwap_crossings: int,
        time_str:       str,
    ) -> None:
        body = format_scout_alert(instrument, adx, range_pct, vwap_crossings, time_str)
        self.send(NotificationEvent.SCOUT_ALERT,
                  f"{instrument} Scout Alert", body, priority="high")

    def send_scout_trade_entry(
        self,
        instrument:     str,
        direction:      str,
        entry_price:    float,
        trail_stop:     float,
        momentum_score: float,
        time_str:       str,
    ) -> None:
        body = format_scout_trade_entry(
            instrument, direction, entry_price, trail_stop, momentum_score, time_str
        )
        self.send(NotificationEvent.SCOUT_TRADE_ENTRY,
                  f"{instrument} Scout Entry", body, priority="high")

    def send_scout_trade_exit(
        self,
        instrument:  str,
        direction:   str,
        entry_price: float,
        exit_price:  float,
        exit_reason: str,
        pnl:         float,
        time_str:    str,
    ) -> None:
        body = format_scout_trade_exit(
            instrument, direction, entry_price, exit_price, exit_reason, pnl, time_str
        )
        self.send(NotificationEvent.SCOUT_TRADE_EXIT,
                  f"{instrument} Scout Exit", body,
                  priority="urgent" if pnl < 0 else "high")

    def _worker(self) -> None:
        """Background thread — delivers notifications from queue."""
        while True:
            try:
                notif = self._q.get(timeout=5)
                if notif is None:
                    break
                self._dispatch(notif)
            except queue.Empty:
                continue
            except Exception:
                pass

    def _dispatch(self, notif: Notification) -> None:
        if self._tg_enabled and self._telegram and notif.event in _TELEGRAM_EVENTS:
            self._telegram.send(notif.body)

        if self._email_enabled and self._email and notif.event in _EMAIL_EVENTS:
            self._email.send(notif.subject, notif.body, notif.html)

        if self._wa_enabled and self._whatsapp and notif.event in _WHATSAPP_EVENTS:
            self._whatsapp.send(notif.body)

    def stop(self) -> None:
        self._q.put(None)

    @property
    def any_enabled(self) -> bool:
        return self._email_enabled or self._tg_enabled or self._wa_enabled
