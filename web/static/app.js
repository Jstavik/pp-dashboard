/* PP Dashboard — klient: líné záložky (HTML fragmenty ze serveru), grafy
   jako Plotly JSON (/api/chart/...), ovládací prvky = formulář, jehož
   změna znovu načte fragment se stejnými parametry, auto-refresh. */
(function () {
  "use strict";

  var DARK = {
    text: "#e3e9f0", muted: "#8d9cab", grid: "#2a3644", zero: "#3a4858",
    surface: "#1d2733", border: "#2a3644", mapBg: "#121a22", mapLine: "#4a5a6a",
  };
  var LIGHT = {
    text: "#1d2733", muted: "#66788a", grid: "#e6ebf1", zero: "#cfd8e3",
    surface: "#ffffff", border: "#dde3ea", mapBg: null, mapLine: "#aaaaaa",
  };

  function lighten(hex) {
    var m = /^#([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return hex;
    var n = parseInt(m[1], 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
    var lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    if (lum > 0.55) return hex;
    var k = 0.45;
    r = Math.round(r + (255 - r) * k); g = Math.round(g + (255 - g) * k); b = Math.round(b + (255 - b) * k);
    return "rgb(" + r + "," + g + "," + b + ")";
  }

  function isNearBlack(hex) {
    var m = /^#([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return hex === "black";
    var n = parseInt(m[1], 16);
    return (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255 < 0.22;
  }

  function isDark() { return document.documentElement.getAttribute("data-theme") === "dark"; }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function isWhiteish(c) {
    return typeof c === "string" && /^(#fff(fff)?|white|rgba?\(255,\s*255,\s*255)/i.test(c.replace(/\s/g, ""));
  }

  /* Úprava layoutu podle tématu — grafy z charts/ mají natvrdo bílé pozadí
     a světlou mřížku; pozadí se zprůhlední (barvu dá karta) a v tmavém
     režimu se přebarví text, mřížka, tooltip a podklad mapy. */
  function themed(fig, forExport) {
    // forExport: obrázek pro report — vždy světlý režim s bílým pozadím
    var dark = !forExport && isDark();
    var f = clone(fig), L = f.layout || (f.layout = {}), c = dark ? DARK : LIGHT;
    L.paper_bgcolor = "rgba(0,0,0,0)";
    L.plot_bgcolor = "rgba(0,0,0,0)";
    L.font = Object.assign({}, L.font || {}, { color: c.text });
    delete L.width;
    L.autosize = true;
    // osy bez explicitního nastavení by si vzaly bílou mřížku ze šablony plotly_white
    if (!L.xaxis) L.xaxis = {};
    if (!L.yaxis) L.yaxis = {};
    Object.keys(L).forEach(function (k) {
      if (/^[xy]axis\d*$/.test(k)) {
        var ax = L[k] = Object.assign({}, L[k]);
        ax.gridcolor = c.grid; ax.zerolinecolor = c.zero; ax.linecolor = c.border;
        if (ax.title && ax.title.font) delete ax.title.font.color;
      }
    });
    if (L.title !== undefined) {
      L.title = typeof L.title === "string" ? { text: L.title } : Object.assign({}, L.title);
      L.title.font = Object.assign({}, L.title.font || {}, { color: c.text });
    }
    if (L.template && L.template.layout) {
      // šablona (plotly_white) nese vlastní tmavé barvy textu — přebíjely by tmavý režim
      ["font", "title"].forEach(function (k) { delete L.template.layout[k]; });
      delete L.template.layout.annotationdefaults;
    }
    if (L.legend) {
      L.legend = Object.assign({}, L.legend);
      if (!L.legend.bgcolor || isWhiteish(L.legend.bgcolor)) L.legend.bgcolor = dark ? "rgba(22,30,39,.85)" : "rgba(255,255,255,.85)";
      if (L.legend.bordercolor) L.legend.bordercolor = c.border;
      if (L.legend.font) delete L.legend.font.color;
    }
    L.hoverlabel = Object.assign({}, L.hoverlabel || {}, { bgcolor: c.surface, bordercolor: c.border, font: Object.assign({}, (L.hoverlabel || {}).font, { color: c.text }) });
    (L.annotations || []).forEach(function (a) {
      if (dark && isWhiteish(a.bgcolor)) a.bgcolor = "rgba(22,30,39,.85)";
      if (a.font && (a.font.color === "#263238" || a.font.color === "#333" || a.font.color === "black")) a.font.color = c.text;
    });
    (L.shapes || []).forEach(function (s) {
      if (dark && s.line && (s.line.color === "black" || s.line.color === "#000")) s.line.color = c.muted;
    });
    if (L.map) {
      L.map = Object.assign({}, L.map);
      (L.map.layers || []).forEach(function (ly) { if (ly.type === "line") ly.color = c.mapLine; });
      if (dark && (L.map.style === "white-bg" || !L.map.style)) {
        L.map.style = { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": c.mapBg } }] };
      }
    }
    if (L.template && L.template.layout) {
      delete L.template.layout.paper_bgcolor;
      delete L.template.layout.plot_bgcolor;
    }
    (f.data || []).forEach(function (t) {
      if (!dark) return;
      if (t.type === "pie" && t.marker && t.marker.line && isWhiteish(t.marker.line.color)) t.marker.line.color = c.surface;
      // tmavé popisky (např. toky v mapě) zesvětlit, ať jsou na tmavém pozadí čitelné
      if (t.textfont && typeof t.textfont.color === "string") t.textfont.color = lighten(t.textfont.color);
      // téměř černé čáry (zatížení, saldo) by na tmavém pozadí zmizely
      if (t.line && typeof t.line.color === "string" && isNearBlack(t.line.color)) t.line = Object.assign({}, t.line, { color: c.text });
    });
    if (forExport) {
      L.paper_bgcolor = "#ffffff";
      L.plot_bgcolor = "#ffffff";
    }
    return f;
  }

  /* ── grafy ─────────────────────────────────────────────────────────── */
  var observer = "IntersectionObserver" in window ? new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) { observer.unobserve(e.target); loadChart(e.target); }
    });
  }, { rootMargin: "300px 0px" }) : null;

  function chartUrl(el) {
    var frag = el.closest(".frag");
    return "/api/chart/" + frag.dataset.page + "/" + frag.dataset.tab + "/" + el.dataset.chart +
      "?" + (frag.dataset.query || "") + (el._ppExtra ? "&" + el._ppExtra : "");
  }

  function showMsg(el, text, cls) {
    if (window.Plotly && el._ppFig) { try { Plotly.purge(el); } catch (e) { /* noop */ } }
    el._ppFig = null;
    var card = el.closest(".chart-card");
    if (card) card.classList.remove("has-fig");
    el.innerHTML = '<div class="chart-msg ' + (cls || "") + '"></div>';
    el.firstChild.textContent = text;
  }

  function renderFig(el, payload) {
    var cfg = Object.assign({ responsive: true, displaylogo: false, displayModeBar: false }, payload.config || {});
    el._ppFig = payload.figure; el._ppCfg = cfg;
    var card = el.closest(".chart-card");
    if (card) card.classList.add("has-fig");
    var f = themed(payload.figure);
    if (!el.querySelector(".plot-container")) el.innerHTML = "";
    return Plotly.react(el, f.data || [], f.layout || {}, cfg).then(function () { bindCrossFilter(el); });
  }

  /* ── cross-filtering (Průzkumník) ──────────────────────────────────── */
  // [data-xrange]: zoom časového grafu přepočítá grafy [data-xfilter] jen
  // pro vybrané okno; [data-xfilter="f_<dim>"]: klik na sloupec nastaví
  // filtr dimenze na tuto hodnotu (znovu = zrušit) a přenačte záložku.
  function isoDay(v) { return String(v).slice(0, 10); }

  function bindCrossFilter(el) {
    if (el._ppXBound || !el.on) return;
    var panel = el.closest(".tab-panel");
    var xr = el.closest("[data-xrange]"), xf = el.closest("[data-xfilter]");
    if (!panel || (!xr && !xf)) return;
    el._ppXBound = true;
    if (xr) el.on("plotly_relayout", function (ev) {
      var extra = null;
      var r = ev["xaxis.range"] || (ev["xaxis.range[0]"] !== undefined ? [ev["xaxis.range[0]"], ev["xaxis.range[1]"]] : null);
      if (r) extra = "xf_from=" + isoDay(r[0]) + "&xf_to=" + isoDay(r[1]);
      else if (!ev["xaxis.autorange"]) return;
      panel.querySelectorAll("[data-xfilter] .chart[data-chart]").forEach(function (t) {
        t._ppExtra = extra; loadChart(t, true);
      });
    });
    if (xf) el.on("plotly_click", function (ev) {
      var pt = ev.points && ev.points[0];
      if (!pt) return;
      var val = String(pt.customdata !== undefined ? pt.customdata : pt.y);
      var name = xf.dataset.xfilter;
      var boxes = Array.prototype.slice.call(panel.querySelectorAll('input[type=checkbox][name="' + name + '"]'));
      var checked = boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; });
      var only = checked.length === 1 && checked[0] === val;
      var found = false;
      boxes.forEach(function (b) { b.checked = !only && b.value === val; if (b.value === val) found = true; });
      if (!found && !only) {
        var form = panel.querySelector("form.explorer-filters") || panel.querySelector("form.controls");
        var h = document.createElement("input");
        h.type = "hidden"; h.name = name; h.value = val; form.appendChild(h);
        var set = document.createElement("input");
        set.type = "hidden"; set.name = name + "__set"; set.value = "1"; form.appendChild(set);
      }
      loadTab(panel, formQuery(panel));
    });
  }

  /* ── uložené pohledy (Průzkumník) ──────────────────────────────────── */
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-view-save],[data-view-delete],[data-copy-link]");
    if (!t) return;
    var panel = t.closest(".tab-panel");
    if (t.hasAttribute("data-copy-link")) {
      var done = function () { t.textContent = "Zkopírováno ✓"; setTimeout(function () { t.textContent = "Kopírovat odkaz"; }, 1500); };
      if (navigator.clipboard) navigator.clipboard.writeText(location.href).then(done, function () { prompt("Odkaz:", location.href); });
      else prompt("Odkaz:", location.href);
      return;
    }
    if (!panel) return;
    var req;
    if (t.hasAttribute("data-view-save")) {
      var inp = panel.querySelector(".view-name");
      var name = inp && inp.value.trim();
      if (!name) { if (inp) inp.focus(); return; }
      req = fetch("/api/explorer/views", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, query: panel.dataset.query || "" }) });
    } else {
      if (!confirm("Smazat pohled „" + t.dataset.viewDelete + "“?")) return;
      req = fetch("/api/explorer/views?name=" + encodeURIComponent(t.dataset.viewDelete), { method: "DELETE" });
    }
    req.then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); loadTab(panel, panel.dataset.query || ""); })
      .catch(function (err) { alert("Uložení pohledu selhalo: " + err.message); });
  });

  function loadChart(el, quiet) {
    if (!quiet) { el.classList.add("loading"); }
    var url = chartUrl(el);
    el._ppUrl = url;
    return fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (payload) {
        if (el._ppUrl !== url) return;  // mezitím přišel novější požadavek
        el.classList.remove("loading");
        if (payload.error) return showMsg(el, payload.error, "err");
        if (payload.empty || !payload.figure) return showMsg(el, "Data nejsou dostupná.");
        return whenPlotly().then(function () { return renderFig(el, payload); });
      })
      .catch(function (err) {
        el.classList.remove("loading");
        if (!quiet) showMsg(el, "Graf se nepodařilo načíst (" + err.message + ")", "err");
      });
  }

  function whenPlotly() {
    return new Promise(function (resolve) {
      (function wait() { if (window.Plotly) resolve(); else setTimeout(wait, 30); })();
    });
  }

  function initCharts(root) {
    root.querySelectorAll(".chart[data-chart]").forEach(function (el) {
      addExportTools(el);
      if (observer) observer.observe(el); else loadChart(el);
    });
  }

  /* ── export (PNG/SVG obrázek, CSV/XLSX data) ───────────────────────── */
  // Obrázky se renderují na klientu z poslední figury grafu, vždy ve
  // světlém režimu s bílým pozadím (pro reporty); PNG 3× rozlišení.
  // Data dává server (/api/chart-data/...) se stejnými parametry jako graf.
  var EXPORT_W = 1600;

  function fileBase(el) {
    var frag = el.closest(".frag");
    // stejné razítko (pražský čas) jako u CSV/XLSX ze serveru
    var stamp = new Date().toLocaleString("sv-SE", { timeZone: "Europe/Prague", hour12: false })
      .replace(/[-:]/g, "").replace(" ", "-").slice(0, 13);
    return [frag.dataset.page, frag.dataset.tab, el.dataset.chart].join("_") + "_" + stamp;
  }

  function saveImage(el, format) {
    if (!el._ppFig || !window.Plotly) return;
    var f = themed(el._ppFig, true);
    var h = Math.max(el.clientHeight || 0, (f.layout && f.layout.height) || 0, 360);
    var w = Math.max(EXPORT_W, el.clientWidth || 0);
    f.layout.width = w; f.layout.height = h; f.layout.autosize = false;
    Plotly.toImage({ data: f.data, layout: f.layout, config: { staticPlot: true } },
                   { format: format, width: w, height: h, scale: format === "png" ? 3 : 1 })
      .then(function (url) {
        var a = document.createElement("a");
        a.href = url; a.download = fileBase(el) + "." + format;
        document.body.appendChild(a); a.click(); a.remove();
      })
      .catch(function (err) { alert("Export obrázku selhal: " + err.message); });
  }

  function dataUrl(el, fmt) {
    var u = chartUrl(el).replace("/api/chart/", "/api/chart-data/");
    return u + (u.indexOf("?") >= 0 ? "&" : "?") + "fmt=" + fmt;
  }

  function addExportTools(el) {
    var card = el.closest(".chart-card");
    if (!card || card.querySelector(".chart-tools")) return;
    var bar = document.createElement("div");
    bar.className = "chart-tools";
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", "Export grafu");
    [["png", "PNG", "Obrázek PNG (vysoké rozlišení, světlý režim)"],
     ["svg", "SVG", "Vektorový obrázek SVG (světlý režim)"],
     ["csv", "CSV", "Data za grafem (CSV)"],
     ["xlsx", "XLSX", "Data za grafem (Excel)"]].forEach(function (b) {
      var btn = document.createElement("button");
      btn.type = "button"; btn.textContent = b[1]; btn.title = b[2]; btn.dataset.fmt = b[0];
      btn.addEventListener("click", function () {
        if (b[0] === "png" || b[0] === "svg") return saveImage(el, b[0]);
        var a = document.createElement("a");
        a.href = dataUrl(el, b[0]); a.download = "";
        document.body.appendChild(a); a.click(); a.remove();
      });
      bar.appendChild(btn);
    });
    card.appendChild(bar);
  }

  function rethemeAll() {
    document.querySelectorAll(".chart").forEach(function (el) {
      if (el._ppFig && window.Plotly) {
        var f = themed(el._ppFig);
        Plotly.react(el, f.data || [], f.layout || {}, el._ppCfg);
      }
    });
  }

  /* ── fragmenty záložek ─────────────────────────────────────────────── */
  var pageEl = document.querySelector(".page");
  var refreshTimers = {};

  function fragUrl(tab, query) {
    return "/frag/" + pageEl.dataset.page + "/" + tab + (query ? "?" + query : "");
  }

  function setUrl(tab, query) {
    var q = "tab=" + encodeURIComponent(tab) + (query ? "&" + query : "");
    history.replaceState(null, "", location.pathname + "?" + q);
  }

  function loadTab(panel, query, opts) {
    opts = opts || {};
    var tab = panel.dataset.tab;
    if (!opts.silent) {
      panel.classList.add("is-loading");
      if (!panel.firstChild) panel.innerHTML = '<div class="loading-block"></div>';
    }
    panel._ppReq = (panel._ppReq || 0) + 1;
    var req = panel._ppReq;
    return fetch(fragUrl(tab, query)).then(function (r) { return r.text(); }).then(function (html) {
      if (req !== panel._ppReq) return;
      panel.classList.remove("is-loading");
      if (opts.partsOnly && panel.querySelector(".frag")) {
        swapParts(panel, html);
      } else {
        panel.innerHTML = html;
        panel.dataset.loaded = "1";
        bindForms(panel);
        initCharts(panel);
      }
      var frag = panel.querySelector(".frag");
      if (frag && !opts.partsOnly) {
        panel.dataset.query = frag.dataset.query || "";
        if (isActive(panel)) setUrl(tab, panel.dataset.query);
        setupRefresh(panel, frag);
      }
    }).catch(function (err) {
      if (!opts.silent) panel.innerHTML = '<div class="alert alert-bad">Záložku se nepodařilo načíst: ' + err.message + "</div>";
    });
  }

  // Auto-refresh: přenačte jen bloky [data-part] (KPI, tabulky) a grafy
  // přes Plotly.react — bez probliknutí a bez ztráty rozbalených prvků.
  function swapParts(panel, html) {
    var tpl = document.createElement("template");
    tpl.innerHTML = html;
    tpl.content.querySelectorAll("[data-part]").forEach(function (nu) {
      var old = panel.querySelector('[data-part="' + nu.dataset.part + '"]');
      if (old) old.replaceWith(nu);
    });
    panel.querySelectorAll(".chart[data-chart]").forEach(function (el) {
      if (el._ppFig) loadChart(el, true);
    });
  }

  function setupRefresh(panel, frag) {
    var tab = panel.dataset.tab;
    clearInterval(refreshTimers[tab]);
    var secs = parseInt(frag.dataset.refresh || "0", 10);
    if (!secs) return;
    refreshTimers[tab] = setInterval(function () {
      if (document.hidden || !isActive(panel)) return;
      loadTab(panel, panel.dataset.query, { silent: true, partsOnly: true });
    }, secs * 1000);
  }

  function isActive(panel) { return !panel.hidden; }

  function activate(tab) {
    document.querySelectorAll(".tab").forEach(function (b) {
      var on = b.dataset.tab === tab;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll(".tab-panel").forEach(function (p) {
      var on = p.dataset.tab === tab;
      p.hidden = !on;
      if (on) {
        if (!p.dataset.loaded) loadTab(p, p.dataset.query || "");
        else {
          setUrl(tab, p.dataset.query || "");
          // grafy vykreslené ve skrytém panelu mohou mít špatnou šířku
          if (window.Plotly) p.querySelectorAll(".js-plotly-plot").forEach(function (g) { Plotly.Plots.resize(g); });
        }
      }
    });
  }

  /* ── formuláře (ovládací prvky záložky) ────────────────────────────── */
  // Všechny formuláře záložky se slučují do jednoho dotazu — ovládací
  // prvky můžou být rozmístěné u jednotlivých grafů.
  function formQuery(panel, submitter) {
    var parts = [];
    panel.querySelectorAll("form.controls").forEach(function (form) {
      new FormData(form).forEach(function (v, k) {
        if (v !== "") parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(v));
      });
    });
    if (submitter && submitter.name) parts.push(encodeURIComponent(submitter.name) + "=" + encodeURIComponent(submitter.value));
    return parts.join("&");
  }

  function bindForms(panel) {
    panel.querySelectorAll("form.controls").forEach(function (form) {
      var submit = function (submitter) {
        var q = formQuery(panel, submitter);
        loadTab(panel, q);
      };
      form.addEventListener("submit", function (e) { e.preventDefault(); submit(e.submitter); });
      form.addEventListener("change", function (e) {
        var t = e.target;
        if (t.closest(".ms")) { t.closest(".ms").dataset.dirty = "1"; return; }
        if (t.classList.contains("ms-filter")) return;
        submit(null);
      });
      form.addEventListener("input", function (e) {
        if (e.target.type === "range") {
          var out = e.target.parentElement.querySelector("output");
          if (out) out.textContent = e.target.value;
        }
        if (e.target.classList.contains("ms-filter")) {
          var needle = e.target.value.toLowerCase();
          e.target.closest(".ms").querySelectorAll(".ms-list label").forEach(function (l) {
            l.style.display = l.textContent.toLowerCase().indexOf(needle) >= 0 ? "" : "none";
          });
        }
      });
      form.querySelectorAll(".ms").forEach(function (ms) {
        ms.addEventListener("toggle", function () {
          if (!ms.open && ms.dataset.dirty) { ms.dataset.dirty = ""; submit(null); }
        });
        ms.querySelectorAll("[data-ms]").forEach(function (btn) {
          btn.addEventListener("click", function () {
            var on = btn.dataset.ms === "all";
            ms.querySelectorAll(".ms-list label").forEach(function (l) {
              if (l.style.display !== "none") l.querySelector("input").checked = on;
            });
            ms.dataset.dirty = "1";
          });
        });
      });
    });
  }

  // zavřít otevřený multiselect klikem mimo
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.ms[open]").forEach(function (d) {
      if (!d.contains(e.target)) d.open = false;
    });
  });

  /* ── hlavička stránky (banner + KPI, Elektřina) ────────────────────── */
  function loadHeader(el) {
    return fetch(el.dataset.src).then(function (r) { return r.text(); }).then(function (html) {
      el.innerHTML = html;
      el.classList.remove("loading-block");
    }).catch(function () { /* necháme poslední stav */ });
  }

  /* ── start ─────────────────────────────────────────────────────────── */
  function start() {
    var toggle = document.getElementById("theme-toggle");
    if (toggle) toggle.addEventListener("click", function () {
      var t = isDark() ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", t);
      try { localStorage.setItem("ppdash-theme", t); } catch (e) { /* noop */ }
      rethemeAll();
    });
    var clear = document.getElementById("cache-clear");
    if (clear) clear.addEventListener("click", function () {
      clear.disabled = true;
      fetch("/api/cache/clear", { method: "POST" }).then(function () { location.reload(); });
    });
    var clock = document.getElementById("clock");
    if (clock) {
      var tick = function () {
        clock.textContent = new Date().toLocaleString("cs-CZ", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Prague" });
      };
      tick(); setInterval(tick, 30000);
    }
    if (!pageEl) return;

    var header = document.getElementById("page-header");
    if (header) {
      loadHeader(header);
      var hs = parseInt(header.dataset.refresh || "0", 10);
      if (hs) setInterval(function () { if (!document.hidden) loadHeader(header); }, hs * 1000);
    }

    var active = pageEl.dataset.active;
    var initialQuery = pageEl.dataset.initialQuery || "";
    document.querySelectorAll(".tab-panel").forEach(function (p) {
      if (p.dataset.tab === active) p.dataset.query = initialQuery;
    });
    document.querySelectorAll(".tab").forEach(function (b) {
      b.addEventListener("click", function () { activate(b.dataset.tab); });
    });
    activate(active);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
