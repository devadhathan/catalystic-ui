#!/usr/bin/env python3
"""
Catalyst — runtime verification of agent-emitted A2UI surfaces against a catalog.

This is the Python mirror of playground/web/verify.js. Both enforce output/catalog.json
on a surface (a list of A2UI component nodes) using real JSON-Schema validation
(unevaluatedProperties:false + const/enum discriminated union catches unknown components,
bad variants, missing required props, and extra/reworded props).

Failure policy is split by consequence (a component is consequential when the catalog
carries `x-consequential: true`, mirrored in output/consequential.json):

  * non-consequential node fails  -> REPAIR (drop unknown props / snap a bad variant to its
                                     default enum value), re-validate, keep if it now passes,
                                     otherwise drop it. Always logged.
  * consequential node fails      -> REJECT. Never coerced. The node is not rendered; a clear
                                     error is surfaced in its place.

`verify(surface, catalog, lenient=...)` returns a structured report plus a `repaired`
surface (used by lenient/playground callers). Strict callers treat any failure as fatal.
"""
import copy
import json
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    _HAVE_JSONSCHEMA = True
except Exception:  # pragma: no cover - fallback path when jsonschema is absent
    _HAVE_JSONSCHEMA = False

# Common props every A2UI node may carry (A2UI ComponentCommon + CatalogComponentCommon).
# unevaluatedProperties:false makes the per-component prop set closed *except* for these.
COMMON_PROPS = {"component", "id", "children", "child", "weight"}

# Stub of the shared A2UI common_types the catalog $refs. The real spec lives at
# https://a2ui.org/... ; for validation we only need the shapes these refs constrain.
COMMON_TYPES = {
    "$id": "https://a2ui.org/specification/v0_9/common_types.json",
    "$defs": {
        "ComponentCommon": {
            "type": "object",
            "properties": {
                "component": {"type": "string"},
                "id": {"type": "string"},
                "children": {"type": "array", "items": {"type": ["string", "object"]}},
                "child": {"type": ["string", "object"]},
            },
        },
        "DynamicString": {"type": ["string", "object"]},
    },
}


# ---------------------------------------------------------------------------
# catalog introspection
# ---------------------------------------------------------------------------
def _inner_schema(comp):
    """The component-specific allOf branch (the one that declares `properties`)."""
    for sub in comp.get("allOf", []):
        if isinstance(sub, dict) and "properties" in sub:
            return sub
    return {}


def component_info(catalog, name):
    """Allowed props, required list, and enum maps for one catalog component."""
    comp = catalog.get("components", {}).get(name)
    if comp is None:
        return None
    inner = _inner_schema(comp)
    props = inner.get("properties", {})
    enums = {p: spec["enum"] for p, spec in props.items()
             if isinstance(spec, dict) and "enum" in spec}
    return {
        "required": [r for r in inner.get("required", [])],
        "enums": enums,
        "allowed": set(props.keys()) | COMMON_PROPS,
    }


def consequential_set(catalog, sidecar=None):
    """Components flagged consequential — from x-consequential, or an override list."""
    if sidecar is not None:
        return set(sidecar)
    return {n for n, c in catalog.get("components", {}).items()
            if isinstance(c, dict) and c.get("x-consequential") is True}


# ---------------------------------------------------------------------------
# validation engine
# ---------------------------------------------------------------------------
def _build_validator(catalog):
    if not _HAVE_JSONSCHEMA:
        return None
    res_cat = Resource.from_contents(catalog, default_specification=DRAFT202012)
    res_com = Resource.from_contents(COMMON_TYPES, default_specification=DRAFT202012)
    registry = (Registry()
                .with_resource(catalog["$id"], res_cat)
                .with_resource(COMMON_TYPES["$id"], res_com))
    return Draft202012Validator(
        {"$ref": catalog["$id"] + "#/$defs/anyComponent"}, registry=registry)


def _node_errors(validator, catalog, node):
    """Return a list of human-readable validation errors for a single node ([] = valid)."""
    if validator is not None:
        return [e.message for e in validator.iter_errors(node)]
    # dependency-free fallback: mirror the schema checks the AJV/jsonschema path performs.
    name = node.get("component")
    info = component_info(catalog, name)
    if info is None:
        return ["unknown component '%s'" % name]
    errs = []
    for r in info["required"]:
        if r not in node:
            errs.append("missing required property '%s'" % r)
    for prop, allowed in info["enums"].items():
        if prop in node and node[prop] not in allowed:
            errs.append("'%s' must be one of %s" % (prop, allowed))
    for k in node:
        if k not in info["allowed"]:
            errs.append("unevaluated (unknown) property '%s'" % k)
    return errs


def _error_node(name, why):
    return {
        "component": "Alert", "tone": "danger",
        "title": "Blocked: %s" % (name or "unknown"),
        "description": "%s failed catalog verification and was not rendered (%s)."
                       % (name or "component", why),
    }


def _repair(node, info):
    """Non-consequential repair: drop unknown props, snap bad enums. Returns (log, missing)."""
    log = []
    for k in list(node.keys()):
        if k not in info["allowed"]:
            del node[k]
            log.append("dropped unknown prop '%s'" % k)
    for prop, allowed in info["enums"].items():
        if prop in node and node[prop] not in allowed and allowed:
            log.append("snapped '%s' %r -> '%s'" % (prop, node[prop], allowed[0]))
            node[prop] = allowed[0]
    missing = [r for r in info["required"] if r not in node]
    return log, missing


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def verify(surface, catalog, lenient=False, consequential=None):
    """Verify a surface (node or list of nodes) against a catalog.

    Returns {ok, lenient, results:[...], rejected:[...], repaired:<surface>}.
    In lenient mode `repaired` is the surface to render (repairs applied, unknown/unrepairable
    nodes dropped, consequential failures replaced with an inline error node).
    """
    validator = _build_validator(catalog)
    conseq = consequential_set(catalog, consequential)
    results, rejected = [], []

    def process(value):
        if isinstance(value, list):
            out = []
            for item in value:
                r = process(item)
                if r is not None:
                    out.append(r)
            return out
        if isinstance(value, dict) and value.get("component"):
            node = copy.deepcopy(value)
            name = node.get("component")
            is_conseq = name in conseq
            errors = _node_errors(validator, catalog, node)
            known = component_info(catalog, name) is not None

            if not errors:
                action = "keep"
            elif is_conseq:
                action = "reject"
            elif not known:
                action = "drop"           # unknown component: nothing to repair
            else:
                info = component_info(catalog, name)
                log, missing = _repair(node, info)
                errors2 = _node_errors(validator, catalog, node)
                if not errors2 and not missing:
                    action = "repair"
                    errors = errors + [">> " + m for m in log]
                else:
                    action = "drop"

            results.append({"component": name, "consequential": is_conseq,
                            "valid": not errors if action == "keep" else False,
                            "action": action, "errors": errors})

            if action == "reject":
                rejected.append({"component": name, "errors": errors})
                return _error_node(name, "; ".join(errors) or "invalid")
            if action == "drop":
                return None
            # keep / repair: also process any nested nodes it carries
            for k, v in list(node.items()):
                if isinstance(v, (list, dict)):
                    node[k] = process(v)
            return node
        if isinstance(value, dict):
            return {k: process(v) for k, v in value.items()}
        return value

    repaired = process(surface)
    any_invalid = any(r["action"] != "keep" for r in results)
    ok = (len(rejected) == 0) if lenient else (not any_invalid)
    return {"ok": ok, "lenient": lenient, "results": results,
            "rejected": rejected, "repaired": repaired}


def load_catalog(path="output/catalog.json"):
    return json.loads(Path(path).read_text())


if __name__ == "__main__":
    import sys
    cat = load_catalog(sys.argv[1] if len(sys.argv) > 1 else "output/catalog.json")
    data = json.loads(sys.stdin.read())
    print(json.dumps(verify(data, cat, lenient=("--lenient" in sys.argv)), indent=2))
