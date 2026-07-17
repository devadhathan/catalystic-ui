#!/usr/bin/env python3
"""
Tests for src/verify.py against the real output/catalog.json.

Fixtures (as required):
  1. valid surface
  2. hallucinated variant   (non-consequential -> repaired; strict -> not ok)
  3. unknown component      (dropped in lenient; not ok in strict)
  4. reworded consequential (ConfirmSheet with a reworded/extra prop -> rejected, never coerced)

Run: python3 src/verify_test.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from verify import verify, load_catalog, consequential_set  # noqa: E402

CAT = load_catalog(os.path.join(os.path.dirname(__file__), "..", "output", "catalog.json"))

FIXTURES = {
    # 1. everything valid: consequential Button (valid enums) + non-consequential nodes
    "valid": [
        {"component": "Button", "label": "Pay now", "style": "primary", "size": "md", "id": "b1"},
        {"component": "TextInput", "label": "Reference", "id": "t1"},
        {"component": "StatCard", "label": "Balance", "value": "$12,400", "trend": "up", "id": "s1"},
    ],
    # 2. hallucinated variant on a NON-consequential component (Badge tone not in enum)
    "hallucinated_variant": [
        {"component": "Badge", "text": "Pending", "tone": "info", "id": "bd1"},
    ],
    # 3. unknown component the catalog has never heard of
    "unknown_component": [
        {"component": "Carousel", "slides": 3, "id": "c1"},
    ],
    # 4. reworded consequential: ConfirmSheet with a reworded/extra prop (ctaLabel instead of
    #    the fixed confirmLabel) -> closed schema rejects it; consequential -> no coercion.
    "reworded_consequential": [
        {"component": "ConfirmSheet", "kind": "payment", "title": "Confirm Payment",
         "summary": "Send £50 to Sam", "ctaLabel": "Yeah, do it", "id": "cs1"},
    ],
}

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
failures = 0


def check(desc, cond):
    global failures
    if not cond:
        failures += 1
    print("  %s  %s" % (PASS if cond else FAIL, desc))


def actions(res):
    return [r["action"] for r in res["results"]]


print("consequential (from catalog):", sorted(consequential_set(CAT)))
assert sorted(consequential_set(CAT)) == ["AmountInput", "Button", "ConfirmSheet"], \
    "catalog must carry x-consequential for Button/AmountInput/ConfirmSheet"

print("\n[1] valid surface")
v_len = verify(FIXTURES["valid"], CAT, lenient=True)
v_str = verify(FIXTURES["valid"], CAT, lenient=False)
check("lenient ok", v_len["ok"] and actions(v_len) == ["keep", "keep", "keep"])
check("strict ok", v_str["ok"])
check("no rejections", not v_len["rejected"])

print("\n[2] hallucinated variant (non-consequential Badge tone=info)")
h_len = verify(FIXTURES["hallucinated_variant"], CAT, lenient=True)
h_str = verify(FIXTURES["hallucinated_variant"], CAT, lenient=False)
check("lenient repairs (not rejects)", actions(h_len) == ["repair"] and not h_len["rejected"])
check("tone snapped to default 'neutral'", h_len["repaired"][0]["tone"] == "neutral")
check("lenient ok (auto-fixed, nothing rejected)", h_len["ok"])
check("strict NOT ok", not h_str["ok"])

print("\n[3] unknown component (Carousel)")
u_len = verify(FIXTURES["unknown_component"], CAT, lenient=True)
u_str = verify(FIXTURES["unknown_component"], CAT, lenient=False)
check("lenient drops it", actions(u_len) == ["drop"] and u_len["repaired"] == [])
check("strict NOT ok", not u_str["ok"])

print("\n[4] reworded consequential (ConfirmSheet + extra 'ctaLabel')")
r_len = verify(FIXTURES["reworded_consequential"], CAT, lenient=True)
r_str = verify(FIXTURES["reworded_consequential"], CAT, lenient=False)
check("rejected (never coerced), even in lenient", actions(r_len) == ["reject"] and len(r_len["rejected"]) == 1)
check("replaced with a clear inline error node", r_len["repaired"][0]["component"] == "Alert"
      and r_len["repaired"][0]["tone"] == "danger")
check("original ctaLabel NOT silently kept", "ctaLabel" not in r_len["repaired"][0])
check("lenient NOT ok (a consequential node was blocked)", not r_len["ok"])
check("strict NOT ok", not r_str["ok"])

print("\n%s (%d failure%s)" % ("ALL PASS" if not failures else "FAILURES",
                               failures, "" if failures == 1 else "s"))
sys.exit(1 if failures else 0)
