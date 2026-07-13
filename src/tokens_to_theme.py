#!/usr/bin/env python3
"""
Catalyst converter — Step 1: W3C Design Tokens (DTCG) -> theme.

This is the ingestion target for Figma MCP: Figma exports a design system's
variables as DTCG tokens; this maps them to (a) the CSS-variable shape a renderer
wears and (b) the A2UI catalog `theme` block. Fully standalone — does NOT touch
the shadcn or Polaris renderers in playground/.

  python3 src/tokens_to_theme.py --tokens data/sample_tokens.dtcg.json
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"

# DTCG token path  ->  renderer CSS variable
TOKEN_TO_CSSVAR = {
    "color.brand": "--primary",
    "color.onBrand": "--primary-foreground",
    "color.background": "--background",
    "color.surface": "--card",
    "color.text": "--foreground",
    "color.muted": "--muted",
    "color.mutedText": "--muted-foreground",
    "color.border": "--border",
    "color.border@input": "--input",   # same source, second target
    "radius.md": "--radius",
    "font.family.sans": "--font-sans",
}


def flatten(tokens, prefix=""):
    """DTCG tree -> {dotted.path: $value}."""
    flat = {}
    for k, v in tokens.items():
        if k.startswith("$"):
            continue
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and "$value" in v:
            flat[path] = v["$value"]
        elif isinstance(v, dict):
            flat.update(flatten(v, path))
    return flat


def resolve(value, flat, seen=None):
    """Resolve DTCG references like {color.brand}."""
    seen = seen or set()
    if isinstance(value, str):
        m = re.fullmatch(r"\{([^}]+)\}", value.strip())
        if m and m.group(1) not in seen:
            return resolve(flat.get(m.group(1), value), flat, seen | {m.group(1)})
    return value


def build_theme(dtcg):
    flat = flatten(dtcg)
    css_vars, missing = {}, []
    for token_path, cssvar in TOKEN_TO_CSSVAR.items():
        src = token_path.split("@")[0]  # strip alias marker
        if src in flat:
            css_vars[cssvar] = resolve(flat[src], flat)
        else:
            missing.append((token_path, cssvar))

    # A2UI catalog theme block (a2ui.org common_types theme shape)
    a2ui_theme = {
        "primaryColor": css_vars.get("--primary", "#000000"),
        "agentDisplayName": dtcg.get("$description", "Design system"),
    }
    return {"cssVars": css_vars, "a2uiTheme": a2ui_theme, "unmapped": missing}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", default="data/sample_tokens.dtcg.json")
    args = ap.parse_args()

    dtcg = json.loads((ROOT / args.tokens).read_text())
    theme = build_theme(dtcg)

    OUT.mkdir(exist_ok=True)
    (OUT / "theme.json").write_text(json.dumps(theme, indent=2) + "\n")

    print(f"[tokens->theme] {len(theme['cssVars'])} CSS variables derived from {args.tokens}")
    for var, val in theme["cssVars"].items():
        print(f"    {var:<22} {val}")
    if theme["unmapped"]:
        print("  unmapped tokens (no source in this file):",
              [t for t, _ in theme["unmapped"]])
    print(f"[tokens->theme] wrote {OUT / 'theme.json'}")
    print("\nThis theme can later be applied to a renderer by setting these CSS variables "
          "on a container — WITHOUT changing the shadcn/Polaris renderers themselves.")


if __name__ == "__main__":
    main()
