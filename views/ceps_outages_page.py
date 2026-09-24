import streamlit as st

from data.entsoe import fetch_installed_capacity
from charts.outages import fig_outages_gantt, fig_installed_capacity


def render_ceps_outages_page(now, df_out, changes, n_pu, n_gu, n_new):
    st.markdown('<div class="section-title">Instalovaná kapacita podle zdroje (14.1.A)</div>',
                unsafe_allow_html=True)
    cap = fetch_installed_capacity()
    if not cap.empty:
        st.plotly_chart(fig_installed_capacity(cap), use_container_width=True,
                        config={"displayModeBar": False})

    st.markdown(f'<div class="section-title">Výrobní jednotky (PU) — {n_pu} aktivních</div>',
                unsafe_allow_html=True)
    st.plotly_chart(fig_outages_gantt(df_out, "PU", now, changes),
                    use_container_width=True, config={"displayModeBar": False})

    st.markdown(f'<div class="section-title">Generační jednotky (GU) — {n_gu} aktivních</div>',
                unsafe_allow_html=True)
    st.plotly_chart(fig_outages_gantt(df_out, "GU", now, changes),
                    use_container_width=True, config={"displayModeBar": False})

    n_ended_tab = len(changes.get("ended", set()))
    n_chmw_tab  = len(changes["changed_mw"])
    with st.expander(f"📋 Detail změn  ·  {n_new} nových · {n_ended_tab} ukončených · {n_chmw_tab} změn MW",
                     expanded=bool(n_new or n_ended_tab or n_chmw_tab)):
        if not (n_new or n_ended_tab or n_chmw_tab):
            st.markdown("<em style='color:#888'>Žádné změny od posledního obnovení.</em>",
                        unsafe_allow_html=True)
        else:
            if n_new and not df_out.empty:
                new_df = (df_out[df_out[["unit_raw","outage_start","outage_end"]]
                                 .apply(tuple, axis=1).isin(changes["new"])]
                          .sort_values("unavailable_MW", ascending=False))
                st.markdown("**🆕 Nové odstávky**")
                st.dataframe(
                    new_df[["unit_name","unit_level","outage_start","outage_end",
                             "installed_MW","unavailable_MW","outage_type"]],
                    use_container_width=True, hide_index=True,
                )
            if not changes["changed_mw"].empty:
                st.markdown("**⚡ Změny výkonu**")
                st.dataframe(changes["changed_mw"], use_container_width=True, hide_index=True)
