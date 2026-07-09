// Real @shopify/polaris renderer for the abstract component tree.
// Exposes window.renderPolaris(components, mountEl). Built to ../web/polaris-bundle.js
// by `npm run build`. The SAME component JSON the shadcn renderer draws is mapped
// here onto genuine Polaris React components.
import React from "react";
import { createRoot } from "react-dom/client";
import {
  AppProvider, Card, BlockStack, InlineStack, InlineGrid, Text, Button, TextField,
  Select, Checkbox, RangeSlider, ChoiceList, Badge, Avatar, Banner, Tabs, DataTable,
  Divider, ProgressBar, SkeletonBodyText, Tooltip, Collapsible, Link, Thumbnail,
} from "@shopify/polaris";
import en from "@shopify/polaris/locales/en.json";
import "@shopify/polaris/build/esm/styles.css";

/* ---------- shared tree helpers (mirror the vanilla renderer) ---------- */
const StateCtx = React.createContext({ state: {}, set: () => {}, byId: {} });
const useDS = () => React.useContext(StateCtx);

function deepWalk(o, cb) {
  if (Array.isArray(o)) o.forEach((x) => deepWalk(x, cb));
  else if (o && typeof o === "object") { cb(o); for (const k in o) deepWalk(o[k], cb); }
}
function buildState(list) {
  const s = {};
  deepWalk(list, (o) => { if (o && o.stateKey != null && o.component) s[o.stateKey] = o.value; });
  return s;
}
function bindVal(v, state) {
  if (v && typeof v === "object" && v.bind && v.bind.map) {
    const m = v.bind.map, key = v.bind.key;
    if (key in state && state[key] in m) return m[state[key]];
    return m[Object.keys(m)[0]];
  }
  return v;
}
function kidsOf(c, byId) {
  let ks = c.children || (c.child ? [c.child] : []);
  if (!Array.isArray(ks)) ks = [ks];
  return ks.map((k) => (typeof k === "string" ? byId[k] : k)).filter(Boolean);
}
const GAP = { sm: "200", md: "400", lg: "600" };
const opt = (o) => (typeof o === "object" ? { label: o.label, value: String(o.value ?? o.label) } : { label: String(o), value: String(o) });

/* ---------- stateful leaf components (need their own hooks) ---------- */
function TextFieldC({ c }) {
  const [v, setV] = React.useState(c.value ?? "");
  const type = c.component === "DatePicker" ? "date" : (c.type || "text");
  return <TextField label={c.label || ""} value={String(v)} onChange={setV}
    placeholder={c.placeholder} type={type} multiline={c.component === "Textarea" ? 3 : undefined}
    autoComplete="off" />;
}
function SelectC({ c }) {
  const { state, set } = useDS();
  const bound = c.stateKey != null;
  const [local, setLocal] = React.useState(c.value != null ? String(c.value) : undefined);
  const value = bound ? String(state[c.stateKey] ?? "") : local;
  const onChange = (val) => { bound ? set(c.stateKey, val) : setLocal(val); };
  return <Select label={c.label || (c.prefix || "")} labelInline={!!c.prefix}
    options={(c.options || []).map(opt)} value={value} onChange={onChange} />;
}
function CheckboxC({ c }) {
  const [on, setOn] = React.useState(!!c.checked);
  return <Checkbox label={c.label || ""} checked={on} onChange={setOn} />;
}
function SliderC({ c }) {
  const [v, setV] = React.useState(Number(c.value) || 0);
  return <RangeSlider label={c.label || ""} value={v} min={Number(c.min) || 0}
    max={Number(c.max) || 100} onChange={setV} output />;
}
function RadioC({ c }) {
  const [sel, setSel] = React.useState(c.value != null ? [String(c.value)] : []);
  return <ChoiceList title={c.label || ""} choices={(c.options || []).map(opt)}
    selected={sel} onChange={setSel} />;
}
function TabsC({ c, byId }) {
  const [i, setI] = React.useState(0);
  const tabs = (c.tabs || []).map((t, idx) => ({ id: "t" + idx, content: t.label || "Tab " + (idx + 1) }));
  const cur = (c.tabs || [])[i] || {};
  const kids = (cur.children || cur.content || []).map((k) => (typeof k === "string" ? byId[k] : k)).filter(Boolean);
  return <Tabs tabs={tabs} selected={i} onSelect={setI}>
    <div style={{ paddingTop: 12 }}><BlockStack gap="300">{kids.map((k, j) => <Node key={j} c={k} />)}</BlockStack></div>
  </Tabs>;
}
function AccordionItem({ it, byId }) {
  const [open, setOpen] = React.useState(false);
  const kids = (it.children || it.content || []).map((k) => (typeof k === "string" ? byId[k] : k)).filter(Boolean);
  return <BlockStack gap="200">
    <Button disclosure={open ? "up" : "down"} textAlign="left" fullWidth onClick={() => setOpen(!open)}>{it.title || ""}</Button>
    <Collapsible open={open} id={"acc" + Math.random()}><BlockStack gap="200">{kids.map((k, j) => <Node key={j} c={k} />)}</BlockStack></Collapsible>
  </BlockStack>;
}

/* ---------- charts (inline SVG; Polaris-viz not bundled) ---------- */
const PAL = ["#4f8df6", "#3fb27f", "#e5a13a", "#5cb85c", "#ef4444", "#06b6d4", "#a855f7"];
const ICON_PATHS = {
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
  export: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
  filter: "M22 3H2l8 9.46V19l4 2v-8.54L22 3z",
  plus: "M12 5v14M5 12h14",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35",
  more: "M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM19 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM5 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2z",
  calendar: "M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0",
};
function IconSvg({ name }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" width="16" height="16">
    <path d={ICON_PATHS[name] || ICON_PATHS.more} /></svg>;
}
function normalize(c, state) {
  let series = bindVal(c.series, state);
  if (series) {
    series = series.map((se) => ({
      name: se.name, color: se.color,
      data: (bindVal(se.data, state) || se.data || []).map((v) => +bindVal(v, state) || 0),
    }));
    return { labels: bindVal(c.labels, state) || [], series };
  }
  const data = bindVal(c.data, state) || [];
  return { labels: data.map((d) => d.label ?? ""), series: [{ name: c.seriesName || "", data: data.map((d) => +bindVal(d.value, state) || 0) }] };
}
function Chart({ c }) {
  const { state } = useDS();
  const { labels, series } = normalize(c, state);
  const W = 520, H = 220, padL = 40, padB = 26, padT = 8, plotW = W - padL - 8, plotH = H - padT - padB;
  const kind = c.component;
  let svg;
  if (kind === "Donut") {
    const data = bindVal(c.data, state) || [];
    const total = data.reduce((s, d) => s + (+d.value || 0), 0) || 1;
    let off = 0; const r = 60, cx = 90, cy = 90, C = 2 * Math.PI * r;
    svg = <svg viewBox="0 0 180 180" style={{ width: 180 }}>{data.map((d, i) => {
      const f = (+d.value || 0) / total; const el = <circle key={i} cx={cx} cy={cy} r={r} fill="none"
        stroke={PAL[i % PAL.length]} strokeWidth="20" strokeDasharray={`${f * C} ${C}`}
        strokeDashoffset={-off * C} transform={`rotate(-90 ${cx} ${cy})`} />; off += f; return el;
    })}</svg>;
  } else {
    const n = Math.max(labels.length, ...series.map((s) => s.data.length), 1);
    const idx = Array.from({ length: n }, (_, i) => i);
    let max = 0;
    idx.forEach((i) => { const t = series.reduce((s, se) => s + (+se.data[i] || 0), 0); max = Math.max(max, t); });
    max = max || 1; const band = plotW / n;
    svg = <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }}>
      {kind !== "LineChart" && idx.map((i) => {
        let acc = 0; const bw = band * 0.55, x = padL + i * band + (band - bw) / 2;
        return <g key={i}>{series.map((se, si) => {
          const val = +se.data[i] || 0, h = (val / max) * plotH, y = padT + plotH - (acc + val) / max * plotH;
          acc += val; return <rect key={si} x={x} y={y} width={bw} height={h} fill={se.color || PAL[si % PAL.length]} />;
        })}<text x={x + bw / 2} y={H - 8} textAnchor="middle" fontSize="9" fill="#6d7175">{labels[i] ?? ""}</text></g>;
      })}
      {kind === "LineChart" && series.map((se, si) => {
        const step = plotW / Math.max(1, n - 1);
        const pts = se.data.map((v, i) => `${padL + i * step},${padT + plotH - (+v || 0) / max * plotH}`).join(" ");
        return <polyline key={si} points={pts} fill="none" stroke={se.color || PAL[si % PAL.length]} strokeWidth="2.5" />;
      })}
      {kind === "LineChart" && idx.map((i) => <text key={"l" + i} x={padL + i * (plotW / Math.max(1, n - 1))} y={H - 8} textAnchor="middle" fontSize="9" fill="#6d7175">{labels[i] ?? ""}</text>)}
    </svg>;
  }
  return <BlockStack gap="200">
    {c.title && <Text as="p" variant="headingSm">{c.title}</Text>}
    {svg}
    {(series.length > 1 || (kind === "Donut")) && <InlineStack gap="300">
      {(kind === "Donut" ? (bindVal(c.data, state) || []) : series).map((s, i) =>
        <InlineStack key={i} gap="100" blockAlign="center">
          <span style={{ width: 10, height: 10, borderRadius: 3, background: s.color || PAL[i % PAL.length], display: "inline-block" }} />
          <Text as="span" variant="bodySm" tone="subdued">{s.name || s.label || ""}</Text>
        </InlineStack>)}
    </InlineStack>}
  </BlockStack>;
}

/* ---------- main dispatch ---------- */
const TEXT_VARIANT = { title: "headingLg", subtitle: "headingMd", label: "bodySm", muted: "bodySm", body: "bodyMd" };
function Node({ c }) {
  const { state, byId } = useDS();
  if (!c || typeof c !== "object") return null;
  const kids = () => kidsOf(c, byId).map((k, i) => <Node key={k.id || i} c={k} />);
  switch (c.component) {
    case "Screen": case "Page": {
      const rich = /"(Card|Metric|Grid|BarChart|LineChart|Donut|PieChart|Table)"/.test(JSON.stringify(c));
      const inner = <BlockStack gap="500">{kids()}</BlockStack>;
      return rich ? inner : <Card><BlockStack gap="400">{kids()}</BlockStack></Card>;  // form -> one container
    }
    case "Card": return <Card><BlockStack gap="400">{kids()}</BlockStack></Card>;
    case "Stack": case "Column": return <BlockStack gap={GAP[c.gap] || "400"}>{kids()}</BlockStack>;
    case "Row": return <InlineStack gap={GAP[c.gap] || "300"} align={c.justify === "between" ? "space-between" : c.justify} blockAlign={c.align || "center"} wrap={!!c.wrap}>{kids()}</InlineStack>;
    case "Grid": return <InlineGrid columns={c.cols || 2} gap="400">{kids()}</InlineGrid>;
    case "Separator": case "Divider": return <Divider />;
    case "Text": return <Text as={/heading/.test(TEXT_VARIANT[c.variant] || "") ? "h2" : "p"}
      variant={TEXT_VARIANT[c.variant] || "bodyMd"} tone={c.variant === "muted" || c.variant === "label" ? "subdued" : undefined}
      fontWeight={c.variant === "label" ? "medium" : undefined}>{bindVal(c.text, state) ?? ""}</Text>;
    case "Link": return <Link url={c.href || "#"}>{c.text || ""}</Link>;
    case "Input": case "Textarea": case "DatePicker": return <TextFieldC c={c} />;
    case "Select": return <SelectC c={c} />;
    case "RadioGroup": return <RadioC c={c} />;
    case "Slider": return <SliderC c={c} />;
    case "Checkbox": case "Switch": return <CheckboxC c={c} />;
    case "Toggle": return <Button pressed={c.pressed}>{c.label || ""}</Button>;
    case "Button": {
      const v = { default: "primary", secondary: "secondary", outline: "secondary", ghost: "tertiary", destructive: "primary" }[c.variant || "default"];
      return <Button variant={v} tone={c.variant === "destructive" ? "critical" : undefined} size={c.size === "sm" ? "slim" : c.size === "lg" ? "large" : undefined}>{c.label || ""}</Button>;
    }
    case "IconButton": return <Button><IconSvg name={c.icon} /></Button>;
    case "Badge": { const t = { success: "success", warning: "warning", destructive: "critical" }[c.tone]; return <Badge tone={t}>{bindVal(c.label, state) || ""}</Badge>; }
    case "Avatar": return <Avatar name={c.fallback || ""} source={c.url} />;
    case "Image": return c.url ? <Thumbnail source={c.url} alt={c.alt || ""} size="large" /> : <div style={{ height: 120, borderRadius: 8, background: "linear-gradient(135deg,#f1f2f4,#e1e3e5)" }} />;
    case "Metric": return <Card><BlockStack gap="200">
      <Text as="p" variant="bodySm" tone="subdued">{c.label || ""}</Text>
      {bindVal(c.description, state) && <Text as="p" variant="bodySm" tone="subdued">{bindVal(c.description, state)}</Text>}
      <Text as="p" variant="heading2xl">{bindVal(c.value, state) ?? ""}</Text>
      {bindVal(c.delta, state) && <Text as="span" variant="bodySm" tone={c.deltaTone === "down" ? "critical" : "success"}>{(c.deltaTone === "down" ? "▾ " : "▴ ") + bindVal(c.delta, state)}</Text>}
      {c.chart && <Node c={c.chart} />}
    </BlockStack></Card>;
    case "Progress": return <BlockStack gap="100">{c.label && <Text as="p" variant="bodySm">{c.label}</Text>}<ProgressBar progress={Number(c.value) || 0} size="small" /></BlockStack>;
    case "Skeleton": return <SkeletonBodyText lines={c.lines || 3} />;
    case "Tooltip": return <Tooltip content={c.tip || ""}><Text as="span" variant="bodyMd">{c.label || ""}</Text></Tooltip>;
    case "Alert": { const t = { destructive: "critical", success: "success", warning: "warning" }[c.tone] || "info"; return <Banner title={c.title || ""} tone={t}>{c.description && <p>{c.description}</p>}</Banner>; }
    case "Tabs": return <TabsC c={c} byId={byId} />;
    case "Accordion": return <BlockStack gap="300">{(c.items || []).map((it, i) => <AccordionItem key={i} it={it} byId={byId} />)}</BlockStack>;
    case "Table": {
      const rows = (c.rows || []).map((r) => (Array.isArray(r) ? r : Object.values(r)).map((x) => String(x)));
      return <DataTable columnContentTypes={(c.columns || []).map(() => "text")} headings={c.columns || []} rows={rows} />;
    }
    case "BarChart": case "LineChart": case "Donut": case "PieChart": return <Chart c={c} />;
    default: return <Banner tone="warning">Unmapped: {c.component}</Banner>;
  }
}

function App({ components }) {
  const list = Array.isArray(components) ? components : [components];
  const byId = {}; list.forEach((c) => c && c.id && (byId[c.id] = c));
  const [state, setState] = React.useState(() => buildState(list));
  const set = (k, v) => setState((s) => ({ ...s, [k]: v }));
  const referenced = new Set();
  deepWalk(list, (o) => { const ks = o.children || (o.child ? [o.child] : []); (Array.isArray(ks) ? ks : [ks]).forEach((k) => typeof k === "string" && referenced.add(k)); });
  const roots = list.filter((c) => c && !referenced.has(c.id));
  return <StateCtx.Provider value={{ state, set, byId }}>
    <AppProvider i18n={en}>
      <div style={{ padding: 20 }}><BlockStack gap="500">{(roots.length ? roots : list).map((n, i) => <Node key={n.id || i} c={n} />)}</BlockStack></div>
    </AppProvider>
  </StateCtx.Provider>;
}

class ErrorBoundary extends React.Component {
  constructor(p) { super(p); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  render() {
    if (this.state.err) return <div style={{ padding: 20, color: "#b91c1c", fontSize: 13, fontFamily: "monospace" }}>
      Polaris render error: {String(this.state.err && this.state.err.message || this.state.err)}</div>;
    return this.props.children;
  }
}

const roots = new WeakMap();
window.renderPolaris = function (components, mount) {
  const existing = roots.get(mount);
  if (existing) { existing.unmount(); roots.delete(mount); }   // never reconcile a clobbered node
  mount.innerHTML = "";
  const r = createRoot(mount);
  roots.set(mount, r);
  r.render(<ErrorBoundary key={Date.now()}><App components={components} /></ErrorBoundary>);
};
window.unmountPolaris = function (mount) {
  const r = roots.get(mount);
  if (r) { r.unmount(); roots.delete(mount); }
};
