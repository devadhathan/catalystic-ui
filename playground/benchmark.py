#!/usr/bin/env python3
"""
Benchmark: A2UI-JSON generation vs raw HTML/CSS/JS generation.

For each scenario we ask the SAME model to produce a UI two ways:
  A) A2UI JSON  — emit component JSON that references a fixed catalog. All
                  styling/markup lives in the (cacheable) catalog, not the output.
  B) Raw HTML   — emit a single self-contained HTML file with inline CSS + JS.

We measure wall-clock latency and token usage (input / output / cached) for each,
so you can compare the cost and speed of the two approaches head to head.

Usage:
  export ANTHROPIC_API_KEY=...
  python3 playground/benchmark.py               # A2UI only (default)
  python3 playground/benchmark.py --with-html   # add the raw HTML baseline
  python3 playground/benchmark.py --trials 3
"""
import argparse
import json
import time
from pathlib import Path

import anthropic

MODEL = "claude-sonnet-4-6"

# Sonnet 4.6 list price, USD per 1M tokens (for a rough cost estimate).
PRICE_IN = 3.0
PRICE_OUT = 15.0
PRICE_CACHE_WRITE = 3.75   # ~1.25x input
PRICE_CACHE_READ = 0.30    # ~0.1x input

ROOT = Path(__file__).resolve().parent.parent
BASIC = ROOT.parent / "A2UI" / "specification" / "v0_9_1" / "catalogs" / "basic"


def load_vocabulary():
    catalog = (BASIC / "catalog.json").read_text()
    rules = (BASIC / "rules.txt").read_text()
    return catalog, rules


A2UI_SYSTEM_TMPL = """You generate A2UI UIs. A2UI is a declarative protocol: you \
compose a UI by emitting a JSON array of components that reference ONLY the \
components defined in the catalog below. You never write HTML, CSS, or JavaScript — \
a renderer turns your JSON into styled native components.

Rules you MUST follow:
{rules}

The component catalog (your entire allowed vocabulary):
{catalog}

Output ONLY a JSON array of component objects (the `components` array of an A2UI \
updateComponents message). Each component has an `id`, a `component` name from the \
catalog, and its properties. Reference children by their string `id`. Bind dynamic \
values to the data model with {{"path": "/field"}}. No prose, no markdown fences."""

A2UI_USER_TMPL = """Scenario: {title}
{brief}

Data model available for binding:
{data_model}

Emit the A2UI components array for this screen."""

HTML_SYSTEM = """You are an expert frontend engineer. You build clean, modern, \
accessible UIs. When given a screen to build, you output a single self-contained \
HTML file: semantic HTML, inline CSS in a <style> tag, and any interactivity in a \
<script> tag. Use a polished, contemporary visual style. Output ONLY the HTML \
document, no prose, no markdown fences."""

HTML_USER_TMPL = """Build this screen as a single self-contained HTML file.

Screen: {title}
{brief}

Data to render:
{data_model}"""


def call(client, system, user, cache_system=False):
    if cache_system:
        system_arg = [{"type": "text", "text": system,
                       "cache_control": {"type": "ephemeral"}}]
    else:
        system_arg = system
    t0 = time.perf_counter()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=system_arg,
        messages=[{"role": "user", "content": user}],
    )
    dt = time.perf_counter() - t0
    u = msg.usage
    text = "".join(b.text for b in msg.content if b.type == "text")
    return {
        "latency_s": dt,
        "input": u.input_tokens,
        "output": u.output_tokens,
        "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
        "stop": msg.stop_reason,
        "text": text,
    }


def cost(r):
    return (
        r["input"] * PRICE_IN
        + r["output"] * PRICE_OUT
        + r["cache_write"] * PRICE_CACHE_WRITE
        + r["cache_read"] * PRICE_CACHE_READ
    ) / 1_000_000


def valid_json_array(text):
    try:
        return isinstance(json.loads(text), list)
    except Exception:
        return False


def avg(rows, key):
    return sum(r[key] for r in rows) / len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--with-html", action="store_true",
                    help="also run the raw HTML/CSS/JS baseline for comparison")
    args = ap.parse_args()

    client = anthropic.Anthropic()
    catalog, rules = load_vocabulary()
    a2ui_system = A2UI_SYSTEM_TMPL.format(rules=rules, catalog=catalog)
    scenarios = json.loads((ROOT / "playground" / "scenarios.json").read_text())["scenarios"]

    results = {}
    for sc in scenarios:
        dm = json.dumps(sc["data_model"], indent=2)
        a2ui_user = A2UI_USER_TMPL.format(title=sc["title"], brief=sc["brief"], data_model=dm)
        html_user = HTML_USER_TMPL.format(title=sc["title"], brief=sc["brief"], data_model=dm)

        print(f"\n### {sc['title']}  ({args.trials} trials each)")
        a2ui_rows, html_rows = [], []
        for i in range(args.trials):
            a = call(client, a2ui_system, a2ui_user, cache_system=True)  # catalog is cacheable
            a["valid"] = valid_json_array(a["text"])
            a2ui_rows.append(a)
            print(f"  A2UI  t{i+1}: {a['latency_s']:5.1f}s  in={a['input']:6d} "
                  f"cache_r={a['cache_read']:6d} out={a['output']:5d}  valid={a['valid']}")

            if args.with_html:
                h = call(client, HTML_SYSTEM, html_user)
                html_rows.append(h)
                print(f"  HTML  t{i+1}: {h['latency_s']:5.1f}s  in={h['input']:6d} "
                      f"                out={h['output']:5d}  stop={h['stop']}")

        results[sc["id"]] = {"a2ui": a2ui_rows}
        if html_rows:
            results[sc["id"]]["html"] = html_rows

        # save the last generated artifacts for inspection
        outdir = ROOT / "playground" / "generated"
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{sc['id']}.a2ui.json").write_text(a2ui_rows[-1]["text"])
        if html_rows:
            (outdir / f"{sc['id']}.html").write_text(html_rows[-1]["text"])

    # ---- summary table --------------------------------------------------
    print("\n" + "=" * 78)
    print(f"SUMMARY  (model={MODEL}, averaged over {args.trials} trials)")
    print("=" * 78)
    hdr = f"{'scenario':<16}{'approach':<8}{'latency':>9}{'out tok':>9}{'in tok':>9}{'$/gen':>10}"
    print(hdr)
    print("-" * 78)
    for sid, cell in results.items():
        approaches = [("A2UI", cell["a2ui"])]
        if "html" in cell:
            approaches.append(("HTML", cell["html"]))
        for name, rows in approaches:
            print(f"{sid:<16}{name:<8}{avg(rows,'latency_s'):>8.1f}s"
                  f"{avg(rows,'output'):>9.0f}{avg(rows,'input'):>9.0f}"
                  f"{sum(cost(r) for r in rows)/len(rows):>10.5f}")
        if "html" in cell:
            a_out, h_out = avg(cell["a2ui"], "output"), avg(cell["html"], "output")
            a_lat, h_lat = avg(cell["a2ui"], "latency_s"), avg(cell["html"], "latency_s")
            print(f"  -> A2UI emits {h_out/max(a_out,1):.1f}x fewer output tokens, "
                  f"{h_lat/max(a_lat,1e-9):.1f}x faster")
        print()

    (ROOT / "playground" / "results.json").write_text(json.dumps(results, indent=2, default=str))
    print("Wrote playground/results.json and playground/generated/*")
    print("\nNote: A2UI input is larger (it carries the whole catalog) but that block is\n"
          "cacheable — see cache_r on trial 2+. Output tokens (5x the price of input and\n"
          "the thing that drives latency) are where A2UI wins.")


if __name__ == "__main__":
    main()
