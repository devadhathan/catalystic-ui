// Additive Design-System Agent (Import modal) + toggle integrator.
// - Injects imported design systems into the shadcn|Polaris dropdown (🧪 = experimental).
// - Renders the full-cycle agent into #tab-agent, which lives inside the Import modal
//   (#ag-modal, opened by ⚡ Import): paste Figma link / tokens -> "tokens you had"
//   vs "converted (W3C->CSS)" -> Save.
// Imported systems render through the (untouched) shadcn renderer + a skin on the
// newest inline chat surface (window.__cat.surface).
(function () {
  const SYSTEMS = [
    { id: "material", name: "Material 3",
      vars: { "--primary": "#6750A4", "--chart-1": "#6750A4", "--primary-foreground": "#fff", "--background": "#fef7ff",
        "--card": "#fff", "--foreground": "#1d1b20", "--muted": "#f3edf7", "--muted-foreground": "#49454f",
        "--border": "#cac4d0", "--input": "#cac4d0", "--radius": "16px" },
      css: `[data-skin=material] .ui-btn{border-radius:20px;font-weight:500}
            [data-skin=material] .ui-card{border-radius:16px;box-shadow:0 1px 3px rgba(0,0,0,.12)}
            [data-skin=material] *{font-family:Roboto,ui-sans-serif,system-ui,sans-serif}` },
    { id: "carbon", name: "IBM Carbon",
      vars: { "--primary": "#0f62fe", "--chart-1": "#0f62fe", "--primary-foreground": "#fff", "--background": "#fff",
        "--card": "#fff", "--foreground": "#161616", "--muted": "#f4f4f4", "--muted-foreground": "#525252",
        "--border": "#e0e0e0", "--input": "#e0e0e0", "--radius": "0px" },
      css: `[data-skin=carbon] .ui-btn,[data-skin=carbon] .ui-card,[data-skin=carbon] .ui-input{border-radius:0}
            [data-skin=carbon] *{font-family:'IBM Plex Sans',ui-sans-serif,system-ui,sans-serif}` },
    // brand-flavoured systems — approximations of these products' look (colour, font, radius)
    { id: "perplexity", name: "Perplexity", label: "Perplexity",
      vars: { "--primary": "#20808d", "--chart-1": "#20808d", "--primary-foreground": "#fff", "--background": "#fcfcfa",
        "--card": "#ffffff", "--foreground": "#091717", "--muted": "#f0f3f3", "--muted-foreground": "#4a5a5a",
        "--border": "#e2e7e7", "--input": "#e2e7e7", "--radius": "8px" },
      css: `@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&display=swap');
            [data-skin=perplexity] *{font-family:'Space Grotesk',ui-sans-serif,system-ui,sans-serif}
            [data-skin=perplexity] .ui-btn{border-radius:8px;font-weight:500}
            [data-skin=perplexity] .ui-card{border-radius:12px}` },
    { id: "claude", name: "Claude", label: "Claude",
      vars: { "--primary": "#c96442", "--chart-1": "#c96442", "--primary-foreground": "#fff", "--background": "#faf9f5",
        "--card": "#ffffff", "--foreground": "#141413", "--muted": "#f0eee6", "--muted-foreground": "#6b6a63",
        "--border": "#e6e2d8", "--input": "#e6e2d8", "--radius": "10px" },
      css: `@import url('https://fonts.googleapis.com/css2?family=Lora:wght@500;600&family=Inter:wght@400;500;600&display=swap');
            [data-skin=claude] *{font-family:'Inter',ui-sans-serif,system-ui,sans-serif}
            [data-skin=claude] .ui-text.v-title,[data-skin=claude] .ui-text.v-subtitle{font-family:'Lora',Georgia,serif;letter-spacing:-.01em}
            [data-skin=claude] .ui-btn{border-radius:9px;font-weight:500}
            [data-skin=claude] .ui-card{border-radius:12px}` },
    { id: "chatgpt", name: "ChatGPT", label: "ChatGPT",
      vars: { "--primary": "#10a37f", "--chart-1": "#10a37f", "--primary-foreground": "#fff", "--background": "#ffffff",
        "--card": "#ffffff", "--foreground": "#0d0d0d", "--muted": "#f7f7f8", "--muted-foreground": "#6e6e80",
        "--border": "#e5e5e5", "--input": "#e5e5e5", "--radius": "14px" },
      css: `@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
            [data-skin=chatgpt] *{font-family:'Inter',ui-sans-serif,system-ui,sans-serif}
            [data-skin=chatgpt] .ui-btn{border-radius:999px;font-weight:500}
            [data-skin=chatgpt] .ui-card{border-radius:16px}
            [data-skin=chatgpt] .ui-input{border-radius:12px}` },
    { id: "grok", name: "Grok", label: "Grok",
      vars: { "--primary": "#111113", "--chart-1": "#6366f1", "--primary-foreground": "#fff", "--background": "#ffffff",
        "--card": "#ffffff", "--foreground": "#0a0a0a", "--muted": "#f4f4f5", "--muted-foreground": "#5a5a5e",
        "--border": "#e4e4e7", "--input": "#e4e4e7", "--radius": "4px" },
      css: `@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600&display=swap');
            [data-skin=grok] *{font-family:'Inter',ui-sans-serif,system-ui,sans-serif}
            [data-skin=grok] .ui-text.v-title,[data-skin=grok] .ui-btn{font-family:'JetBrains Mono',ui-monospace,monospace;letter-spacing:-.02em}
            [data-skin=grok] .ui-btn{border-radius:6px;font-weight:500}
            [data-skin=grok] .ui-card{border-radius:6px}` },
  ];
  const VARKEYS = ["--primary","--primary-foreground","--background","--card","--foreground",
    "--muted","--muted-foreground","--border","--input","--radius"];

  // ---- conversion (JS port of src/figma_to_designsystem.py) ----
  const RULES = [
    [["on-brand","primary-foreground","on-primary"], ["--primary-foreground"]],
    [["brand","primary","accent"], ["--primary"]],
    [["background","canvas"], ["--background"]],
    [["surface","card"], ["--card"]],
    [["muted-text","text-secondary","subtle-text"], ["--muted-foreground"]],
    [["muted","subtle"], ["--muted"]],
    [["text","foreground","ink"], ["--foreground"]],
    [["border","outline","stroke"], ["--border","--input"]],
    [["radius","radii","corner","rounded"], ["--radius"]],
  ];
  // fuzzy: does a token name/path map to renderer CSS var(s)?
  function mapVar(name) {
    const n = String(name).toLowerCase();
    for (const [keys, t] of RULES) if (keys.some((k) => n.includes(k))) return t;
    return null;
  }
  const hx = (n) => Math.max(0, Math.min(255, Math.round((n || 0) * 255))).toString(16).padStart(2, "0");
  const hexof = (c) => "#" + hx(c.r) + hx(c.g) + hx(c.b);
  function figmaResolved(figma) {
    const meta = figma.meta || figma, colls = meta.variableCollections || {};
    const dm = (Object.values(colls)[0] || {}).defaultModeId, rows = [];
    for (const v of Object.values(meta.variables || {})) {
      const modes = v.valuesByMode || {}, raw = dm in modes ? modes[dm] : Object.values(modes)[0];
      let val = raw;
      if (v.resolvedType === "COLOR" && raw && typeof raw === "object") val = hexof(raw);
      else if (v.resolvedType === "FLOAT") val = raw + "px";
      rows.push([v.name, String(val)]);
    }
    return rows;
  }
  function figmaToVars(figma) {
    const meta = figma.meta || figma, colls = meta.variableCollections || {};
    const dm = (Object.values(colls)[0] || {}).defaultModeId, vars = {};
    for (const v of Object.values(meta.variables || {})) {
      const n = v.name.toLowerCase(); let targets = null;
      for (const [keys, t] of RULES) if (keys.some((k) => n.includes(k))) { targets = t; break; }
      if (!targets) continue;
      const modes = v.valuesByMode || {}, raw = dm in modes ? modes[dm] : Object.values(modes)[0];
      let val; if (v.resolvedType === "COLOR" && raw && typeof raw === "object") val = hexof(raw);
      else if (v.resolvedType === "FLOAT") val = raw + "px"; else continue;
      targets.forEach((t) => { if (!(t in vars)) vars[t] = val; });
    }
    return vars;
  }
  // flatten a token tree to {path: value}. Handles W3C DTCG ($value) AND
  // Tokens Studio / Style Dictionary (value / type without the $).
  function flat(o, p, out) {
    for (const k in o) {
      if (k[0] === "$") continue;
      const path = p ? p + "." + k : k, v = o[k];
      if (v && typeof v === "object") {
        if ("$value" in v) out[path] = v["$value"];
        else if ("value" in v && typeof v.value !== "object") out[path] = v.value;
        else flat(v, path, out);
      }
    }
    return out;
  }
  // exact leaf-name → renderer var(s). Covers Ant Design, shadcn and common token names.
  const LEAF_MAP = {
    colorprimary: ["--primary"], primary: ["--primary"], brand: ["--primary"], accent: ["--primary"],
    colorprimarytext: ["--primary-foreground"], colortextlightsolid: ["--primary-foreground"],
    colorwhite: ["--primary-foreground"], onprimary: ["--primary-foreground"], primaryforeground: ["--primary-foreground"],
    colorbglayout: ["--background"], colorbgbase: ["--background"], background: ["--background"], canvas: ["--background"],
    colorbgcontainer: ["--card"], colorbgelevated: ["--card"], surface: ["--card"], card: ["--card"],
    colortext: ["--foreground"], colortextbase: ["--foreground"], foreground: ["--foreground"], text: ["--foreground"],
    colortextsecondary: ["--muted-foreground"], colortexttertiary: ["--muted-foreground"], mutedforeground: ["--muted-foreground"],
    colorfillsecondary: ["--muted"], colorfilltertiary: ["--muted"], colorfill: ["--muted"], muted: ["--muted"],
    colorborder: ["--border", "--input"], colorbordersecondary: ["--border", "--input"], border: ["--border", "--input"],
    borderradius: ["--radius"], borderradiuslg: ["--radius"], radius: ["--radius"],
  };
  const norm = (s) => String(s).toLowerCase().replace(/[^a-z0-9]/g, "");
  // resolve {a.b.c} alias values against the flattened token map (a few hops)
  function resolveRefs(f) {
    const key = (ref) => ref.replace(/[{}]/g, "").trim();
    for (let pass = 0; pass < 4; pass++) {
      let changed = false;
      for (const k in f) {
        const v = f[k];
        if (typeof v === "string" && /^\{.+\}$/.test(v)) {
          const t = f[key(v)];
          if (t !== undefined && t !== v) { f[k] = t; changed = true; }
        }
      }
      if (!changed) break;
    }
    return f;
  }
  // DTCG / Style-Dictionary / Tokens-Studio / Ant → renderer vars (exact names, then fuzzy).
  function dtcg(json) {
    const f = resolveRefs(flat(json, "", {}));
    const rows = Object.entries(f).map(([k, v]) => [k, String(v)]);
    const vars = {}, exact = {};
    const put = (t, val, isExact) => {
      if ((exact[t] || vars[t]) && !isExact) return;
      if (t === "--radius") { if (/^[\d.]/.test(val)) { vars[t] = /px|rem|em|%/.test(val) ? val : val + "px"; if (isExact) exact[t] = 1; } }
      else if (/^#|^rgb|^hsl/i.test(val)) { vars[t] = val; if (isExact) exact[t] = 1; }
    };
    for (const [path, val] of Object.entries(f)) {   // pass 1: exact leaf names
      const t = LEAF_MAP[norm(path.split(".").pop())];
      if (t) t.forEach((x) => put(x, String(val), true));
    }
    for (const [path, val] of Object.entries(f)) {   // pass 2: fuzzy fill
      const t = mapVar(path);
      if (t) t.forEach((x) => put(x, String(val), false));
    }
    return { rows, vars };
  }

  // ---- skin registry ----
  // The generated UI renders inline in the chat; index.html (window.__cat.applyDS) is the
  // single source of truth for the selected design system and applies skins to every
  // surface via window.__skins after each render. Here we just register options + vars.
  const registry = {};
  function register(sys) {
    registry[sys.id] = sys;
    if (sys.css && !document.getElementById("skin-css-" + sys.id)) {
      const st = document.createElement("style"); st.id = "skin-css-" + sys.id; st.textContent = sys.css; document.head.appendChild(st);
    }
    // add to every design-system picker: header (source of truth), chat-tools box, agent UI tab
    ["ds-select", "ct-ds", "ap-ds"].forEach((sid) => {
      const sel = document.getElementById(sid);
      if (sel && !sel.querySelector('option[value="' + sys.id + '"]')) {
        const o = document.createElement("option"); o.value = sys.id; o.textContent = sys.label || (sys.name + " (imported)"); sel.appendChild(o);
      }
    });
  }
  // consumed by index.html renderCurrent(): apply / clear the imported skin on a surface.
  window.__skins = {
    get: (id) => registry[id],
    apply(surface, id) {
      if (!surface) return;
      const sys = registry[id];
      VARKEYS.forEach((k) => surface.style.removeProperty(k));
      if (!sys) { delete surface.dataset.skin; return; }
      for (const k in sys.vars) surface.style.setProperty(k, sys.vars[k]);
      surface.dataset.skin = sys.id;
    },
    clear(surface) {
      if (!surface) return;
      VARKEYS.forEach((k) => surface.style.removeProperty(k));
      delete surface.dataset.skin;
    },
  };

  // ---- Agent tab (rendered into #tab-agent) ----
  let pending = null;
  function status(t) { const s = document.getElementById("ag-status"); if (s) s.textContent = t; }
  function renderColumns(fromRows, vars) {
    const tok = (n, v) => `<div class="ag-tok"><span class="sw" style="background:${/^#/.test(v) ? v : "transparent"}"></span><span class="nm">${n}</span><span class="vl">${v}</span></div>`;
    document.getElementById("ag-from").innerHTML = fromRows.map(([n, v]) => tok(n, v)).join("") || '<div class="ag-empty">—</div>';
    document.getElementById("ag-to").innerHTML = Object.entries(vars).map(([k, v]) => tok(k, v)).join("") || '<div class="ag-empty">—</div>';
    pending = { vars };
    const res = document.getElementById("ag-result"); if (res) res.hidden = false;   // reveal preview + save once there's a result
    document.getElementById("ag-save").disabled = !Object.keys(vars).length;
  }
  // ---- turn a partial palette into a COMPLETE, contrast-safe theme ----
  // A raw import often maps only a few vars (e.g. a dark fg + bg) and leaves the rest at their light
  // defaults — which renders white text on a white surface. This fills every renderer var coherently,
  // in one consistent light/dark polarity, and guarantees foreground/background contrast.
  function _hex(c) {
    if (!c) return null;
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})/i.exec(String(c).trim());
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : null;
  }
  function _lum(c) { const r = _hex(c); if (!r) return null; return (0.2126 * r[0] + 0.7152 * r[1] + 0.0722 * r[2]) / 255; }
  function _mix(a, b, t) {
    const x = _hex(a), y = _hex(b); if (!x || !y) return a;
    const p = (i) => Math.round(x[i] + (y[i] - x[i]) * t).toString(16).padStart(2, "0");
    return "#" + p(0) + p(1) + p(2);
  }
  function normalizeTheme(vars) {
    const v = Object.assign({}, vars);
    let bg = v["--background"] || v["--card"] || "#ffffff";
    let fg = v["--foreground"] || "#0b0f14";
    const lbg = _lum(bg), lfg = _lum(fg);
    const dark = lbg != null ? lbg < 0.45 : (lfg != null ? lfg > 0.55 : false);
    // guarantee fg/bg contrast — never white-on-white / black-on-black
    if (lbg != null && lfg != null && Math.abs(lbg - lfg) < 0.35) fg = dark ? "#f4f6f8" : "#0b0f14";
    const toward = dark ? "#ffffff" : "#000000";      // the direction "elevated" surfaces shift
    v["--background"] = bg;
    v["--foreground"] = fg;
    v["--card"] = v["--card"] || (dark ? _mix(bg, toward, 0.08) : "#ffffff");
    v["--muted"] = dark ? _mix(bg, toward, 0.05) : (v["--muted"] || _mix(bg, toward, 0.035));
    v["--muted-foreground"] = v["--muted-foreground"] || _mix(fg, bg, 0.42);
    v["--secondary"] = v["--secondary"] || v["--muted"];
    v["--border"] = v["--border"] || _mix(bg, toward, dark ? 0.14 : 0.1);
    v["--input"] = v["--input"] || v["--border"];
    v["--primary"] = v["--primary"] || (dark ? "#f4f6f8" : "#111318");
    v["--primary-foreground"] = v["--primary-foreground"] || ((_lum(v["--primary"]) || 0) < 0.5 ? "#ffffff" : "#0b0f14");
    v["--ring"] = v["--ring"] || v["--primary"];
    return v;
  }

  function ingest(json) {
    let data;
    try { data = typeof json === "string" ? JSON.parse(json) : json; }
    catch (e) { status("⚠ Not valid JSON — " + e.message); return; }
    let rows, vars;
    if ((data.meta && data.meta.variables) || data.variables) { rows = figmaResolved(data); vars = figmaToVars(data); }
    else { const d = dtcg(data); rows = d.rows; vars = d.vars; }
    if (Object.keys(vars).length) vars = normalizeTheme(vars);   // complete + contrast-safe
    renderColumns(rows, vars);
    const nv = Object.keys(vars).length;
    if (nv) status("Found " + rows.length + " tokens · mapped " + nv + " to the renderer. Name it & Save →");
    else if (rows.length) status("Read " + rows.length + " tokens but couldn't map any. Expecting Figma Variables or DTCG/Tokens-Studio colours.");
    else status("No tokens found in that file. Is it a Figma Variables or design-tokens (DTCG) export?");
  }
  function buildAgent() {
    const host = document.getElementById("tab-agent"); if (!host) return;
    const css = `
    #tab-agent{color:var(--foreground);font:14px ui-sans-serif,system-ui}
    #tab-agent .ag-hd{display:flex;align-items:center;gap:11px;margin-bottom:18px}
    #tab-agent .ag-ico{width:36px;height:36px;border-radius:10px;flex:none;display:flex;align-items:center;justify-content:center;background:var(--muted);color:var(--foreground)}
    #tab-agent .ag-ico svg{width:18px;height:18px}
    #tab-agent .ag-title{font-size:16px;font-weight:650;letter-spacing:-.01em;line-height:1.2}
    #tab-agent .ag-desc{font-size:12.5px;color:var(--muted-foreground);margin-top:1px}
    #tab-agent .ag-bd{display:flex;flex-direction:column;gap:10px}
    #tab-agent .ag-row{display:flex;gap:8px}
    #tab-agent input,#tab-agent textarea{width:100%;border:1px solid var(--input);border-radius:10px;padding:0 13px;height:40px;font:inherit;font-size:13.5px;background:var(--card);color:var(--foreground)}
    #tab-agent input:focus,#tab-agent textarea:focus{outline:none;border-color:var(--ring);box-shadow:0 0 0 3px hsl(240 5% 65% / .15)}
    #tab-agent textarea{height:64px;padding:10px 13px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;resize:vertical}
    #tab-agent .go{height:40px;border-radius:10px;padding:0 16px;cursor:pointer;font:inherit;font-size:13.5px;font-weight:500;white-space:nowrap;display:inline-flex;align-items:center;justify-content:center;gap:6px;border:1px solid transparent}
    #tab-agent .go svg{width:14px;height:14px}
    #tab-agent .go.primary{background:var(--primary);color:var(--primary-foreground)}
    #tab-agent .go.outline{background:var(--card);color:var(--foreground);border-color:var(--border)}
    #tab-agent .go.outline:hover{border-color:var(--ring)}
    #tab-agent .go.wide{width:100%}
    #ag-status{color:var(--muted-foreground);font-size:12px;padding:2px 0}
    #tab-agent .ag-divider{display:flex;align-items:center;gap:10px;color:var(--muted-foreground);font-size:11px}
    #tab-agent .ag-divider::before,#tab-agent .ag-divider::after{content:"";flex:1;height:1px;background:var(--border)}
    #tab-agent .ag-cols{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    #tab-agent h5{margin:2px 0 6px;font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted-foreground)}
    #tab-agent .box{border:1px solid var(--border);border-radius:10px;padding:8px 10px;height:116px;overflow:auto;background:var(--card)}
    #tab-agent .ag-link{background:none;border:0;color:var(--muted-foreground);font:inherit;font-size:12px;cursor:pointer;text-decoration:underline;text-underline-offset:2px;align-self:flex-start;padding:0}
    #tab-agent .ag-link:hover{color:var(--foreground)}
    #tab-agent [hidden]{display:none}
    #tab-agent #ag-result{display:flex;flex-direction:column;gap:10px;margin-top:2px}
    .ag-tok{display:flex;align-items:center;gap:8px;padding:3px 0;font-size:11.5px}
    .ag-tok .sw{width:13px;height:13px;border-radius:4px;border:1px solid rgba(0,0,0,.12);flex:none}
    .ag-tok .nm{color:var(--muted-foreground);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .ag-tok .vl{margin-left:auto;color:var(--foreground);font-family:ui-monospace,monospace}
    .ag-empty{color:var(--muted-foreground);opacity:.6;font-size:12px;text-align:center;padding:16px}
    #tab-agent .ag-save-row{display:flex;gap:8px;margin-top:2px}
    #tab-agent .ag-save-row input{flex:1}
    #ag-save{height:40px;border:0;border-radius:10px;padding:0 18px;background:var(--primary);color:var(--primary-foreground);cursor:pointer;font:inherit;font-size:13.5px;font-weight:500;white-space:nowrap}
    #ag-save:disabled{opacity:.4;cursor:default}
    .ag-hint{color:var(--muted-foreground);font-size:11px;margin-top:6px;line-height:1.5}`;
    const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
    host.innerHTML = `
      <div class="ag-hd">
        <span class="ag-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 2 9 5-9 5-9-5 9-5z"/><path d="m3 12 9 5 9-5"/><path d="m3 17 9 5 9-5"/></svg></span>
        <div><div class="ag-title">Design system agent</div><div class="ag-desc">Turn a Figma or design-tokens export into a usable design system.</div></div>
      </div>
      <div class="ag-bd">
        <div class="ag-row"><input id="ag-url" placeholder="Paste a Figma file link…"/><button class="go primary" id="ag-import">Import</button></div>
        <input type="file" id="ag-file" accept=".json,application/json" style="display:none"/>
        <button class="go outline wide" id="ag-file-btn"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 8 5-5 5 5"/><path d="M5 21h14"/></svg>Upload a design-tokens JSON file</button>
        <button class="ag-link" id="ag-paste-toggle">or paste tokens JSON</button>
        <div id="ag-paste-wrap" hidden>
          <textarea id="ag-paste" placeholder="Figma Variables · W3C DTCG · Tokens-Studio JSON"></textarea>
          <button class="go outline wide" id="ag-convert" style="margin-top:8px">Convert pasted JSON</button>
        </div>
        <div id="ag-status">Link, upload, or paste a Figma / tokens file — the agent converts it to a design system.</div>
        <div id="ag-result" hidden>
          <div class="ag-cols">
            <div><h5>Tokens you had</h5><div class="box" id="ag-from"><div class="ag-empty">—</div></div></div>
            <div><h5>Converted → renderer</h5><div class="box" id="ag-to"><div class="ag-empty">—</div></div></div>
          </div>
          <div class="ag-save-row">
            <input id="ag-name" placeholder="Name this design system (e.g. Acme)"/>
            <button id="ag-save" disabled>Save to dropdown</button>
          </div>
        </div>
        <div class="ag-hint">A Figma link needs a FIGMA_TOKEN on the server. No token? Upload or paste the tokens JSON — that always works.</div>
      </div>`;
    document.getElementById("ag-paste-toggle").onclick = () => {
      const w = document.getElementById("ag-paste-wrap"), t = document.getElementById("ag-paste-toggle");
      const show = w.hidden; w.hidden = !show; t.hidden = show;
      if (show) document.getElementById("ag-paste").focus();
    };
    document.getElementById("ag-convert").onclick = () => ingest(document.getElementById("ag-paste").value);
    // upload a JSON file → read it, preview it, convert it
    const fileInput = document.getElementById("ag-file");
    document.getElementById("ag-file-btn").onclick = () => fileInput.click();
    fileInput.onchange = () => {
      const f = fileInput.files && fileInput.files[0]; if (!f) return;
      status("Reading " + f.name + "…");
      const rd = new FileReader();
      rd.onload = () => {
        document.getElementById("ag-paste").value = String(rd.result).slice(0, 4000);
        const nameEl = document.getElementById("ag-name");
        if (nameEl && !nameEl.value) nameEl.value = f.name.replace(/\.json$/i, "");
        ingest(rd.result);
      };
      rd.onerror = () => status("⚠ couldn't read that file");
      rd.readAsText(f);
      fileInput.value = "";   // allow re-uploading the same file
    };
    document.getElementById("ag-import").onclick = async () => {
      const url = document.getElementById("ag-url").value.trim(); if (!url) return;
      status("Agent fetching Figma variables…");
      try {
        const r = await fetch("/api/figma", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }) });
        const out = await r.json();
        if (out.error) { status("⚠ " + out.error); return; }
        ingest(out);
      } catch (e) { status("⚠ " + e.message); }
    };
    document.getElementById("ag-save").onclick = () => {
      if (!pending) return;
      const name = document.getElementById("ag-name").value.trim() || "Imported";
      const sys = { id: "custom-" + Date.now(), name, vars: pending.vars, css: "" };
      register(sys);
      const sel = document.getElementById("ds-select");
      if (sel) { sel.value = sys.id; }
      if (window.__cat && window.__cat.applyDS) window.__cat.applyDS(sys.id);   // apply now + persist
      const modal = document.getElementById("ag-modal"); if (modal) modal.hidden = true;  // close, reveal result
      status("Saved ✓ — “" + name + "” is now the active design system.");
    };
  }

  function init() {
    buildAgent();  // Figma agent lives in the Import modal (#ag-modal), opened by #ds-import-btn (wired in index.html)
    SYSTEMS.forEach(register);  // Material 3 + IBM Carbon appear as 🧪 options in #ds-select
    // #ds-select is the single source of truth; index.html (window.__cat.applyDS) applies the choice.
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
