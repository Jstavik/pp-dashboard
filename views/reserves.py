import pandas as pd
import streamlit as st

from config import RESERVES_DEFAULT_RANGE_DAYS
from charts.reserves import fig_reserve_volumes, fig_reserve_prices


def render_reserves_tab(now, reserves):
    res_start = now.normalize()
    res_end   = now.normalize() + pd.Timedelta(days=RESERVES_DEFAULT_RANGE_DAYS)
    if now.month < 7:
        _a04_label = f"{now.year}-01-01 – {now.year}-07-01"
    else:
        _a04_label = f"{now.year}-07-01 – {now.year + 1}-01-01"

    st.markdown(
        '<div class="section-title">'
        f'aFRR + mFRR — D0 až D+7 &nbsp;·&nbsp; '
        f'Solid = A01 denní &nbsp;·&nbsp; Dash = A04 roční ({_a04_label})'
        '</div>',
        unsafe_allow_html=True,
    )
    st.plotly_chart(
        fig_reserve_volumes(reserves, now, res_start, res_end, height=420),
        use_container_width=True, config={"displayModeBar": False},
    )
    st.plotly_chart(
        fig_reserve_prices(reserves, now, res_start, res_end, height=420),
        use_container_width=True, config={"displayModeBar": False},
    )

    with st.expander("📥 Stáhnout surová data rezerv"):
        ec1, ec2, ec3, ec4, ec5, ec6 = st.columns(6)
        for col_obj, df_r, label, fname in [
            (ec1, reserves["afrr_d_amt"], "aFRR denní obj.", "afrr_d_amount.csv"),
            (ec2, reserves["afrr_d_pri"], "aFRR denní ceny", "afrr_d_price.csv"),
            (ec3, reserves["afrr_y_amt"], "aFRR roční obj.", "afrr_y_amount.csv"),
            (ec4, reserves["afrr_y_pri"], "aFRR roční ceny", "afrr_y_price.csv"),
            (ec5, reserves["mfrr_d_amt"], "mFRR denní obj.", "mfrr_d_amount.csv"),
            (ec6, reserves["mfrr_d_pri"], "mFRR denní ceny", "mfrr_d_price.csv"),
        ]:
            with col_obj:
                if not df_r.empty:
                    st.download_button(f"⬇ {label}", df_r.to_csv().encode(), fname, "text/csv")
                else:
                    st.caption(f"{label}: —")
