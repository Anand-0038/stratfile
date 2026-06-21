"""Stratfile dashboard (Streamlit, :8501). Pages from Build Spec §9."""

from __future__ import annotations

import json

import httpx
import pandas as pd
import streamlit as st

from stratfile import __version__, config
from stratfile.dashboard import components

st.set_page_config(page_title="Stratfile", page_icon="\U0001f4c4", layout="wide")

EXECUTOR = config.EXECUTOR_URL


def api(method: str, path: str, **kwargs):
    try:
        resp = httpx.request(method, f"{EXECUTOR}{path}", timeout=30, **kwargs)
        if resp.status_code >= 400:
            st.error(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
            return None
        return resp.json()
    except httpx.HTTPError as exc:
        st.error(f"Executor unreachable at {EXECUTOR}: {exc}")
        return None


st.sidebar.title("\U0001f4c4 Stratfile")
st.sidebar.caption(f"v{__version__} · BSC Testnet · ERC-8004")
page = st.sidebar.radio(
    "Pages",
    ["Home", "Strategies", "Strategy detail", "Executor", "Backtest playground"],
    label_visibility="collapsed",
)

health = api("GET", "/health")
if health:
    st.sidebar.success(f"executor: {health['status']} · {health['network']}")


# --- Home ----------------------------------------------------------------
if page == "Home":
    st.title("Define your strategy in a Stratfile. Backtest it. Run it on-chain.")
    st.markdown(
        """
**Stratfile** is a `Dockerfile`-like open format for trading strategies. One JSON artifact, two consumers:

1. **Authors** — `stratfile init "DCA into BNB when Fear & Greed < 30"` emits a deterministic,
   schema-validated `stratfile.json` plus a **signed backtest receipt** (content hash + x402 payment proof).
2. **Agents** — this executor loads the same file, holds an **ERC-8004 identity** on BSC Testnet,
   and fires verifiable swaps on PancakeSwap V3 Testnet, gas-free via MegaFuel.

*What Hummingbot YAML configs would look like if they were portable, signed, and on-chain-consumable.*
        """
    )
    st.divider()
    st.subheader("Run the 90-second proof loop")
    if st.button("\u25b6 Run demo", type="primary"):
        strategies = api("GET", "/strategies") or []
        if not strategies:
            st.warning("No strategies loaded on the executor yet.")
        else:
            sid = strategies[0]["strategy_id"]
            with st.status("Running proof loop...", expanded=True) as status:
                st.write(f"1\ufe0f\u20e3 strategy loaded: `{strategies[0]['name']}` ({strategies[0]['content_hash'][:23]}...)")
                ev = api("POST", f"/strategies/{sid}/evaluate")
                if ev:
                    st.write(f"2\ufe0f\u20e3 current regime: **{ev['current_regime']}** -> {ev['recommended_action']}")
                rec = api("GET", f"/receipts/{sid}")
                if rec:
                    st.write(f"3\ufe0f\u20e3 receipt: `{rec['x402']['payment_hash']}` ({rec['x402']['mode']})")
                ex = api("POST", f"/strategies/{sid}/execute", json={"dry_run": True})
                if ex:
                    st.write(f"4\ufe0f\u20e3 executed (dry-run): `{ex['tx_hash']}`")
                ident = api("GET", "/identity")
                if ident:
                    st.write(f"5\ufe0f\u20e3 ERC-8004 identity: token #{ident['token_id']} @ `{ident['address']}`")
                status.update(label="Proof loop complete", state="complete")


# --- Strategies -----------------------------------------------------------
elif page == "Strategies":
    st.title("Loaded strategies")
    strategies = api("GET", "/strategies") or []
    if strategies:
        st.dataframe(pd.DataFrame(strategies), use_container_width=True, hide_index=True)
        st.caption("Open the Strategy detail page to inspect regimes, rules, backtest, and receipt.")
    else:
        st.info("No strategies loaded. Upload one on the Backtest playground page or via POST /strategies.")


# --- Strategy detail --------------------------------------------------------
elif page == "Strategy detail":
    st.title("Strategy detail")
    strategies = api("GET", "/strategies") or []
    if not strategies:
        st.info("No strategies loaded yet.")
    else:
        options = {f"{s['name']} ({s['strategy_id']})": s["strategy_id"] for s in strategies}
        choice = st.selectbox("Strategy", list(options))
        sid = options[choice]
        detail = api("GET", f"/strategies/{sid}")
        if detail:
            doc = detail["stratfile"]
            st.caption(doc["description"])
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Regimes")
                st.dataframe(pd.DataFrame(doc["regimes"]), use_container_width=True, hide_index=True)
            with c2:
                st.subheader("Rules")
                st.dataframe(pd.DataFrame(doc["rules"]), use_container_width=True, hide_index=True)

            with st.expander("Raw stratfile.json"):
                st.json(doc)

            if detail.get("receipt"):
                rec = detail["receipt"]
                bt = rec["backtest"]
                st.subheader("Walk-forward backtest (from receipt)")
                components.metrics_row(bt["metrics"])
                st.dataframe(pd.DataFrame(bt["regime_breakdown"]), use_container_width=True, hide_index=True)
                components.receipt_panel(rec, doc)
            else:
                st.info("No receipt attached. Run a backtest on the playground to produce one.")

            st.subheader("Run a fresh walk-forward backtest")
            if st.button("Backtest now"):
                from stratfile.backtest import engine

                with st.spinner("Running 34-window walk-forward..."):
                    result = engine.run(doc)
                components.metrics_row(result["metrics"])
                components.equity_chart(result)
                components.regime_table(result)
                components.walkforward_table(result)


# --- Executor ----------------------------------------------------------------
elif page == "Executor":
    st.title("On-chain executor")
    ident = api("GET", "/identity")
    if ident:
        st.subheader("ERC-8004 identity card")
        c1, c2, c3 = st.columns(3)
        c1.metric("Token ID", f"#{ident['token_id']}")
        c2.code(ident["address"], language=None)
        c3.metric("Mode", "DRY-RUN" if ident["dry_run"] else "ON-CHAIN")
        if ident.get("bscscan_url"):
            st.markdown(f"[Registration tx on BscScan Testnet]({ident['bscscan_url']})")
        else:
            st.caption(f"registration ref: `{ident['tx_hash']}` (dry-run identity, no on-chain tx)")

    st.subheader("Execute now")
    strategies = api("GET", "/strategies") or []
    if strategies:
        options = {f"{s['name']} ({s['strategy_id']})": s["strategy_id"] for s in strategies}
        choice = st.selectbox("Strategy", list(options))
        dry = st.toggle("Dry run", value=True, help="Disable only with a faucet-funded testnet wallet")
        if st.button("\u26a1 Execute", type="primary"):
            result = api("POST", f"/strategies/{options[choice]}/execute", json={"dry_run": dry})
            if result:
                st.json(result)
                if result.get("bscscan_url"):
                    st.markdown(f"**[View tx on BscScan Testnet]({result['bscscan_url']})**")

    st.subheader("Last runs")
    runs = api("GET", "/runs") or []
    if runs:
        st.dataframe(pd.DataFrame(runs[:10]), use_container_width=True, hide_index=True)
    else:
        st.caption("No runs recorded yet.")


# --- Backtest playground -------------------------------------------------------
elif page == "Backtest playground":
    st.title("Backtest playground")
    st.caption("Upload any stratfile.json -> validate -> walk-forward backtest -> signed receipt.")
    uploaded = st.file_uploader("stratfile.json", type=["json"])
    windows = st.slider("Walk-forward windows", 4, 50, 34)
    if uploaded is not None:
        try:
            doc = json.loads(uploaded.read())
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")
            st.stop()

        from stratfile.generator import validator

        problems = validator.validate(doc)
        if problems:
            st.error("Stratfile is INVALID:")
            for p in problems:
                st.write(f"- {p}")
            st.stop()
        st.success(f"\u2713 valid stratfile: {doc['name']}")

        if st.button("Run walk-forward backtest", type="primary"):
            from stratfile.backtest import engine, receipt as receipt_mod

            with st.spinner("Backtesting..."):
                result = engine.run(doc, windows=windows)
            components.metrics_row(result["metrics"])
            components.equity_chart(result)
            components.regime_table(result)
            components.walkforward_table(result)
            rec = receipt_mod.build_receipt(doc, result)
            components.receipt_panel(rec, doc)
            st.download_button(
                "Download receipt.json",
                json.dumps(rec, indent=2),
                file_name=f"{doc['name']}.receipt.json",
                mime="application/json",
            )
