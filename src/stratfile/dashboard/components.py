"""Shared dashboard widgets."""

from __future__ import annotations

import pandas as pd
import streamlit as st


def metrics_row(metrics: dict) -> None:
    cols = st.columns(6)
    cols[0].metric("Total return", f"{metrics['total_return_pct']:.2f}%")
    cols[1].metric("Sharpe", f"{metrics['sharpe']:.2f}")
    cols[2].metric("Sortino", f"{metrics['sortino']:.2f}")
    cols[3].metric("Max DD", f"{metrics['max_drawdown_pct']:.2f}%")
    cols[4].metric("Calmar", f"{metrics['calmar']:.2f}")
    cols[5].metric("Trades", metrics["n_trades"])


def equity_chart(result: dict) -> None:
    curve = pd.DataFrame(result["equity_curve"]).set_index("date")
    st.line_chart(curve, y="equity", height=260)


def regime_table(result: dict) -> None:
    st.dataframe(pd.DataFrame(result["regime_breakdown"]), use_container_width=True, hide_index=True)


def walkforward_table(result: dict) -> None:
    st.dataframe(pd.DataFrame(result["walk_forward"]), use_container_width=True, hide_index=True, height=240)


def receipt_panel(receipt: dict, stratfile: dict | None = None) -> None:
    st.subheader("Signed backtest receipt")
    x402 = receipt.get("x402", {})
    c1, c2 = st.columns(2)
    with c1:
        st.code(receipt["stratfile"]["content_hash"], language=None)
        st.caption("content hash — sha256(canonical stratfile, metadata + description excluded)")
    with c2:
        st.code(str(x402.get("payment_hash")), language=None)
        st.caption(f"x402 payment proof ({x402.get('mode', '?')} mode, {x402.get('network', '?')})")
    st.caption(f"signer: `{receipt.get('signer', {}).get('address', '?')}` · signed at {receipt.get('signed_at', '?')}")

    if st.button("Verify receipt", key=f"verify-{receipt['stratfile']['content_hash'][:16]}"):
        from ..x402.proof import verify_receipt

        outcome = verify_receipt(receipt, stratfile)
        for name, check in outcome["checks"].items():
            icon = "\u2705" if check["ok"] else "\u274c"
            st.write(f"{icon} **{name}** — {check.get('note') or check.get('error') or 'ok'}")
        if outcome["ok"]:
            st.success("Receipt VERIFIED — hash, signature, and x402 proof all check out.")
        else:
            st.error("Receipt verification FAILED.")
