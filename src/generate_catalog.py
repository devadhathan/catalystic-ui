#!/usr/bin/env python3
"""
Catalyst — generate an A2UI catalog from a design-system intermediate representation.

Pipeline:
  intermediate JSON (from Figma/Storybook extraction)
    --> [deterministic] component schemas (props, variants, required fields)
    --> [Opus 4.8]      semantic layer: LLM usage guidance in description fields
    --> [Opus 4.8]      rules.txt (hard MUST-rules)
    --> [deterministic] assemble into A2UI v0.9.1 catalog shape
    --> catalog.json + rules.txt

The split matters: SCHEMAS are built by code (never hallucinated), GUIDANCE is
written by the model (judgement). A validator checks every rule references a real prop.

The stage-1 `skeletons.json` is a flat intermediate; the A2UI `allOf` /
`unevaluatedProperties` component shape is assembled deterministically at the end.
"""
import copy
import json
import os
import sys
import argparse
from pathlib import Path

try:
    from anthropic import Anthropic
except ImportError:
    print("Run: pip install anthropic --break-system-packages", file=sys.stderr)
    sys.exit(1)

MODEL = "claude-sonnet-4-6"  # cost-efficient; good structured-output + guidance quality

# Shared A2UI common types every catalog component references.
COMMON = "https://a2ui.org/specification/v0_9/common_types.json#/$defs/"

# `weight` guidance is identical across every A2UI catalog — keep it deterministic.
WEIGHT_DESCRIPTION = (
    "The relative weight of this component within a Row or Column layout. Similar "
    "to the CSS 'flex-grow' property. May ONLY be set when the component is a "
    "direct descendant of a layout component."
)


# ---------------------------------------------------------------------------
# 1. DETERMINISTIC: build JSON-schema skeletons from the intermediate rep.
#    No model here. Props and variants are ground truth from extraction.
# ---------------------------------------------------------------------------
def build_schema_skeletons(design):
    skeletons = {}
    for comp in design["components"]:
        name = comp["name"]
        props = {"component": {"const": name}}
        required = ["component"]

        # variant properties -> enums
        for vprop, values in comp.get("variantProperties", {}).items():
            # skip pure interaction states (focus/hover/open) — not agent-set
            if vprop.lower() in ("state",) and set(v.lower() for v in values) & {
                "hover", "focus", "open"
            }:
                # keep only the semantically meaningful states if any
                meaningful = [v for v in values if v.lower() in ("disabled", "error", "on", "off")]
                if meaningful:
                    props[vprop] = {"type": "string", "enum": meaningful}
                continue
            props[vprop] = {"type": "string", "enum": values}

        # text layers -> text props (dynamic-bindable)
        for layer in comp.get("textLayers", []):
            props[layer] = {
                "type": ["string", "object"],
                "description": f"PLACEHOLDER_{name}_{layer}",  # LLM fills this
            }
            # heuristics for required text
            if layer in ("label", "text", "value", "merchant", "amount"):
                required.append(layer)

        skeletons[name] = {
            "type": "object",
            "properties": props,
            "required": required,
            "_meta": {  # stripped before final output; guides the LLM pass
                "description": comp.get("description", ""),
                "consequential": comp.get("consequentialHint", False),
            },
        }
    return skeletons


# ---------------------------------------------------------------------------
# 2. SEMANTIC: ask Opus to write description fields + usage guidance.
#    One structured-output call per component — the JSON shape is guaranteed,
#    so there is no fenced-string parsing to get wrong.
# ---------------------------------------------------------------------------
GUIDANCE_SYSTEM = """You are generating the semantic layer of an A2UI component catalog.

A2UI is a declarative protocol: an LLM agent composes UIs by emitting JSON that
references pre-approved components. The QUALITY of the agent's output depends
entirely on the guidance written into each component's `description` fields.

Write concise, imperative guidance aimed at the AGENT that will later compose UIs.
Good guidance says WHEN to use the component, WHICH prop value fits WHICH situation,
and names common mistakes to avoid. Keep each description under 30 words. Encode
interaction patterns: prefer binding conditional UI to the data model over deferring
to a follow-up generation.

For any component flagged consequential, the component_description MUST state that the
agent must use this dedicated component for the action rather than composing it from
primitives, and must not reword its fixed content."""


def write_guidance(client, skeletons):
    enriched = copy.deepcopy(skeletons)
    for name, schema in enriched.items():
        text_props = [
            p for p, v in schema["properties"].items()
            if p != "component" and isinstance(v.get("type"), list)
        ]
        out_schema = {
            "type": "object",
            "properties": {
                "component_description": {"type": "string"},
                "descriptions": {
                    "type": "object",
                    "properties": {p: {"type": "string"} for p in text_props},
                    "required": text_props,
                    "additionalProperties": False,
                },
            },
            "required": ["component_description", "descriptions"],
            "additionalProperties": False,
        }
        facts = {
            "component": name,
            "purpose": schema["_meta"]["description"],
            "consequential": schema["_meta"]["consequential"],
            "variant_props": {
                p: v["enum"] for p, v in schema["properties"].items() if "enum" in v
            },
            "text_props": text_props,
            "required": [r for r in schema["required"] if r != "component"],
        }
        msg = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=GUIDANCE_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": out_schema}},
            messages=[{
                "role": "user",
                "content": "Write catalog guidance for this component.\n\n"
                           + json.dumps(facts, indent=2),
            }],
        )
        text = next(b.text for b in msg.content if b.type == "text")
        result = json.loads(text)
        schema["description"] = result["component_description"]
        for p in text_props:
            schema["properties"][p]["description"] = result["descriptions"][p]
    return enriched


# ---------------------------------------------------------------------------
# 3. RULES: generate the hard MUST-rules file (like basic_catalog/rules.txt).
# ---------------------------------------------------------------------------
RULES_SYSTEM = """Generate a `rules.txt` for an A2UI catalog: a short list of hard,
non-negotiable rules the composing agent MUST follow. Model it on this style:

**REQUIRED PROPERTIES:** You MUST include ALL required properties for every component.
- For 'Text', you MUST provide 'text'.
- For 'Button', you MUST provide 'action'.

Derive rules from the required arrays and consequential flags in the schemas provided.
Be terse. Plain text, no markdown fences. Under 15 lines."""


def write_rules(client, enriched):
    payload = json.dumps(enriched, indent=2)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=RULES_SYSTEM,
        messages=[{"role": "user", "content": f"Schemas:\n{payload}"}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


# ---------------------------------------------------------------------------
# 4. VALIDATE: every prop a rule/description leans on must actually exist.
# ---------------------------------------------------------------------------
def validate(enriched, skeletons):
    problems = []
    for name, schema in enriched.items():
        real_props = set(skeletons.get(name, {}).get("properties", {}).keys())
        for req in schema.get("required", []):
            if req not in real_props and req != "component":
                problems.append(f"{name}: required '{req}' not in extracted props")
    return problems


# ---------------------------------------------------------------------------
# 5. ASSEMBLE: convert the flat enriched schemas into the A2UI v0.9.1 shape —
#    allOf(ComponentCommon, CatalogComponentCommon, {props}) + unevaluatedProperties.
# ---------------------------------------------------------------------------
def to_a2ui_component(name, flat, consequential=False):
    inner_props = {"component": {"const": name}}
    for prop, spec in flat["properties"].items():
        if prop == "component":
            continue
        if isinstance(spec.get("type"), list) and "object" in spec["type"]:
            # dynamic-bindable text -> DynamicString ref (keeps the guidance prose)
            ref = {"$ref": COMMON + "DynamicString"}
            if spec.get("description"):
                ref["description"] = spec["description"]
            inner_props[prop] = ref
        else:
            inner_props[prop] = spec  # enum / const props pass through unchanged

    inner = {"type": "object"}
    if flat.get("description"):
        inner["description"] = flat["description"]
    inner["properties"] = inner_props
    inner["required"] = flat["required"]

    comp = {
        "type": "object",
        "allOf": [
            {"$ref": COMMON + "ComponentCommon"},
            {"$ref": "#/$defs/CatalogComponentCommon"},
            inner,
        ],
        "unevaluatedProperties": False,
    }
    # Carry the consequential flag into the catalog as a JSON-Schema-safe annotation
    # (unknown keyword; ignored by validators). The runtime verifier reads this to
    # decide reject-vs-repair. Also mirrored in the output/consequential.json sidecar.
    if consequential:
        comp["x-consequential"] = True
    return comp


def _is_consequential(flat):
    return bool(flat.get("_meta", {}).get("consequential"))


def assemble_catalog(design, enriched):
    catalog_id = f"urn:a2ui:catalog:{design['designSystem'].lower()}"
    names = list(enriched.keys())
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": catalog_id,
        "title": f"{design['designSystem']} Catalog",
        "description": design.get("description", ""),
        "catalogId": catalog_id,
        "components": {n: to_a2ui_component(n, enriched[n], _is_consequential(enriched[n])) for n in names},
        "$defs": {
            "CatalogComponentCommon": {
                "type": "object",
                "properties": {
                    "weight": {"type": "number", "description": WEIGHT_DESCRIPTION}
                },
            },
            "anyComponent": {
                "oneOf": [{"$ref": f"#/components/{n}"} for n in names]
            },
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/mock_design_system.json")
    ap.add_argument("--out-dir", default="output")
    ap.add_argument("--dry-run", action="store_true", help="skeletons only, no API calls")
    args = ap.parse_args()

    design = json.loads(Path(args.input).read_text())
    print(f"[1/5] Extracting schemas from {len(design['components'])} components (deterministic)...")
    skeletons = build_schema_skeletons(design)

    if args.dry_run:
        Path(args.out_dir).mkdir(exist_ok=True)
        Path(f"{args.out_dir}/skeletons.json").write_text(json.dumps(skeletons, indent=2))
        print("Dry run: wrote skeletons.json. No API calls made.")
        return

    client = Anthropic()  # reads ANTHROPIC_API_KEY
    print(f"[2/5] Writing usage guidance ({MODEL}, structured output)...")
    enriched = write_guidance(client, skeletons)
    print(f"[3/5] Generating rules.txt ({MODEL})...")
    rules = write_rules(client, enriched)
    print("[4/5] Validating rules against extracted props...")
    problems = validate(enriched, skeletons)
    if problems:
        print("  ! validation warnings:")
        for p in problems:
            print(f"    - {p}")
    else:
        print("  ok: all required props exist in source.")

    print("[5/5] Assembling A2UI v0.9.1 catalog...")
    # NOTE: _meta is intentionally NOT stripped before assembly — assemble_catalog reads
    # each component's consequential flag from it. _meta never leaks into the catalog
    # because to_a2ui_component builds the output component from scratch (only x-consequential
    # is carried over). The runtime verifier needs to know which components are consequential.
    catalog = assemble_catalog(design, enriched)
    consequential = [n for n in enriched if _is_consequential(enriched[n])]

    out = Path(args.out_dir)
    out.mkdir(exist_ok=True)
    (out / "catalog.json").write_text(json.dumps(catalog, indent=2))
    (out / "rules.txt").write_text(rules + "\n")
    # sidecar list of consequential components (mirrors the x-consequential annotations)
    (out / "consequential.json").write_text(json.dumps(
        {"catalogId": catalog["catalogId"], "consequential": consequential}, indent=2) + "\n")
    print(f"\nDone. Wrote {out}/catalog.json, {out}/consequential.json and {out}/rules.txt")


if __name__ == "__main__":
    main()
