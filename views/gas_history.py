import streamlit as st

from charts.gas import fig_gas_point_history


def render_history_tab(pivot_gas):
    point_sel = st.selectbox(
        "Hraniční přechod",
        options=[c for c in pivot_gas.columns if pivot_gas[c].abs().sum() > 0],
        key="gas_point_sel",
    )
    st.plotly_chart(
        fig_gas_point_history(pivot_gas, point_sel),
        use_container_width=True,
    )
