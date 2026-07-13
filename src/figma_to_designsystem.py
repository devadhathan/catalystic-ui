#!/usr/bin/env python3
"""
Catalyst — Figma agent engine: Figma Variables -> W3C DTCG tokens -> toggle-ready design system.

This is what the agent runs on a real design system. Input is the Figma REST response
GET /v1/files/:key/variables/local (the modern token source); output is:
  1. W3C DTCG tokens  (standard, portable)
  2. a "design system" skin  (the exact shape playground/web/tokens-panel.js drops into the toggle)

Standalone — touches nothing in playground/. Live use: give a Figma file key + token
(or point the Figma MCP at a file) and pipe its variables JSON in here.

  python3 src/figma_to_designsystem.py --figma data/sample_figma_variables.json --name Acme
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"

# Figma variable name (keyword) -> CSS variable(s). Matched by substring, so
# "color/brand", "colors/primary", "brand-color" all resolve.
NAME_RULES = [
    (("on-brand", "on brand", "primary-foreground", "on-primary"), ["--primary-foreground"]),
    (("brand", "primary", "accent"), ["--primary"]),
    (("background", "canvas", "/bg"), ["--background"]),
    (("surface", "card"), ["--card"]),
    (("muted-text", "muted foreground", "text-secondary", "subtle-text"), ["--muted-foreground"]),
    (("muted", "subtle", "fill-secondary"), ["--muted"]),
    (("text", "foreground", "ink"), ["--foreground"]),
    (("border", "outline", "stroke"), ["--border", "--input"]),
    (("radius", "corner"), ["--radius"]),
]


def hexof(c):
    to = lambda x: max(0, min(255, round((x or 0) * 255)))
    return "#{:02x}{:02x}{:02x}".format(to(c.get("r")), to(c.get("g")), to(c.get("b")))


def match(name):
    n = name.lower()
    for keys, targets in NAME_RULES:
        if any(k in n for k in keys):
            return targets
    return None


def figma_to_dtcg(figma):
    """Figma variables -> W3C DTCG token tree (color.*, radius.*)."""
    meta = figma.get("meta", figma)
    variables = meta.get("variables", {})
    colls = meta.get("variableCollections", {})
    default_mode = next(iter(colls.values()), {}).get("defaultModeId")

    tokens = {"color": {}, "radius": {}}
    for v in variables.values():
        modes = v.get("valuesByMode", {})
        raw = modes.get(default_mode, next(iter(modes.values()), None))
        leaf = v["name"].split("/")[-1]
        if v.get("resolvedType") == "COLOR" and isinstance(raw, dict):
            tokens["color"][leaf] = {"$type": "color", "$value": hexof(raw)}
        elif v.get("resolvedType") == "FLOAT" and "radius" in v["name"].lower():
            tokens["radius"][leaf] = {"$type": "dimension", "$value": f"{raw}px"}
    return {k: v for k, v in tokens.items() if v}


def figma_to_skin(figma, name):
    """Figma variables -> CSS-variable skin (toggle-ready), by name matching."""
    meta = figma.get("meta", figma)
    variables = meta.get("variables", {})
    colls = meta.get("variableCollections", {})
    default_mode = next(iter(colls.values()), {}).get("defaultModeId")

    vars_out = {}
    for v in variables.values():
        targets = match(v["name"])
        if not targets:
            continue
        modes = v.get("valuesByMode", {})
        raw = modes.get(default_mode, next(iter(modes.values()), None))
        if v.get("resolvedType") == "COLOR" and isinstance(raw, dict):
            val = hexof(raw)
        elif v.get("resolvedType") == "FLOAT":
            val = f"{raw}px"
        else:
            continue
        for t in targets:
            vars_out.setdefault(t, val)   # first match wins
    return {"id": re.sub(r"\W+", "-", name.lower()), "name": name, "vars": vars_out, "css": ""}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--figma", default="data/sample_figma_variables.json")
    ap.add_argument("--name", default="Imported")
    args = ap.parse_args()

    figma = json.loads((ROOT / args.figma).read_text())
    dtcg = figma_to_dtcg(figma)
    skin = figma_to_skin(figma, args.name)

    OUT.mkdir(exist_ok=True)
    (OUT / "figma.dtcg.json").write_text(json.dumps(dtcg, indent=2) + "\n")
    (OUT / "figma.designsystem.json").write_text(json.dumps(skin, indent=2) + "\n")

    nvars = sum(len(v) for v in dtcg.values())
    print(f"[figma agent] '{args.name}': {nvars} tokens -> {len(skin['vars'])} CSS vars")
    for k, val in skin["vars"].items():
        print(f"    {k:<22} {val}")
    print(f"[figma agent] wrote output/figma.dtcg.json + output/figma.designsystem.json")
    print("\nThe designsystem.json is exactly the shape tokens-panel.js adds to the toggle — "
          "so a real Figma import becomes a new option next to shadcn / Polaris.")


if __name__ == "__main__":
    main()
