"""
Fundamentals — the quantitative half of the Investor Modus.

Fetches annual financials, ticker details, and dividend history from
Polygon, computes the measurable metrics from the user's long-term
checklist (growth, margins, returns, balance-sheet health, cash flow,
valuation multiples), scores them 0-100, and offers a deliberately simple
3-scenario DCF whose assumptions are USER INPUTS, clearly labeled.

Honesty rules baked in:
- Polygon's financials are parsed defensively: field names vary between
  filers, so every metric that can't be computed shows as "—" with the
  reason, never as an invented number.
- Free cash flow uses OCF - CapEx when a capex-like field exists; if it
  doesn't, FCF-based metrics are marked as approximations based on
  operating cash flow and labeled as such.
- The DCF is a sensitivity tool, not an oracle: its output moves 1:1 with
  the growth/discount assumptions the user chooses. The UI says so.
- Everything qualitative (moat, management, business model, SWOT) is OUT
  of scope for computation — the page carries the user's own checklist
  for those instead of pretending a formula can judge them.
"""
import logging
from dataclasses import dataclass, field

import httpx

from cloud.config import Settings

logger = logging.getLogger(__name__)
BASE_URL = "https://api.polygon.io"


# --------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------- #

def fetch_financials(settings: Settings, ticker: str, timeframe: str = "annual", limit: int = 10) -> list[dict]:
    """Raw Polygon financial reports, newest first. Empty list on any failure."""
    if not settings.POLYGON_API_KEY:
        return []
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(
                f"{BASE_URL}/vX/reference/financials",
                params={"ticker": ticker, "timeframe": timeframe, "limit": limit,
                        "order": "desc", "sort": "period_of_report_date",
                        "apiKey": settings.POLYGON_API_KEY},
            )
            if resp.status_code != 200:
                logger.warning("Financials %s -> HTTP %s", ticker, resp.status_code)
                return []
            return resp.json().get("results", []) or []
    except httpx.HTTPError as exc:
        logger.warning("Financials fetch failed for %s: %s", ticker, exc)
        return []


def fetch_ticker_details(settings: Settings, ticker: str) -> dict:
    if not settings.POLYGON_API_KEY:
        return {}
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(f"{BASE_URL}/v3/reference/tickers/{ticker}",
                              params={"apiKey": settings.POLYGON_API_KEY})
            if resp.status_code != 200:
                return {}
            return resp.json().get("results", {}) or {}
    except httpx.HTTPError:
        return {}


def fetch_dividends(settings: Settings, ticker: str, limit: int = 40) -> list[dict]:
    if not settings.POLYGON_API_KEY:
        return []
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(f"{BASE_URL}/v3/reference/dividends",
                              params={"ticker": ticker, "limit": limit, "order": "desc",
                                      "sort": "ex_dividend_date", "apiKey": settings.POLYGON_API_KEY})
            if resp.status_code != 200:
                return []
            return resp.json().get("results", []) or []
    except httpx.HTTPError:
        return []


# --------------------------------------------------------------------- #
# Parsing (defensive: filer vocabularies differ)
# --------------------------------------------------------------------- #

def _val(fin: dict, section: str, *keys: str) -> float | None:
    sec = (fin.get("financials") or {}).get(section) or {}
    for key in keys:
        node = sec.get(key)
        if isinstance(node, dict) and isinstance(node.get("value"), (int, float)):
            return float(node["value"])
    return None


@dataclass
class YearRecord:
    fiscal_year: str
    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    eps: float | None = None
    equity: float | None = None
    assets: float | None = None
    liabilities: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    inventory: float | None = None
    long_term_debt: float | None = None
    interest_expense: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None
    shares: float | None = None

    @property
    def fcf(self) -> float | None:
        if self.operating_cash_flow is None:
            return None
        if self.capex is None:
            return None
        return self.operating_cash_flow - abs(self.capex)


def parse_reports(reports: list[dict]) -> list[YearRecord]:
    """Newest-first raw reports -> oldest-first YearRecords."""
    out: list[YearRecord] = []
    for rep in reports:
        rec = YearRecord(fiscal_year=str(rep.get("fiscal_year") or rep.get("period_of_report_date", ""))[:10])
        rec.revenue = _val(rep, "income_statement", "revenues")
        rec.gross_profit = _val(rep, "income_statement", "gross_profit")
        rec.operating_income = _val(rep, "income_statement", "operating_income_loss")
        rec.net_income = _val(rep, "income_statement", "net_income_loss",
                              "net_income_loss_attributable_to_parent")
        rec.eps = _val(rep, "income_statement", "diluted_earnings_per_share", "basic_earnings_per_share")
        rec.interest_expense = _val(rep, "income_statement", "interest_expense_operating", "interest_expense")
        rec.equity = _val(rep, "balance_sheet", "equity_attributable_to_parent", "equity")
        rec.assets = _val(rep, "balance_sheet", "assets")
        rec.liabilities = _val(rep, "balance_sheet", "liabilities")
        rec.current_assets = _val(rep, "balance_sheet", "current_assets")
        rec.current_liabilities = _val(rep, "balance_sheet", "current_liabilities")
        rec.inventory = _val(rep, "balance_sheet", "inventory")
        rec.long_term_debt = _val(rep, "balance_sheet", "long_term_debt")
        rec.operating_cash_flow = _val(rep, "cash_flow_statement",
                                       "net_cash_flow_from_operating_activities",
                                       "net_cash_flow_from_operating_activities_continuing")
        rec.capex = _val(rep, "cash_flow_statement", "capital_expenditure",
                         "payments_to_acquire_property_plant_and_equipment")
        rec.shares = _val(rep, "income_statement", "diluted_average_shares", "basic_average_shares")
        out.append(rec)
    out.reverse()
    return out


# --------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------- #

def _cagr(first: float | None, last: float | None, years: int) -> float | None:
    if not first or not last or first <= 0 or last <= 0 or years <= 0:
        return None
    return round(((last / first) ** (1 / years) - 1) * 100, 1)


@dataclass
class Metrics:
    years_covered: int = 0
    revenue_cagr: float | None = None
    net_income_cagr: float | None = None
    eps_cagr: float | None = None
    fcf_cagr: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    margin_trend: str = ""  # "steigend" | "stabil" | "fallend" | ""
    roe: float | None = None
    roic_approx: float | None = None
    debt_to_equity: float | None = None
    current_ratio: float | None = None
    quick_ratio: float | None = None
    equity_ratio: float | None = None
    interest_coverage: float | None = None
    fcf_latest: float | None = None
    fcf_is_approx: bool = False  # True when capex missing -> OCF used
    ocf_latest: float | None = None
    net_income_latest: float | None = None
    revenue_latest: float | None = None
    eps_latest: float | None = None
    shares_latest: float | None = None
    notes: list[str] = field(default_factory=list)


def compute_metrics(records: list[YearRecord]) -> Metrics:
    m = Metrics()
    recs = [r for r in records if r.revenue is not None]
    m.years_covered = len(recs)
    if not recs:
        m.notes.append("Keine auswertbaren Finanzberichte gefunden.")
        return m
    first, last = recs[0], recs[-1]
    years = max(len(recs) - 1, 1)

    m.revenue_cagr = _cagr(first.revenue, last.revenue, years)
    m.net_income_cagr = _cagr(first.net_income, last.net_income, years)
    m.eps_cagr = _cagr(first.eps, last.eps, years)

    m.revenue_latest = last.revenue
    m.net_income_latest = last.net_income
    m.eps_latest = last.eps
    m.shares_latest = last.shares

    if last.revenue:
        if last.gross_profit is not None:
            m.gross_margin = round(last.gross_profit / last.revenue * 100, 1)
        if last.operating_income is not None:
            m.operating_margin = round(last.operating_income / last.revenue * 100, 1)
        if last.net_income is not None:
            m.net_margin = round(last.net_income / last.revenue * 100, 1)

    # Margin trend: net margin first vs last year
    if first.revenue and last.revenue and first.net_income is not None and last.net_income is not None:
        nm_first = first.net_income / first.revenue * 100
        nm_last = last.net_income / last.revenue * 100
        if nm_last > nm_first + 1:
            m.margin_trend = "steigend"
        elif nm_last < nm_first - 1:
            m.margin_trend = "fallend"
        else:
            m.margin_trend = "stabil"

    if last.equity and last.net_income is not None and last.equity > 0:
        m.roe = round(last.net_income / last.equity * 100, 1)
    if last.net_income is not None and last.equity and last.equity > 0:
        invested = last.equity + (last.long_term_debt or 0)
        if invested > 0:
            m.roic_approx = round(last.net_income / invested * 100, 1)
            m.notes.append("ROIC ist eine Näherung (Nettogewinn / (Eigenkapital + langfr. Schulden)) — kein exaktes NOPAT.")

    if last.equity and last.equity > 0 and last.liabilities is not None:
        m.debt_to_equity = round(last.liabilities / last.equity, 2)
    if last.current_liabilities and last.current_assets is not None and last.current_liabilities > 0:
        m.current_ratio = round(last.current_assets / last.current_liabilities, 2)
        quick_assets = last.current_assets - (last.inventory or 0)
        m.quick_ratio = round(quick_assets / last.current_liabilities, 2)
    if last.assets and last.equity is not None and last.assets > 0:
        m.equity_ratio = round(last.equity / last.assets * 100, 1)
    if last.interest_expense and last.operating_income is not None and last.interest_expense > 0:
        m.interest_coverage = round(last.operating_income / last.interest_expense, 1)

    m.ocf_latest = last.operating_cash_flow
    if last.fcf is not None:
        m.fcf_latest = last.fcf
        fcf_first = first.fcf
        if fcf_first is not None:
            m.fcf_cagr = _cagr(fcf_first, last.fcf, years)
    elif last.operating_cash_flow is not None:
        m.fcf_latest = last.operating_cash_flow
        m.fcf_is_approx = True
        m.notes.append("CapEx-Feld fehlt im Bericht — FCF-Werte basieren näherungsweise auf dem operativen Cashflow und überzeichnen den echten FCF.")

    return m


# --------------------------------------------------------------------- #
# Valuation multiples + quant score
# --------------------------------------------------------------------- #

@dataclass
class Valuation:
    price: float
    market_cap: float | None = None
    pe: float | None = None
    ps: float | None = None
    p_fcf: float | None = None
    fcf_yield: float | None = None
    earnings_yield: float | None = None


def compute_valuation(m: Metrics, price: float, market_cap: float | None) -> Valuation:
    v = Valuation(price=price, market_cap=market_cap)
    if m.eps_latest and m.eps_latest > 0:
        v.pe = round(price / m.eps_latest, 1)
        v.earnings_yield = round(m.eps_latest / price * 100, 2)
    mc = market_cap
    if mc is None and m.shares_latest:
        mc = price * m.shares_latest
        v.market_cap = mc
    if mc:
        if m.revenue_latest:
            v.ps = round(mc / m.revenue_latest, 2)
        if m.fcf_latest and m.fcf_latest > 0:
            v.p_fcf = round(mc / m.fcf_latest, 1)
            v.fcf_yield = round(m.fcf_latest / mc * 100, 2)
    return v


@dataclass
class QuantScore:
    total: float  # 0-100
    components: list[tuple[str, float, float, str]] = field(default_factory=list)  # (name, points, max, reason)


def score_quant(m: Metrics, v: Valuation) -> QuantScore:
    """Deliberately transparent scoring of ONLY the measurable half.
    Six buckets — the qualitative half (moat, management, business model)
    is not scored because it is not computable."""
    comps: list[tuple[str, float, float, str]] = []

    def bucket(name: str, pts: float, mx: float, reason: str) -> None:
        comps.append((name, round(pts, 1), mx, reason))

    # Growth (20)
    g = 0.0
    reasons = []
    if m.revenue_cagr is not None:
        g += 10 if m.revenue_cagr >= 10 else 6 if m.revenue_cagr >= 5 else 2 if m.revenue_cagr >= 0 else 0
        reasons.append(f"Umsatz-CAGR {m.revenue_cagr}%")
    if m.eps_cagr is not None:
        g += 10 if m.eps_cagr >= 12 else 6 if m.eps_cagr >= 6 else 2 if m.eps_cagr >= 0 else 0
        reasons.append(f"EPS-CAGR {m.eps_cagr}%")
    elif m.net_income_cagr is not None:
        g += 10 if m.net_income_cagr >= 12 else 6 if m.net_income_cagr >= 6 else 2 if m.net_income_cagr >= 0 else 0
        reasons.append(f"Gewinn-CAGR {m.net_income_cagr}%")
    bucket("Wachstum", g, 20, ", ".join(reasons) if reasons else "nicht messbar")

    # Profitability (20)
    p = 0.0
    reasons = []
    if m.net_margin is not None:
        p += 8 if m.net_margin >= 15 else 5 if m.net_margin >= 8 else 2 if m.net_margin >= 3 else 0
        reasons.append(f"Nettomarge {m.net_margin}%")
    if m.roe is not None:
        p += 8 if m.roe >= 18 else 5 if m.roe >= 12 else 2 if m.roe >= 8 else 0
        reasons.append(f"ROE {m.roe}%")
    if m.margin_trend == "steigend":
        p += 4
        reasons.append("Margen steigend")
    elif m.margin_trend == "stabil":
        p += 2
    bucket("Profitabilität", p, 20, ", ".join(reasons) if reasons else "nicht messbar")

    # Balance sheet (15)
    b = 0.0
    reasons = []
    if m.debt_to_equity is not None:
        b += 7 if m.debt_to_equity <= 1.0 else 4 if m.debt_to_equity <= 2.0 else 0
        reasons.append(f"Debt/Equity {m.debt_to_equity}")
    if m.current_ratio is not None:
        b += 4 if m.current_ratio >= 1.5 else 2 if m.current_ratio >= 1.0 else 0
        reasons.append(f"Current Ratio {m.current_ratio}")
    if m.interest_coverage is not None:
        b += 4 if m.interest_coverage >= 8 else 2 if m.interest_coverage >= 3 else 0
        reasons.append(f"Zinsdeckung {m.interest_coverage}x")
    elif m.debt_to_equity is not None and m.debt_to_equity <= 0.5:
        b += 2
    bucket("Bilanz", b, 15, ", ".join(reasons) if reasons else "nicht messbar")

    # Cash flow (15)
    c = 0.0
    reasons = []
    if m.fcf_latest is not None and m.fcf_latest > 0:
        c += 7 if not m.fcf_is_approx else 4
        reasons.append("FCF positiv" + (" (Näherung über OCF)" if m.fcf_is_approx else ""))
        if m.net_income_latest and m.fcf_latest >= m.net_income_latest * 0.8:
            c += 4
            reasons.append("FCF ≥ 80% des Gewinns (hohe Gewinnqualität)")
    if m.fcf_cagr is not None and m.fcf_cagr > 0:
        c += 4
        reasons.append(f"FCF-CAGR {m.fcf_cagr}%")
    bucket("Cashflow", c, 15, ", ".join(reasons) if reasons else "nicht messbar")

    # Valuation (20)
    val = 0.0
    reasons = []
    if v.fcf_yield is not None:
        val += 10 if v.fcf_yield >= 5 else 6 if v.fcf_yield >= 3 else 2 if v.fcf_yield >= 1.5 else 0
        reasons.append(f"FCF-Rendite {v.fcf_yield}%")
    if v.pe is not None:
        val += 10 if v.pe <= 15 else 6 if v.pe <= 25 else 2 if v.pe <= 40 else 0
        reasons.append(f"KGV {v.pe}")
    elif v.earnings_yield is None:
        reasons.append("KGV nicht berechenbar (negativer/fehlender Gewinn)")
    bucket("Bewertung", val, 20, ", ".join(reasons) if reasons else "nicht messbar")

    # Data quality (10) — rewards long history, punishes thin data
    d = 10.0 if m.years_covered >= 8 else 6.0 if m.years_covered >= 5 else 3.0 if m.years_covered >= 3 else 0.0
    bucket("Datenbasis", d, 10, f"{m.years_covered} Jahresberichte auswertbar")

    total = round(sum(pts for _, pts, _, _ in comps), 1)
    return QuantScore(total=total, components=comps)


# --------------------------------------------------------------------- #
# Simplified DCF (assumptions are user inputs, clearly labeled in the UI)
# --------------------------------------------------------------------- #

def dcf_fair_value(
    fcf_base: float, shares: float,
    growth_pct: float, years: int = 10,
    discount_pct: float = 10.0, terminal_growth_pct: float = 2.5,
) -> float | None:
    """Two-stage DCF: `years` of constant growth, then Gordon terminal
    value. Returns fair value PER SHARE, or None when inputs are unusable
    (r must exceed terminal growth; fcf and shares must be positive)."""
    if fcf_base <= 0 or shares <= 0 or discount_pct <= terminal_growth_pct:
        return None
    g, r, gt = growth_pct / 100, discount_pct / 100, terminal_growth_pct / 100
    pv = 0.0
    fcf = fcf_base
    for t in range(1, years + 1):
        fcf *= (1 + g)
        pv += fcf / (1 + r) ** t
    terminal = fcf * (1 + gt) / (r - gt)
    pv += terminal / (1 + r) ** years
    return round(pv / shares, 2)
