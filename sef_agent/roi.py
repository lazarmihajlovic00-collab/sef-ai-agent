"""Business-case calculator. Every number is an explicit, editable assumption."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class RoiInputs:
    invoices_per_month: int = 400
    manual_minutes_per_invoice: float = 12.0      # open, check vs PO/receipt, assign account, enter, accept in SEF
    review_minutes_clean: float = 2.0             # person reviews a READY proposal and approves
    review_minutes_exception: float = 10.0        # person resolves an exception with the agent's explanation
    accountant_cost_rsd_per_hour: float = 1250.0  # employer cost, assumption for Serbia 2026
    usd_rsd: float = 108.0                        # exchange-rate assumption
    hosting_rsd_per_month: float = 3000.0         # small VPS / Render instance


def _rsd(x: float) -> str:
    return f"{x:,.0f} RSD".replace(",", ".")


def compute_roi(inp: RoiInputs, auto_share: float, llm_cost_per_invoice_usd: float) -> dict:
    """auto_share: fraction of invoices that come out READY (no blocking finding)."""
    n = inp.invoices_per_month
    manual_h = n * inp.manual_minutes_per_invoice / 60
    agent_h = n * (auto_share * inp.review_minutes_clean + (1 - auto_share) * inp.review_minutes_exception) / 60
    saved_h = manual_h - agent_h
    saved_rsd = saved_h * inp.accountant_cost_rsd_per_hour
    run_rsd = n * llm_cost_per_invoice_usd * inp.usd_rsd + inp.hosting_rsd_per_month
    net = saved_rsd - run_rsd
    table = [
        ("Invoices per month (assumption)", str(n)),
        ("Share that arrive READY for one-click approval (measured)", f"{100 * auto_share:.0f}%"),
        ("Manual processing time", f"{manual_h:.0f} h"),
        ("Time with the agent (review + exceptions)", f"{agent_h:.0f} h"),
        ("Hours saved", f"{saved_h:.0f} h"),
        ("Value of hours saved", _rsd(saved_rsd)),
        ("LLM cost (measured per invoice) + hosting", _rsd(run_rsd)),
        ("Net monthly saving", _rsd(net)),
    ]
    return {"inputs": asdict(inp), "auto_share": auto_share, "hours_saved": saved_h, "net_rsd": net, "table": table}
