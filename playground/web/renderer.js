// Renders the component tree into shadcn-styled DOM (light or dark).
// Charts are inline SVG with axes, gridlines, currency ticks, stacked/grouped
// multi-series, and legends — no external charting library.

// Reactive state: filters with a `stateKey` write here; bound values read from here;
// a change re-paints the tree instantly (no API call).
let RS = { list: [], byId: {}, mount: null, state: {} };

// STRICT by default: the renderer assumes verified input and renders literally. LENIENT is
// turned ON only in the playground (which composes exploratory, unverified surfaces). It gates
// the salvage behaviors — TVAR variant remapping, strChild fallback, and the ui-unknown default
// path via the verifier below. See window.setRendererLenient / window.__CATALYST_LENIENT.
let LENIENT = (typeof window !== "undefined" && window.__CATALYST_LENIENT === true) || false;

// Verification gate. When a catalog is present (window.__catalog) and verify.js is loaded, every
// surface is checked against the catalog before it renders. Non-consequential failures are repaired
// (lenient) or fatal (strict); consequential failures are always rejected — never coerced.
function verifyGate(list, mount) {
  const w = (typeof window !== "undefined") ? window : {};
  const catalog = w.__catalog;
  if (!catalog || typeof w.verifySurface !== "function") return { list };  // no catalog -> no-op (back-compat)
  const res = w.verifySurface(list, catalog, { lenient: LENIENT });
  res.results.forEach((r) => {
    if (r.action !== "keep") console.warn("[catalyst.verify] " + r.component + " -> " + r.action + ": " + (r.errors || []).join("; "));
  });
  if (!LENIENT) {
    if (!res.ok) { mount.innerHTML = ""; mount.appendChild(renderVerifyError(res)); return { halt: true }; }
    return { list };                 // strict + verified: render literally
  }
  return { list: res.repaired };     // lenient: repaired tree (bad non-consequential fixed; consequential -> inline Alert)
}
function renderVerifyError(res) {
  const box = el("div", "ui-verify-error");
  box.style.cssText = "border:1px solid hsl(0 72% 55% / .4);background:hsl(0 72% 55% / .06);color:hsl(0 60% 40%);border-radius:12px;padding:14px 16px;font-size:13px;line-height:1.5";
  const h = el("div", null, "This screen was blocked by catalog verification");
  h.style.cssText = "font-weight:600;margin-bottom:6px";
  box.appendChild(h);
  const ul = el("ul"); ul.style.cssText = "margin:0;padding-left:18px";
  const rows = res.rejected.length ? res.rejected : res.results.filter((r) => r.action !== "keep");
  rows.forEach((r) => ul.appendChild(el("li", null, (r.component || "?") + " — " + ((r.errors || []).join("; ") || "invalid"))));
  box.appendChild(ul);
  return box;
}

function renderA2UI(components, _data, mount) {
  let list = Array.isArray(components) ? components : [components];
  const gate = verifyGate(list, mount);
  if (gate.halt) return;             // strict + invalid: a clear error was painted instead
  list = gate.list;
  const byId = {};
  list.forEach((c) => c && c.id && (byId[c.id] = c));
  RS = { list, byId, mount, state: buildState(list) };
  const wide = /"(BarChart|LineChart|Donut|PieChart|Table|Grid|Metric|Screen)"/.test(JSON.stringify(list));
  mount.dataset.width = wide ? "lg" : "sm";
  paint();
}
function paint() {
  const { list, byId, mount } = RS;
  mount.innerHTML = "";
  const referenced = new Set();
  for (const c of list) collectRefs(c, referenced);
  const roots = list.filter((c) => c && !referenced.has(c.id));
  (roots.length ? roots : list).forEach((n) => mount.appendChild(node(n, byId)));
}
function deepWalk(o, cb) {
  if (Array.isArray(o)) o.forEach((x) => deepWalk(x, cb));
  else if (o && typeof o === "object") { cb(o); for (const k in o) deepWalk(o[k], cb); }
}
function buildState(list) {
  const s = {};
  deepWalk(list, (o) => { if (o && o.stateKey != null && o.component) s[o.stateKey] = o.value; });
  return s;
}
// Resolve a value that may be a binding: {bind:{key, map:{option: value}}} -> map[state[key]]
function bindVal(v) {
  if (v && typeof v === "object" && v.bind && v.bind.map) {
    const m = v.bind.map, key = v.bind.key;
    if (key in RS.state && RS.state[key] in m) return m[RS.state[key]];
    return m[Object.keys(m)[0]];
  }
  return v;
}
function setState(key, val) { RS.state[key] = val; paint(); }

function collectRefs(c, set) {
  if (!c || typeof c !== "object") return;
  const ks = c.children || (c.child ? [c.child] : []);
  (Array.isArray(ks) ? ks : [ks]).forEach((k) => typeof k === "string" && set.add(k));
}
function kids(c, byId) {
  let ks = c.children || (c.child ? [c.child] : []);
  if (!Array.isArray(ks)) ks = [ks];
  return ks.map((k) => (typeof k === "string" ? byId[k] : k)).filter(Boolean);
}
function renderArr(arr, byId) {
  return (arr || []).map((k) => (typeof k === "string" ? byId[k] : k))
    .filter(Boolean).map((n) => node(n, byId));
}
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function field(labelText) {
  const w = el("div", "ui-field");
  if (labelText) w.appendChild(el("label", "ui-label", labelText));
  return w;
}
const ICON_PATHS = {
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
  export: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
  upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
  filter: "M22 3H2l8 9.46V19l4 2v-8.54L22 3z",
  plus: "M12 5v14M5 12h14",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35",
  more: "M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM19 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM5 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2z",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z",
  calendar: "M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0",
  share: "M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8M16 6l-4-4-4 4M12 2v13",
  refresh: "M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15",
};
function iconSvg(name) {
  const p = ICON_PATHS[name] || ICON_PATHS.more;
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round", width: "16", height: "16" }))
    s.setAttribute(k, v);
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", p); s.appendChild(path); return s;
}

// Text variant aliases the model sometimes uses, mapped to our real variants.
// LENIENT-only: wrapped in a Proxy so that in strict mode the lookup returns undefined and the
// renderer falls through to the literal variant it was given (no silent remapping).
const _TVAR = { headline: "title", heading: "title", h1: "title", h2: "subtitle", h3: "subtitle", caption: "muted", small: "muted" };
const TVAR = new Proxy(_TVAR, { get: (t, k) => (LENIENT ? t[k] : undefined) });
// Some models put the text content in `children` as a string instead of the text/label prop.
// LENIENT-only: strict mode assumes verified input and does not salvage misplaced text.
const strChild = (c) => (LENIENT && typeof c.children === "string" ? c.children : "");

function node(c, byId) {
  if (!c || typeof c !== "object") return document.createComment("empty");
  const K = (b) => kids(c, byId).forEach((k) => b.appendChild(node(k, byId)));
  switch (c.component) {
    /* ---- shell / layout ---- */
    case "Screen": case "Page": {
      const s = el("div", "ui-screen" + (c.theme === "dark" ? " ui-dark" : ""));
      if (c.width) s.dataset.width = c.width;
      // Panel the screen for plain forms; stay transparent when it already has cards (dashboard)
      const rich = /"(Card|Metric|Grid|BarChart|LineChart|Donut|PieChart|Table)"/.test(JSON.stringify(c));
      if (c.theme !== "dark" && !rich) s.classList.add("paneled");
      K(s); return s;
    }
    case "Card": { const b = el("div", "ui-card"); K(b); return b; }
    case "Stack": case "Column": { const b = el("div", "ui-stack gap-" + (c.gap || "md")); K(b); return b; }
    case "Row": {
      const b = el("div", "ui-row gap-" + (c.gap || "md"));
      if (c.justify) b.dataset.justify = c.justify;
      if (c.align) b.dataset.align = c.align;
      if (c.wrap) b.dataset.wrap = "1";
      K(b); return b;
    }
    case "Grid": {
      const b = el("div", "ui-grid");
      b.style.gridTemplateColumns = `repeat(${c.cols || 2}, minmax(0,1fr))`;
      K(b); return b;
    }
    case "Separator": case "Divider": return el("hr", "ui-separator");

    /* ---- typography ---- */
    case "Text": {
      const t = bindVal(c.text) ?? (typeof c.children === "string" ? c.children : "");
      return el("p", "ui-text v-" + (TVAR[c.variant] || c.variant || "body"), t);
    }
    case "Link": { const a = el("a", "ui-link", c.text || c.label || strChild(c) || ""); a.href = c.href || "#"; a.onclick = (e) => e.preventDefault(); return a; }

    /* ---- inputs ---- */
    case "Input": {
      const w = field(c.label); const i = el("input", "ui-input");
      i.type = c.type || "text"; if (c.placeholder) i.placeholder = c.placeholder;
      if (c.value != null) i.value = c.value; w.appendChild(i); return w;
    }
    case "Textarea": {
      const w = field(c.label); const t = el("textarea", "ui-input ui-textarea");
      if (c.placeholder) t.placeholder = c.placeholder; if (c.value != null) t.value = c.value;
      w.appendChild(t); return w;
    }
    case "DatePicker": {
      const w = field(c.label); const i = el("input", "ui-input");
      i.type = "date"; if (c.value) i.value = c.value; w.appendChild(i); return w;
    }
    case "Select": {
      const cur = c.stateKey != null && c.stateKey in RS.state ? RS.state[c.stateKey] : bindVal(c.value);
      const onPick = (v) => { if (c.stateKey != null) setState(c.stateKey, v); else toast((c.prefix || c.label || "filter") + ": " + v); };
      if (c.variant === "filter") {
        const p = el("span", "ui-filter");
        if (c.prefix) p.appendChild(el("span", "ui-filter-pre", c.prefix));
        const s = el("select", "ui-filter-select");
        (c.options || []).forEach((o) => {
          const op = el("option", null, typeof o === "object" ? o.label : o);
          op.value = typeof o === "object" ? (o.value ?? o.label) : o; s.appendChild(op);
        });
        if (cur != null) s.value = cur;
        s.onchange = () => onPick(s.value);
        p.appendChild(s); return p;
      }
      const w = field(c.label); const s = el("select", "ui-input ui-select");
      (c.options || []).forEach((o) => {
        const op = el("option", null, typeof o === "object" ? o.label : o);
        op.value = typeof o === "object" ? (o.value ?? o.label) : o; s.appendChild(op);
      });
      if (cur != null) s.value = cur; s.onchange = () => onPick(s.value); w.appendChild(s); return w;
    }
    case "RadioGroup": {
      const w = field(c.label); const nm = "r" + Math.random().toString(36).slice(2);
      (c.options || []).forEach((o) => {
        const lab = el("label", "ui-radio"); const i = el("input"); i.type = "radio"; i.name = nm;
        const val = typeof o === "object" ? (o.value ?? o.label) : o; i.checked = c.value === val;
        lab.appendChild(i); lab.append(" " + (typeof o === "object" ? o.label : o)); w.appendChild(lab);
      });
      return w;
    }
    case "Slider": {
      const w = field(); const head = el("div", "ui-slider-head");
      head.appendChild(el("label", "ui-label", c.label || ""));
      const val = el("span", "ui-slider-val", c.value ?? ""); head.appendChild(val); w.appendChild(head);
      const r = el("input", "ui-range"); r.type = "range";
      r.min = c.min ?? 0; r.max = c.max ?? 100; r.value = c.value ?? 0;
      r.oninput = () => (val.textContent = r.value); w.appendChild(r); return w;
    }
    case "Checkbox": case "Switch": {
      const w = el("label", c.component === "Switch" ? "ui-switch" : "ui-check");
      const i = el("input"); i.type = "checkbox"; i.checked = !!c.checked;
      if (c.component === "Switch") { w.appendChild(i); w.appendChild(el("span", "ui-track")); }
      else w.appendChild(i);
      w.appendChild(el("span", "ui-check-label", c.label || "")); return w;
    }
    case "Toggle": {
      const b = el("button", "ui-toggle" + (c.pressed ? " on" : ""), c.label || "");
      b.onclick = () => b.classList.toggle("on"); return b;
    }
    case "Button": {
      const label = c.label || strChild(c);
      const b = el("button", "ui-btn v-" + (c.variant || "default") + " s-" + (c.size || "md"), label);
      if (!label && (c.child || Array.isArray(c.children))) { b.textContent = ""; K(b); }
      b.onclick = () => toast((label || "action") + " →"); return b;
    }
    case "IconButton": {
      const b = el("button", "ui-iconbtn"); b.appendChild(iconSvg(c.icon));
      if (c.label) b.title = c.label;
      b.onclick = () => toast((c.label || c.icon || "action") + " →"); return b;
    }

    /* ---- display ---- */
    case "Badge": return el("span", "ui-badge v-" + (c.tone || "default"), bindVal(c.label) || strChild(c) || "");
    case "Avatar": {
      const fb = () => el("span", "ui-avatar ui-avatar-fallback", (c.fallback || "?").slice(0, 2));
      if (!c.url) return fb();
      const img = el("img", "ui-avatar"); img.src = c.url; img.alt = c.fallback || "";
      img.onerror = () => img.replaceWith(fb());   // broken URL -> initials
      return img;
    }
    case "Image": {
      const ph = () => { const d = el("div", "ui-image-ph"); d.textContent = c.alt || ""; return d; };
      if (!c.url) return ph();
      const img = el("img", "ui-image"); img.src = c.url; img.alt = c.alt || "";
      img.onerror = () => img.replaceWith(ph());   // broken URL -> gradient placeholder
      return img;
    }
    case "Progress": {
      const w = el("div", "ui-field");
      if (c.label) w.appendChild(el("label", "ui-label", c.label));
      const bar = el("div", "ui-progress"); const fill = el("div", "ui-progress-fill");
      fill.style.width = Math.max(0, Math.min(100, +c.value || 0)) + "%"; bar.appendChild(fill);
      w.appendChild(bar); return w;
    }
    case "Skeleton": {
      const w = el("div", "ui-stack gap-sm"); const n = c.lines || 3;
      for (let i = 0; i < n; i++) { const s = el("div", "ui-skel"); s.style.width = (i === n - 1 ? 60 : 100) + "%"; w.appendChild(s); }
      return w;
    }
    case "Tooltip": { const s = el("span", "ui-tooltip", c.label || ""); s.title = c.tip || c.text || ""; return s; }
    case "Alert": {
      const a = el("div", "ui-alert v-" + (c.tone || "default"));
      if (c.title) a.appendChild(el("div", "ui-alert-title", c.title));
      const adesc = c.description || c.message || strChild(c);
      if (adesc) a.appendChild(el("div", "ui-alert-desc", adesc));
      return a;
    }
    case "Metric": {
      const card = el("div", "ui-card ui-metric");
      const row = el("div", "ui-row"); row.dataset.justify = "between"; row.dataset.align = "start";
      const left = el("div", "ui-stack gap-sm");
      left.appendChild(el("div", "ui-metric-label", c.label || ""));
      const desc = bindVal(c.description);
      if (desc) left.appendChild(el("div", "ui-metric-desc", desc));
      left.appendChild(el("div", "ui-metric-value", bindVal(c.value) ?? ""));
      const delta = bindVal(c.delta);
      if (delta) {
        left.appendChild(el("span", "ui-metric-delta " + (c.deltaTone || "up"),
          (c.deltaTone === "down" ? "▾ " : "▴ ") + delta));
      }
      row.appendChild(left);
      if (c.chart) row.appendChild(node(c.chart, byId));
      card.appendChild(row); return card;
    }

    /* ---- composite ---- */
    case "Tabs": {
      const wrap = el("div", "ui-tabs"); const bar = el("div", "ui-tablist"); const body = el("div", "ui-tabbody");
      const tabs = c.tabs || [];
      tabs.forEach((t, i) => {
        const tb = el("button", "ui-tab" + (i === 0 ? " active" : ""), t.label || "Tab " + (i + 1));
        tb.onclick = () => {
          bar.querySelectorAll(".ui-tab").forEach((x) => x.classList.remove("active")); tb.classList.add("active");
          body.innerHTML = ""; renderArr(t.children || t.content, byId).forEach((n) => body.appendChild(n));
        };
        bar.appendChild(tb);
      });
      wrap.appendChild(bar); wrap.appendChild(body);
      if (tabs[0]) renderArr(tabs[0].children || tabs[0].content, byId).forEach((n) => body.appendChild(n));
      return wrap;
    }
    case "Accordion": {
      const wrap = el("div", "ui-accordion");
      (c.items || []).forEach((it) => {
        const item = el("div", "ui-acc-item"); const head = el("button", "ui-acc-head", it.title || "");
        const panel = el("div", "ui-acc-panel");
        renderArr(it.children || it.content, byId).forEach((n) => panel.appendChild(n));
        panel.style.display = "none";
        head.onclick = () => { panel.style.display = panel.style.display === "none" ? "" : "none"; head.classList.toggle("open"); };
        item.appendChild(head); item.appendChild(panel); wrap.appendChild(item);
      });
      return wrap;
    }
    case "Table": {
      const t = el("table", "ui-table");
      if (c.columns) {
        const tr = el("tr"); c.columns.forEach((h) => tr.appendChild(el("th", null, h)));
        const thead = el("thead"); thead.appendChild(tr); t.appendChild(thead);
      }
      const tb = el("tbody");
      (c.rows || []).forEach((row) => {
        const tr = el("tr");
        (Array.isArray(row) ? row : Object.values(row)).forEach((cell) => tr.appendChild(el("td", null, String(cell))));
        tb.appendChild(tr);
      });
      t.appendChild(tb); return t;
    }

    /* ---- charts ---- */
    case "BarChart": return barChart(c);
    case "HBar": case "HorizontalBar": return hbarChart(c);
    case "LineChart": return lineChart(c);
    case "AreaChart": return lineChart(Object.assign({}, c, { area: true }));
    case "Donut": case "PieChart": return donut(c);
    case "Gauge": case "Radial": return gauge(c);
    case "Sparkline": return sparkline(c);

    default: return el("div", "ui-unknown", c.component || "unknown");
  }
}

/* ================= charts ================= */
const PALETTE = ["#4f8df6", "#3fb27f", "#e5a13a", "#5cb85c", "#ef4444", "#06b6d4", "#a855f7"];

// ---- interactive hover tooltip shared by every chart ----
function chartTip() {
  let t = document.getElementById("ui-chart-tip");
  if (!t) {
    t = document.createElement("div"); t.id = "ui-chart-tip";
    t.style.cssText = "position:fixed;z-index:9999;pointer-events:none;opacity:0;transition:opacity .08s;" +
      "background:#1b2129;color:#fff;font:500 12px/1.4 var(--sans,system-ui,sans-serif);padding:6px 9px;" +
      "border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,.22);white-space:nowrap;transform:translate(-50%,-124%)";
    document.body.appendChild(t);
  }
  return t;
}
function tipShow(html, x, y) { const t = chartTip(); t.innerHTML = html; t.style.left = x + "px"; t.style.top = y + "px"; t.style.opacity = "1"; }
function tipHide() { const t = document.getElementById("ui-chart-tip"); if (t) t.style.opacity = "0"; }
// wire hover on an SVG element: floating tooltip + optional highlight/restore
function bindTip(elm, htmlFn, onIn, onOut) {
  elm.style.cursor = "pointer";
  elm.addEventListener("mousemove", (e) => tipShow(htmlFn(), e.clientX, e.clientY));
  elm.addEventListener("mouseenter", (e) => { if (onIn) onIn(); tipShow(htmlFn(), e.clientX, e.clientY); });
  elm.addEventListener("mouseleave", () => { if (onOut) onOut(); tipHide(); });
}
function tipRow(name, label, valueStr, color) {
  return (color ? '<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:' + color + ';margin-right:6px"></span>' : "") +
    (label != null && label !== "" ? '<span style="opacity:.7">' + label + '</span>  ' : "") +
    (name ? '<b>' + name + '</b>: ' : "") + '<b>' + valueStr + '</b>';
}

function fmt(kind) {
  if (kind === "currency") return (v) => "$" + (Math.round(v * 100) / 100).toLocaleString();
  if (kind === "percent") return (v) => v + "%";
  return (v) => "" + (Math.round(v * 100) / 100).toLocaleString();
}
function niceStep(max, target) {
  const raw = max / (target || 5);
  const p = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  const n = raw / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}
function normalize(c) {
  // -> { labels:[], series:[{name,color,data:[num]}] }.  data/series/values may all be state-bound.
  let series = bindVal(c.series);
  if (series) {
    series = series.map((se) => ({
      name: se.name, color: se.color,
      data: (bindVal(se.data) || se.data || []).map((v) => +bindVal(v) || 0),
    }));
    return { labels: bindVal(c.labels) || [], series };
  }
  const data = bindVal(c.data) || [];
  return { labels: data.map((d) => d.label ?? ""), series: [{ name: c.seriesName || "", data: data.map((d) => +bindVal(d.value) || 0) }] };
}
function svgEl(tag, attrs) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}
function chartFrame(c, build) {
  const wrap = el("div", "ui-chart");
  if (c.title) wrap.appendChild(el("div", "ui-chart-title", c.title));
  if (c.subtitle) wrap.appendChild(el("div", "ui-chart-sub", c.subtitle));
  const { svg, series } = build();
  wrap.appendChild(svg);
  if (series.length > 1 || (series[0] && series[0].name)) {
    const leg = el("div", "ui-legend");
    series.forEach((s, i) => {
      const item = el("span", "ui-legend-item");
      const sw = el("span", "ui-legend-sw"); sw.style.background = s.color || PALETTE[i % PALETTE.length];
      item.appendChild(sw); item.append(s.name || "Series " + (i + 1)); leg.appendChild(item);
    });
    wrap.appendChild(leg);
  }
  return wrap;
}
function axes(svg, W, H, padL, padT, plotW, plotH, maxV, format) {
  const step = niceStep(maxV, 5);
  const top = Math.ceil(maxV / step) * step || step;
  const f = fmt(format);
  for (let v = 0; v <= top + 1e-9; v += step) {
    const y = padT + plotH - (v / top) * plotH;
    svg.appendChild(svgEl("line", { x1: padL, y1: y, x2: padL + plotW, y2: y, class: "chart-grid" }));
    const t = svgEl("text", { x: padL - 8, y: y + 3, "text-anchor": "end", class: "chart-tick" });
    t.textContent = f(v); svg.appendChild(t);
  }
  return top;
}
function barChart(c) {
  return chartFrame(c, () => {
    const { labels, series } = normalize(c);
    const stacked = c.stacked !== false; // default stacked for multi-series
    const W = 560, H = 260, padL = 44, padR = 10, padT = 10, padB = 30;
    const plotW = W - padL - padR, plotH = H - padT - padB, n = Math.max(labels.length, ...series.map((s) => s.data.length), 1);
    let maxV = 0;
    for (let i = 0; i < n; i++) {
      if (stacked) maxV = Math.max(maxV, series.reduce((s, se) => s + (+se.data[i] || 0), 0));
      else series.forEach((se) => (maxV = Math.max(maxV, +se.data[i] || 0)));
    }
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg" });
    const top = axes(svg, W, H, padL, padT, plotW, plotH, maxV || 1, c.format);
    const band = plotW / n;
    for (let i = 0; i < n; i++) {
      const f = fmt(c.format);
      const bar = (attrs, se, val) => {
        const r = svgEl("rect", attrs);
        const color = attrs.fill;
        bindTip(r, () => tipRow(se.name, labels[i] ?? "", f(val), color),
          () => r.setAttribute("opacity", "0.82"), () => r.removeAttribute("opacity"));
        svg.appendChild(r);
      };
      if (stacked) {
        let acc = 0;
        const bw = band * 0.5, x = padL + i * band + (band - bw) / 2;
        series.forEach((se, si) => {
          const val = +se.data[i] || 0; const h = (val / top) * plotH;
          const y = padT + plotH - (acc + val) / top * plotH;
          bar({ x, y, width: bw, height: h, fill: se.color || PALETTE[si % PALETTE.length] }, se, val);
          acc += val;
        });
      } else {
        const gw = band * 0.7 / series.length;
        series.forEach((se, si) => {
          const val = +se.data[i] || 0; const h = (val / top) * plotH;
          const x = padL + i * band + band * 0.15 + si * gw;
          bar({ x, y: padT + plotH - h, width: gw * 0.9, height: h, rx: 2, fill: se.color || PALETTE[si % PALETTE.length] }, se, val);
        });
      }
      const lab = svgEl("text", { x: padL + i * band + band / 2, y: H - 8, "text-anchor": "middle", class: "chart-lbl" });
      lab.textContent = labels[i] ?? ""; svg.appendChild(lab);
    }
    return { svg, series };
  });
}
function lineChart(c) {
  return chartFrame(c, () => {
    const { labels, series } = normalize(c);
    const W = 560, H = 260, padL = 44, padR = 10, padT = 10, padB = 30;
    const plotW = W - padL - padR, plotH = H - padT - padB, n = Math.max(labels.length, ...series.map((s) => s.data.length), 1);
    let maxV = 0; series.forEach((se) => se.data.forEach((v) => (maxV = Math.max(maxV, +v || 0))));
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg" });
    const top = axes(svg, W, H, padL, padT, plotW, plotH, maxV || 1, c.format);
    const step = plotW / Math.max(1, n - 1);
    const f = fmt(c.format);
    const baseY = padT + plotH;
    series.forEach((se, si) => {
      const color = se.color || PALETTE[si % PALETTE.length];
      const xy = se.data.map((v, i) => [padL + i * step, padT + plotH - (+v || 0) / top * plotH]);
      const pts = xy.map((p) => p.join(",")).join(" ");
      // AreaChart: soft gradient fill under the line
      if (c.area && xy.length) {
        const gid = "ag" + Math.random().toString(36).slice(2);
        const defs = svgEl("defs", {}); const grad = svgEl("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 });
        grad.appendChild(svgEl("stop", { offset: "0%", "stop-color": color, "stop-opacity": 0.28 }));
        grad.appendChild(svgEl("stop", { offset: "100%", "stop-color": color, "stop-opacity": 0.02 }));
        defs.appendChild(grad); svg.appendChild(defs);
        const area = pts + " " + xy[xy.length - 1][0] + "," + baseY + " " + xy[0][0] + "," + baseY;
        svg.appendChild(svgEl("polygon", { points: area, fill: "url(#" + gid + ")", stroke: "none" }));
      }
      svg.appendChild(svgEl("polyline", { points: pts, fill: "none", stroke: color, "stroke-width": 2.5, "stroke-linejoin": "round" }));
      xy.forEach(([x, y], i) => {
        const dot = svgEl("circle", { cx: x, cy: y, r: 3, fill: "var(--card,#fff)", stroke: color, "stroke-width": 2 });
        bindTip(dot, () => tipRow(se.name, labels[i] ?? "", f(+se.data[i] || 0), color),
          () => dot.setAttribute("r", "5.5"), () => dot.setAttribute("r", "3"));
        svg.appendChild(dot);
      });
    });
    labels.forEach((l, i) => {
      const lab = svgEl("text", { x: padL + i * step, y: H - 8, "text-anchor": "middle", class: "chart-lbl" });
      lab.textContent = l ?? ""; svg.appendChild(lab);
    });
    return { svg, series };
  });
}
function donut(c) {
  return chartFrame(c, () => {
    const data = c.data || [];
    const size = 170, r = 58, cx = size / 2, cy = size / 2, C = 2 * Math.PI * r;
    const total = data.reduce((s, d) => s + (+d.value || 0), 0) || 1;
    const svg = svgEl("svg", { viewBox: `0 0 ${size} ${size}`, class: "chart-svg chart-donut" });
    const f = fmt(c.format);
    let off = 0;
    data.forEach((d, i) => {
      const frac = (+d.value || 0) / total;
      const color = d.color || PALETTE[i % PALETTE.length];
      const seg = svgEl("circle", {
        cx, cy, r, fill: "none", stroke: color, "stroke-width": 20,
        "stroke-dasharray": `${frac * C} ${C}`, "stroke-dashoffset": -off * C, transform: `rotate(-90 ${cx} ${cy})`,
      });
      bindTip(seg, () => tipRow(d.label || "", "", f(+d.value || 0) + " · " + Math.round(frac * 100) + "%", color),
        () => seg.setAttribute("stroke-width", "24"), () => seg.setAttribute("stroke-width", "20"));
      svg.appendChild(seg);
      off += frac;
    });
    if (c.center) {
      const t = svgEl("text", { x: cx, y: cy + 5, "text-anchor": "middle", class: "chart-center" });
      t.textContent = c.center; svg.appendChild(t);
    }
    const series = data.map((d, i) => ({ name: d.label, color: PALETTE[i % PALETTE.length] }));
    return { svg, series };
  });
}

function hbarChart(c) {
  return chartFrame(c, () => {
    const { labels, series } = normalize(c);
    const se = series[0] || { data: [] }, data = se.data;
    const n = Math.max(labels.length, data.length, 1);
    const W = 560, rowH = 34, padL = 116, padR = 54, padT = 6, H = padT * 2 + n * rowH;
    const maxV = Math.max(1, ...data.map((v) => +v || 0)), plotW = W - padL - padR;
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg" });
    const f = fmt(c.format);
    for (let i = 0; i < n; i++) {
      const val = +data[i] || 0, w = (val / maxV) * plotW, y = padT + i * rowH + 4, bh = rowH - 14;
      const lab = svgEl("text", { x: padL - 12, y: y + bh / 2 + 4, "text-anchor": "end", class: "chart-lbl" });
      lab.textContent = labels[i] ?? ""; svg.appendChild(lab);
      svg.appendChild(svgEl("rect", { x: padL, y, width: plotW, height: bh, rx: 5, fill: "var(--muted,#eee)", opacity: 0.5 }));
      const color = se.color || PALETTE[i % PALETTE.length];
      const bar = svgEl("rect", { x: padL, y, width: Math.max(2, w), height: bh, rx: 5, fill: color });
      bindTip(bar, () => tipRow("", labels[i] ?? "", f(val), color), () => bar.setAttribute("opacity", "0.82"), () => bar.removeAttribute("opacity"));
      svg.appendChild(bar);
      const vt = svgEl("text", { x: padL + w + 8, y: y + bh / 2 + 4, class: "chart-tick" }); vt.textContent = f(val); svg.appendChild(vt);
    }
    return { svg, series: [] };
  });
}
function gauge(c) {
  return chartFrame(c, () => {
    const val = +bindVal(c.value) || 0, max = +c.max || 100, frac = Math.max(0, Math.min(1, max ? val / max : 0));
    const size = 180, r = 64, cx = size / 2, cy = size / 2, C = 2 * Math.PI * r, color = c.color || PALETTE[0];
    const svg = svgEl("svg", { viewBox: `0 0 ${size} ${size}`, class: "chart-svg chart-donut" });
    svg.appendChild(svgEl("circle", { cx, cy, r, fill: "none", stroke: "var(--muted,#eee)", "stroke-width": 16, opacity: 0.5 }));
    const ring = svgEl("circle", { cx, cy, r, fill: "none", stroke: color, "stroke-width": 16, "stroke-linecap": "round",
      "stroke-dasharray": `${frac * C} ${C}`, transform: `rotate(-90 ${cx} ${cy})` });
    bindTip(ring, () => tipRow(c.label || "", "", fmt(c.format)(val) + " / " + max),
      () => ring.setAttribute("stroke-width", "19"), () => ring.setAttribute("stroke-width", "16"));
    svg.appendChild(ring);
    const t = svgEl("text", { x: cx, y: cy + 2, "text-anchor": "middle", class: "chart-center" });
    t.textContent = (c.centerLabel != null ? c.centerLabel : Math.round(frac * 100) + "%"); svg.appendChild(t);
    if (c.label) { const s = svgEl("text", { x: cx, y: cy + 24, "text-anchor": "middle", class: "chart-lbl" }); s.textContent = c.label; svg.appendChild(s); }
    return { svg, series: [] };
  });
}
function sparkline(c) {
  const wrap = el("div", "ui-chart");
  const data = (bindVal(c.data) || c.values || []).map((v) => +bindVal(v && v.value != null ? v.value : v) || 0);
  const W = 160, H = 40, pad = 3, color = c.color || PALETTE[0];
  const max = Math.max(1, ...data), min = Math.min(0, ...data), rng = (max - min) || 1;
  const step = (W - pad * 2) / Math.max(1, data.length - 1);
  const pts = data.map((v, i) => `${pad + i * step},${H - pad - ((v - min) / rng) * (H - pad * 2)}`).join(" ");
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg" }); svg.style.height = "40px";
  svg.appendChild(svgEl("polyline", { points: pts, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  wrap.appendChild(svg); return wrap;
}

function toast(msg) {
  let t = document.getElementById("toast");
  if (!t) { t = el("div"); t.id = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show");
  clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove("show"), 1800);
}

window.renderA2UI = renderA2UI;
// Strict by default; the playground opts into lenient salvage behaviors.
window.setRendererLenient = (v) => { LENIENT = !!v; };
window.isRendererLenient = () => LENIENT;
