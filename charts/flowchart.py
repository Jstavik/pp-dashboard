"""CEE flowchart plynu jako SVG (renderuje server, ostré v každém rozlišení).

Schéma (ne mapa) ve stylu „Daily CEE flowchart“: země = boxy se zásobníky
a bilancí, šipky = čistý fyzický tok přes hranici (tloušťka podle objemu,
barva podle změny proti srovnávacímu dni), LNG terminály jako přítoky,
⚠ = nesrovnalost exit/entry na hranici. Data: core/gasflow.flowchart().

Barvy jsou v <style> uvnitř SVG (světlý vzhled) — export SVG/PNG je tak
vždy světlý; tmavý režim na stránce přebíjí web/static/app.css (.gf …).
Tooltip: atribut data-tip (app.js ho ukáže na hover/klepnutí) + <title>.
"""
import math
from html import escape

from config import storage_color
from core.gasflow import BOX_H, BOX_W, EXT_H, EXT_W, LNG_POS, NO_BAR, NO_DROP_X, fmt_num

W, H = 1200, 1060
COL_UP, COL_DOWN, COL_FLAT, COL_ZERO = "#2E7D32", "#C62828", "#607D8B", "#B0BEC5"
COL_LNG = "#00838F"
UNIT_LABEL = {"gwh": "GWh/d", "mcm": "mcm/d"}
# hrany, které by vedly přes cizí box → oblouk (kladné = doleva po směru a→b)
CURVES = {("DE", "FR"): 0.12, ("BE", "DE"): 0.08, ("NL", "UK"): 0.15}

STYLE = """
.gf text{font-family:system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
.gf-bg{fill:#ffffff}
.gf-box{fill:#ffffff;stroke:#c5d0dc;stroke-width:1.2}
.gf-ext{fill:#f3f6f9;stroke:#c5d0dc;stroke-width:1}
.gf-head{fill:#eef2f7}
.gf-t{fill:#1d2733}
.gf-m{fill:#5f7183}
.gf-lbl{fill:#ffffff;stroke:#d5dde6;stroke-width:.8}
.gf-bar{fill:#e6ebf1}
.gf-warn{fill:#F9A825;stroke:#5d4037;stroke-width:.8}
.gf-hit{stroke:transparent;fill:none}
.gf [data-tip]{cursor:pointer}
"""


def _box_point(cx, cy, hw, hh, tx, ty, gap=4):
    """Bod na okraji obdélníku (střed cx, cy) ve směru k (tx, ty)."""
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    t = min(hw / abs(dx) if dx else math.inf, hh / abs(dy) if dy else math.inf)
    L = math.hypot(dx, dy)
    return cx + dx * t + dx / L * gap, cy + dy * t + dy / L * gap


def _size(n):
    if n["kind"] == "bar":
        return (NO_BAR[1] - NO_BAR[0]) / 2, (NO_BAR[3] - NO_BAR[2]) / 2
    return (BOX_W / 2, BOX_H / 2) if n["kind"] == "main" else (EXT_W / 2, EXT_H / 2)


def _color(change, value):
    if value < 0.5:
        return COL_ZERO
    if change is None or abs(change) < max(2.0, 0.03 * value):
        return COL_FLAT
    return COL_UP if change > 0 else COL_DOWN


def _width(v, vmax):
    return 1.6 + 12.0 * math.sqrt(min(1.0, v / vmax)) if vmax > 0 else 1.6


def _t(x, y, s, cls="gf-t", size=13, weight=None, anchor="start", extra=""):
    w = f' font-weight="{weight}"' if weight else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" font-size="{size}"{w} text-anchor="{anchor}"{extra}>'
            f'{escape(str(s))}</text>')


def _pct(v):
    return "—" if v is None or v != v else f"{v:.1f}".replace(".", ",")


def _signed(v, unit):
    if v is None or v != v:
        return "—"
    s = fmt_num(abs(v), unit)
    return ("+" if v > 0 else "−" if v < 0 else "±") + s


def _arrowhead(x1, y1, x2, y2, w, color):
    """Trojúhelník na konci (x2, y2) — vlastní polygon místo <marker> kvůli exportu."""
    ang = math.atan2(y2 - y1, x2 - x1)
    L = 7 + w * 1.3
    hw = 4 + w * 0.9
    bx, by = x2 - L * math.cos(ang), y2 - L * math.sin(ang)
    px, py = -math.sin(ang), math.cos(ang)
    pts = [(x2, y2), (bx + px * hw, by + py * hw), (bx - px * hw, by - py * hw)]
    return (f'<polygon points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}" fill="{color}"/>',
            (bx, by))


def _bar_geom(bar, other, outgoing):
    """Hrana z/do pruhu Norska: svisle pod pruhem, nebo vodorovně vedle něj."""
    x0, x1, y0, y1 = NO_BAR
    hw, hh = _size(other)
    if other["x"] - hw > x1:  # vedle pruhu (Dánsko)
        p, q = (x1 + 3, other["y"]), (other["x"] - hw - 4, other["y"])
    else:
        x = NO_DROP_X.get(other["code"], other["x"])
        p, q = (x, y1 + 3), (x, other["y"] - hh - 4)
    s, e = (p, q) if outgoing else (q, p)
    return s, e, ((s[0] + e[0]) / 2, (s[1] + e[1]) / 2)


def _edge_geom(a, b, curve):
    """Start, konec a kontrolní bod (oblouk) mezi boxy a, b."""
    if a["kind"] == "bar":
        return _bar_geom(a, b, True)
    if b["kind"] == "bar":
        return _bar_geom(b, a, False)
    (ax, ay), (bx, by) = (a["x"], a["y"]), (b["x"], b["y"])
    mx, my = (ax + bx) / 2, (ay + by) / 2
    L = math.hypot(bx - ax, by - ay) or 1
    cx, cy = mx + -(by - ay) / L * curve * L, my + (bx - ax) / L * curve * L
    s = _box_point(ax, ay, *_size(a), cx, cy)
    e = _box_point(bx, by, *_size(b), cx, cy)
    return s, e, (cx, cy)


def _qpoint(s, c, e, t):
    return ((1 - t) ** 2 * s[0] + 2 * (1 - t) * t * c[0] + t ** 2 * e[0],
            (1 - t) ** 2 * s[1] + 2 * (1 - t) * t * c[1] + t ** 2 * e[1])


def _overlap(r1, r2):
    return r1[0] < r2[2] and r2[0] < r1[2] and r1[1] < r2[3] and r2[1] < r1[3]


def _label_pos(pts, tw, nodes, taken):
    """Bod na hraně (kvadratická křivka s, c, e) pro popisek šířky tw, který
    nepřekrývá box uzlu ani dřív umístěný popisek; jinak střed hrany."""
    s, c, e = pts
    boxes = []
    for n in nodes.values():
        hw, hh = _size(n)
        boxes.append((n["x"] - hw - 2, n["y"] - hh - 2, n["x"] + hw + 2, n["y"] + hh + 2))
    for t in (0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74, 0.2, 0.8):
        x, y = _qpoint(s, c, e, t)
        r = (x - tw / 2, y - 10, x + tw / 2, y + 10)
        if not any(_overlap(r, b) for b in boxes) and not any(_overlap(r, b) for b in taken):
            return x, y
    return _qpoint(s, c, e, 0.5)


def _edge_tip(e, fc, unit):
    u = UNIT_LABEL[unit]
    cmp_lbl = "předchozí týden" if fc["compare"] == "w" else "předchozí den"
    lines = [f"{e['from']} → {e['to']}: {fmt_num(e['value'], unit)} {u} (čistý fyzický tok)",
             f"{e['a']}→{e['b']} {fmt_num(e['ab'], unit)} · {e['b']}→{e['a']} {fmt_num(e['ba'], unit)} {u}"]
    if e["prev"] is not None:
        lines.append(f"{cmp_lbl} ({fc['cmp_day']:%d.%m.}): {fmt_num(e['prev'], unit)} {u}, "
                     f"změna {_signed(e['change'], unit)}")
    for c in e["checks"]:
        lines.append(f"⚠ exit {c['from']} {fmt_num(c['exit'], unit)} vs. entry {c['to']} {fmt_num(c['entry'], unit)} "
                     f"{u} — rozdíl {_signed(c['diff'], unit)} ({c['rel']:.0f} %)")
    return "\n".join(lines)


def _node_tip(n, fc, unit):
    u = UNIT_LABEL[unit]
    lines = [f"{n['name']} ({n['code']}) — {fc['day']:%d.%m.%Y}"]
    st, sp, b = n["storage"], n["storage_prev"], n["balance"]
    if st:
        lines.append(f"Zásobníky: {_pct(st['full'])} % · {_pct(st['twh'])} TWh "
                     f"(vtláčení {fmt_num(st['inj'], unit)}, těžba {fmt_num(st['wd'], unit)} {u}; GIE {st['day']:%d.%m.})")
        if sp and sp.get("full") == sp.get("full") and st.get("full") == st.get("full"):
            lines.append(f"  změna plnění od {fc['cmp_day']:%d.%m.}: {st['full'] - sp['full']:+.2f} p. b.".replace(".", ","))
    if b:
        lines += [f"Vstupy z hranic {fmt_num(b['in'], unit)} · výstupy {fmt_num(b['out'], unit)} {u}",
                  f"LNG {fmt_num(b['lng'], unit)} · domácí těžba {fmt_num(b['prod'], unit)} · "
                  f"zásobníky (těžba − vtláčení, {b['stor_src']}) {_signed(b['stor_wd'], unit)} {u}",
                  f"Odvozená spotřeba (vstupy − výstupy + LNG + těžba + zásobníky): {fmt_num(b['implied'], unit)} {u}"]
        if b.get("reported"):
            lines.append(f"Hlášeno ENTSO-G (distribuce + koneční zákazníci): {fmt_num(b['reported'], unit)} {u}")
    elif n["kind"] == "main":
        lines.append("Země nehlásí toky do ENTSO-G — toky z pohledu sousedů.")
    return "\n".join(lines)


def render_svg(fc: dict, unit: str = "gwh") -> str:
    """SVG string z výsledku core.gasflow.flowchart()."""
    nodes, u = fc["nodes"], UNIT_LABEL.get(unit, "GWh/d")
    unit = unit if unit in UNIT_LABEL else "gwh"
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" class="gf" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'role="img" aria-label="Toky plynu ve střední Evropě {fc["day"]:%d.%m.%Y}">',
           f"<style>{STYLE}</style>", f'<rect class="gf-bg" x="0" y="0" width="{W}" height="{H}"/>']
    cmp_lbl = "předchozím týdnem" if fc["compare"] == "w" else "předchozím dnem"
    out.append(_t(20, 32, f"Toky plynu CEE — {fc['day']:%d.%m.%Y}", size=20, weight=700))
    out.append(_t(20, 52, f"čistý fyzický tok přes hranice (ENTSO-G), {u}; barva = změna proti {cmp_lbl} "
                          f"({fc['cmp_day']:%d.%m.})", cls="gf-m", size=12))
    vmax = max([e["value"] for e in fc["edges"]] + [l["value"] for l in fc["lng"].values()] + [1.0])
    vmax = max(vmax, 600.0)
    labels = []
    # hrany s nulovým tokem k vnějším uzlům se nekreslí (ani uzel bez hran)
    edges = [e for e in fc["edges"] if e["value"] >= 0.5 or (nodes[e["from"]]["kind"] == "main"
                                                              and nodes[e["to"]]["kind"] == "main")]
    used = {c for e in edges if e["value"] >= 0.5 for c in (e["from"], e["to"])}
    # ── hrany ──
    for e in sorted(edges, key=lambda e: e["value"]):
        a, b = nodes[e["from"]], nodes[e["to"]]
        key = (e["from"], e["to"]) if (e["from"], e["to"]) in CURVES else (e["to"], e["from"])
        curve = CURVES.get(key, 0.0) * (1 if key == (e["from"], e["to"]) else -1)
        s, t, c = _edge_geom(a, b, curve)
        w = _width(e["value"], vmax)
        col = _color(e["change"], e["value"])
        head, base = _arrowhead(c[0] if curve else s[0], c[1] if curve else s[1], t[0], t[1], w, col)
        if curve:
            path = f'M{s[0]:.1f},{s[1]:.1f} Q{c[0]:.1f},{c[1]:.1f} {base[0]:.1f},{base[1]:.1f}'
        else:
            path = f'M{s[0]:.1f},{s[1]:.1f} L{base[0]:.1f},{base[1]:.1f}'
        dash = ' stroke-dasharray="4 4"' if e["value"] < 0.5 else ""
        tip = _edge_tip(e, fc, unit)
        out.append(f'<g class="gf-edge" data-tip="{escape(tip)}"><title>{escape(tip)}</title>'
                   f'<path d="{path}" class="gf-hit" stroke-width="{w + 14:.1f}"/>'
                   f'<path d="{path}" fill="none" stroke="{col}" stroke-width="{w:.1f}" stroke-linecap="butt"{dash}/>'
                   f'{head}</g>')
        labels.append(((s, c if curve else ((s[0] + base[0]) / 2, (s[1] + base[1]) / 2), base), e, col, tip))
    # ── LNG přítoky (štítek „LNG hodnota“ + krátká šipka do země) ──
    for cc, l in fc["lng"].items():
        if cc not in nodes:
            continue
        n = nodes[cc]
        lx, ly = LNG_POS[cc]
        txt = f"LNG {fmt_num(l['value'], unit)}"
        pw = 22 + 7.4 * len(txt)
        s = _box_point(lx, ly, pw / 2, 13, n["x"], n["y"], gap=2)
        t = _box_point(n["x"], n["y"], *_size(n), lx, ly)
        w = _width(max(l["value"], 0), vmax)
        col = _color(l["change"], l["value"])
        head, base = _arrowhead(s[0], s[1], t[0], t[1], w, col)
        alsi = l.get("alsi") or {}
        tip = (f"LNG → {cc}: {fmt_num(l['value'], unit)} {u} (ENTSO-G entry z LNG terminálů)"
               + (f"\nzměna proti {fc['cmp_day']:%d.%m.}: {_signed(l['change'], unit)} {u}" if l["change"] is not None else "")
               + (f"\nALSI send-out {fmt_num(alsi.get('sendout'), unit)} {u}, zásoba LNG {_pct(alsi.get('inv_twh'))} TWh"
                  f" ({_pct(alsi.get('full'))} %)" if alsi else ""))
        white = ' fill="#fff"'
        out.append(f'<g class="gf-edge" data-tip="{escape(tip)}"><title>{escape(tip)}</title>'
                   f'<line x1="{s[0]:.1f}" y1="{s[1]:.1f}" x2="{base[0]:.1f}" y2="{base[1]:.1f}" stroke="{col}" '
                   f'stroke-width="{w:.1f}"/>{head}'
                   f'<rect x="{lx - pw / 2:.1f}" y="{ly - 13}" width="{pw:.1f}" height="26" rx="13" fill="{COL_LNG}"/>'
                   f'{_t(lx, ly + 4.5, txt, cls="", size=12, weight=700, anchor="middle", extra=white)}</g>')
    # ── uzly ──
    for code, n in nodes.items():
        tip = escape(_node_tip(n, fc, unit))
        x, y = n["x"], n["y"]
        if n["kind"] == "bar":
            x0, x1, y0, y1 = NO_BAR
            out.append(f'<g data-tip="{tip}"><title>{tip}</title>'
                       f'<rect class="gf-ext" x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="14"/>'
                       f'{_t(x0 + 16, y0 + 19, "Norsko — přítoky do Evropy (plynovody ze severního moře)", size=13, weight=700)}</g>')
            continue
        if n["kind"] == "ext":
            if code not in used:
                continue
            bold = code == "NO"
            out.append(f'<g data-tip="{tip}"><title>{tip}</title>'
                       f'<rect class="gf-ext" x="{x - EXT_W / 2}" y="{y - EXT_H / 2}" width="{EXT_W}" height="{EXT_H}" rx="17"/>'
                       f'{_t(x, y + 4.5, n["name"], cls="gf-t" if bold else "gf-m", size=13 if bold else 11.5, weight=700 if bold else 600, anchor="middle")}</g>')
            continue
        x0, y0 = x - BOX_W / 2, y - BOX_H / 2
        st, b = n["storage"], n["balance"]
        g = [f'<g data-tip="{tip}"><title>{tip}</title>',
             f'<rect class="gf-box" x="{x0}" y="{y0}" width="{BOX_W}" height="{BOX_H}" rx="8"/>',
             f'<path class="gf-head" d="M{x0},{y0 + 8} a8,8 0 0 1 8,-8 h{BOX_W - 16} a8,8 0 0 1 8,8 v18 h-{BOX_W} z"/>',
             _t(x0 + 10, y0 + 19, code, size=14, weight=800),
             _t(x0 + 38, y0 + 19, n["name"], cls="gf-m", size=12)]
        yy = y0 + 44
        if st and st.get("full") == st.get("full"):
            col = storage_color(float(st["full"]))
            bw = BOX_W - 20
            g.append(_t(x0 + 10, yy, f"{_pct(st['full'])} %", size=13, weight=800, extra=f' fill="{col}"'))
            g.append(_t(x0 + BOX_W - 10, yy, f"{_pct(st['twh'])} TWh", size=11.5, weight=600, anchor="end"))
            g.append(f'<rect class="gf-bar" x="{x0 + 10}" y="{yy + 5}" width="{bw}" height="5" rx="2.5"/>')
            g.append(f'<rect x="{x0 + 10}" y="{yy + 5}" width="{bw * min(100, max(0, st["full"])) / 100:.1f}" '
                     f'height="5" rx="2.5" fill="{col}"/>')
            net = st.get("net") or 0
            word = "vtláčení" if net > 0 else "těžba" if net < 0 else "zásobníky"
            g.append(_t(x0 + 10, yy + 26, f"{'▲' if net > 0 else '▼' if net < 0 else '•'} {word} "
                                          f"{fmt_num(abs(net), unit)} {u}", cls="gf-m", size=11.5))
        else:
            g.append(_t(x0 + 10, yy, "bez zásobníků v GIE" if code in ("SI", "CH") else "zásobníky: —",
                        cls="gf-m", size=11))
        if b:
            g.append(_t(x0 + 10, yy + 46, f"spotřeba ≈ {fmt_num(b['implied'], unit)}", size=12.5, weight=700))
            if b.get("reported") and b["reported"] > 0.25 * max(b["implied"], 1):
                g.append(_t(x0 + BOX_W - 10, yy + 46, f"(hl. {fmt_num(b['reported'], unit)})", cls="gf-m", size=10.5,
                            anchor="end"))
        elif code in ("NO", "UA", "CH"):
            g.append(_t(x0 + 10, yy + 46, "toky z pohledu sousedů", cls="gf-m", size=10.5))
        g.append("</g>")
        out += g
    # ── popisky hran (nad vším; posunuté po hraně mimo boxy a už umístěné popisky) ──
    taken = []
    for pts, e, col, tip in labels:
        if e["value"] < 0.5:
            continue
        txt = fmt_num(e["value"], unit)
        warn = bool(e.get("checks"))
        tw = 9 + 7.6 * len(txt) + (20 if warn else 0)
        mx, my = _label_pos(pts, tw, nodes, taken)
        taken.append((mx - tw / 2, my - 10, mx + tw / 2, my + 10))
        out.append(f'<g data-tip="{escape(tip)}"><title>{escape(tip)}</title>'
                   f'<rect class="gf-lbl" x="{mx - tw / 2:.1f}" y="{my - 10:.1f}" width="{tw:.1f}" height="20" rx="10"/>'
                   + _t(mx - (10 if warn else 0), my + 4.5, txt, size=12.5, weight=700, anchor="middle",
                        extra=f' fill="{col if col != COL_FLAT else "#37474f"}"')
                   + (f'<g transform="translate({mx + tw / 2 - 15:.1f},{my - 8:.1f})"><path class="gf-warn" '
                      f'd="M7,0 L14,13 L0,13 Z"/>{_t(7, 11.5, "!", cls="", size=10, weight=800, anchor="middle", extra=" fill=\"#3e2723\"")}</g>'
                      if warn else "")
                   + "</g>")
    # ── legenda ──
    lx, ly = 950, 960
    out.append(f'<g transform="translate({lx},{ly})">')
    for i, (col, lbl) in enumerate([(COL_UP, "tok roste"), (COL_DOWN, "tok klesá"), (COL_FLAT, "beze změny"),
                                    (COL_LNG, "LNG terminály")]):
        out.append(f'<line x1="0" y1="{i * 18}" x2="26" y2="{i * 18}" stroke="{col}" stroke-width="5"/>'
                   + _t(34, i * 18 + 4, lbl, cls="gf-m", size=11.5))
    out.append(f'<g transform="translate(120,-7)"><path class="gf-warn" d="M7,0 L14,13 L0,13 Z"/></g>'
               + _t(140, 4, "exit ≠ entry", cls="gf-m", size=11.5))
    out.append(_t(120, 22, "▲ vtláčení ▼ těžba", cls="gf-m", size=11.5))
    out.append(_t(120, 40, "tloušťka ~ objem", cls="gf-m", size=11.5))
    out.append("</g>")
    out.append(_t(20, H - 14, "Zdroj: ENTSO-G (fyzické toky, D-1/D-2), GIE AGSI+/ALSI · spotřeba ≈ odvozená bilance "
                              "(vstupy − výstupy + LNG + těžba + zásobníky)", cls="gf-m", size=10.5))
    out.append("</svg>")
    return "".join(out)
