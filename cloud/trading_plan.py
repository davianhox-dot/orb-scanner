"""Derives a simple ORB/pullback trading plan from a premarket range.
Shared by the Streamlit detail page (and easy to reuse anywhere else)."""


def build_trading_plan(price: float, premarket_high: float, premarket_low: float) -> dict:
    orb_entry = round(premarket_high * 1.001, 2) if premarket_high else price
    pullback_entry = round(premarket_high * 0.985, 2) if premarket_high else price
    stop = round(premarket_low, 2) if premarket_low else round(price * 0.92, 2)
    risk_per_share = max(orb_entry - stop, 0.01)
    target = round(orb_entry + risk_per_share * 2, 2)
    rr = round((target - orb_entry) / risk_per_share, 2) if risk_per_share else 0.0
    return {
        "orb_entry": orb_entry,
        "pullback_entry": pullback_entry,
        "stop": stop,
        "target": target,
        "risk_reward_ratio": rr,
    }
