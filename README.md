# Catalyst

Generate an [A2UI](https://github.com/google/A2UI) component catalog from a
design system.

A2UI is a declarative protocol: an agent composes a UI by emitting JSON that
references *pre-approved* components from a catalog. The catalog is what makes
that output good — it pins down the components' schemas **and** carries the
usage guidance the agent reads while composing. Catalyst produces both, in the
[A2UI v0.9.1 `basic` catalog](https://github.com/google/A2UI/tree/main/specification/v0_9_1/catalogs/basic)
format.

## The idea: split the mechanical from the judgement

A catalog has two kinds of content, and they should not be produced the same way.

| Layer | What it is | Who writes it |
|---|---|---|
| **Schemas** | Component names, props, variant enums, required fields | **Code** — extracted from the design system, never hallucinated |
| **Guidance** | The `description` prose telling an agent *when* to use a component and *which* variant fits *which* situation, plus a `rules.txt` of hard MUST-rules | **The model** — this is judgement |

Extraction is ground truth: if the Figma component set has a `style` variant of
`{primary, secondary, ghost, destructive}`, that enum is a fact, not a guess.
Getting it from an LLM would only introduce drift. Guidance is the opposite —
there is no "correct" string to look up, only a well-judged one — so that half
is generated. A final validator checks the two halves agree (every required
prop a rule leans on actually exists in the extracted schema).

## Pipeline

```
data/mock_design_system.json          intermediate rep
   (stands in for a Figma REST /       (props, variants, text layers,
    Storybook extraction)               consequential hints)
        │
        ▼
  [1] build_schema_skeletons()         DETERMINISTIC — no model
        │                              → output/skeletons.json
        ▼
  [2] write_guidance()   (Opus 4.8)    fills every PLACEHOLDER_* description
        │
        ▼
  [3] write_rules()      (Opus 4.8)    → output/rules.txt (hard MUST-rules)
        │
        ▼
  [4] validate()                       every required prop exists in source?
        │
        ▼
  output/catalog.json + output/rules.txt      A2UI v0.9.1 format
```

Stage 1 is pure and reproducible — `--dry-run` runs only this stage and needs
no API key. The `_meta` block it emits (source description, `consequential`
flag) is what steers stages 2–3, and is stripped before the final
`catalog.json`.

## The intermediate representation

`data/mock_design_system.json` is a mock of what you'd get from a Figma REST
extraction (or Storybook). Each component carries:

- `variantProperties` — variant axes → allowed values (become enum props). Pure
  interaction states (`hover`, `focus`, `open`) are dropped by stage 1: an agent
  sets *semantic* variants, not transient render states.
- `textLayers` — named text slots (become dynamic-bindable string/object props).
  A few well-known names (`label`, `value`, `amount`, …) are treated as required.
- `consequentialHint` — marks components (`Button`, `AmountInput`, `ConfirmSheet`)
  whose action or fixed structure the agent must not recompose or reword. Stage 2
  turns this into an explicit guidance note; stage 3 into a rule.

The sample system, **Meridian**, is a fintech/banking set — chosen because
consequential flows (pay, transfer, confirm) are exactly where "agent must use
the dedicated component, not primitives" guidance earns its keep.

## Usage

```bash
pip install anthropic --break-system-packages

# Deterministic half only — reproducible, no API key, writes output/skeletons.json
python3 src/generate_catalog.py --dry-run

# Full run — writes output/catalog.json and output/rules.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 src/generate_catalog.py
```

Flags: `--input` (default `data/mock_design_system.json`),
`--out-dir` (default `output`), `--dry-run`.

The generated `catalog.json` is meant to be consumed by an A2UI renderer such as
the React demo client at
[`samples/client/react`](https://github.com/google/A2UI/tree/main/samples/client/react).

## Model

Guidance and rules use **Claude Sonnet 4.6** (`claude-sonnet-4-6`) — a
cost-efficient default that still supports structured outputs. Bump `MODEL` in
`src/generate_catalog.py` to `claude-opus-4-8` if you want maximum guidance
quality. The client reads `ANTHROPIC_API_KEY` from the environment.

## Playground

A local interface for testing workflows end to end. Pick a workflow → see the
generated A2UI JSON render live. **Rendering cached output costs nothing** — only
the explicit "Generate new" button spends tokens.

```bash
python3 playground/app.py          # then open http://localhost:8000
```

- `playground/scenarios.json` — the workflows (prompt + seed data model)
- `playground/web/` — the interface + `renderer.js` (the A2UI→DOM renderer; this
  is the seam to swap for a real shadcn renderer later)
- `playground/generated/` — cached A2UI JSON per workflow (rendered for free)
- `playground/benchmark.py` — latency/token measurement; raw-HTML baseline is
  opt-in behind `--with-html` (off by default)

## Files

```
catalyst/
├── README.md
├── src/generate_catalog.py         the pipeline
├── data/mock_design_system.json    intermediate rep (input)
└── output/
    ├── skeletons.json              stage-1 output (regenerate: --dry-run)
    ├── catalog.json                full run only
    └── rules.txt                   full run only
```
