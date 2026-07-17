// Catalyst — runtime verification of agent-emitted A2UI surfaces against a catalog.
// Mirror of src/verify.py. Enforces output/catalog.json on a surface: every node is
// validated against the catalog's anyComponent / component schemas (unevaluatedProperties:false
// + const/enum discriminated union catches unknown components, bad variants, missing required
// props, and extra/reworded props).
//
// Validation engine: AJV (draft 2020-12) when a global is available (window.Ajv2020 /
// window.ajv2020 / Ajv) — load it e.g. from a CDN before this file. Without AJV it falls back
// to a focused validator that performs the same const/enum/required/closed-property checks.
//
// Failure policy is split by consequence (x-consequential on the catalog component, mirrored
// in output/consequential.json):
//   * non-consequential fails -> REPAIR (drop unknown props / snap bad variant to default enum),
//                                re-validate, keep if valid else drop. Logged.
//   * consequential fails     -> REJECT. Never coerced. Replaced with an inline error node.
//
//   verifySurface(surface, catalog, { lenient, consequential }) ->
//     { ok, lenient, results:[{component,consequential,valid,action,errors}], rejected:[...],
//       repaired:<surface> }
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;   // node / tests
  if (typeof window !== "undefined") window.verifySurface = api.verifySurface;  // browser global
})(this, function () {
  // A2UI common props allowed on every node (ComponentCommon + CatalogComponentCommon).
  // unevaluatedProperties:false makes each component's prop set closed except for these.
  const COMMON_PROPS = ["component", "id", "children", "child", "weight"];

  // Stub of the shared A2UI common_types the catalog $refs (only the shapes we need).
  const COMMON_TYPES = {
    $id: "https://a2ui.org/specification/v0_9/common_types.json",
    $defs: {
      ComponentCommon: {
        type: "object",
        properties: {
          component: { type: "string" },
          id: { type: "string" },
          children: { type: "array", items: { type: ["string", "object"] } },
          child: { type: ["string", "object"] },
        },
      },
      DynamicString: { type: ["string", "object"] },
    },
  };

  function innerSchema(comp) {
    const allOf = (comp && comp.allOf) || [];
    for (const sub of allOf) if (sub && sub.properties) return sub;
    return {};
  }
  function componentInfo(catalog, name) {
    const comp = catalog.components && catalog.components[name];
    if (!comp) return null;
    const inner = innerSchema(comp);
    const props = inner.properties || {};
    const enums = {};
    for (const p in props) if (props[p] && Array.isArray(props[p].enum)) enums[p] = props[p].enum;
    const allowed = new Set(Object.keys(props).concat(COMMON_PROPS));
    return { required: inner.required || [], enums, allowed };
  }
  function consequentialSet(catalog, override) {
    if (override) return new Set(override);
    const s = new Set();
    const comps = catalog.components || {};
    for (const n in comps) if (comps[n] && comps[n]["x-consequential"] === true) s.add(n);
    return s;
  }

  // ---- validation engine: AJV if available, else a focused mirror ----
  function getAjv() {
    const g = (typeof window !== "undefined" && window) || (typeof globalThis !== "undefined" && globalThis) || {};
    return g.Ajv2020 || (g.ajv2020 && (g.ajv2020.default || g.ajv2020)) || g.Ajv || null;
  }
  function makeValidator(catalog) {
    const Ajv = getAjv();
    if (Ajv) {
      const ajv = new Ajv({ strict: false, allErrors: true });
      ajv.addSchema(COMMON_TYPES);
      ajv.addSchema(catalog);
      const fn = ajv.compile({ $ref: catalog.$id + "#/$defs/anyComponent" });
      return (node) => (fn(node) ? [] : (fn.errors || []).map((e) => (e.instancePath || "") + " " + e.message));
    }
    // fallback: same checks the schema would enforce
    return (node) => {
      const name = node.component;
      const info = componentInfo(catalog, name);
      if (!info) return ["unknown component '" + name + "'"];
      const errs = [];
      for (const r of info.required) if (!(r in node)) errs.push("missing required property '" + r + "'");
      for (const p in info.enums) if (p in node && info.enums[p].indexOf(node[p]) === -1)
        errs.push("'" + p + "' must be one of " + JSON.stringify(info.enums[p]));
      for (const k in node) if (!info.allowed.has(k)) errs.push("unevaluated (unknown) property '" + k + "'");
      return errs;
    };
  }

  function errorNode(name, why) {
    return {
      component: "Alert", tone: "danger",
      title: "Blocked: " + (name || "unknown"),
      description: (name || "component") + " failed catalog verification and was not rendered (" + why + ").",
    };
  }
  function repair(node, info) {
    const log = [];
    for (const k of Object.keys(node)) if (!info.allowed.has(k)) { delete node[k]; log.push("dropped unknown prop '" + k + "'"); }
    for (const p in info.enums) {
      const allowed = info.enums[p];
      if (p in node && allowed.indexOf(node[p]) === -1 && allowed.length) {
        log.push("snapped '" + p + "' '" + node[p] + "' -> '" + allowed[0] + "'");
        node[p] = allowed[0];
      }
    }
    const missing = info.required.filter((r) => !(r in node));
    return { log, missing };
  }

  function verifySurface(surface, catalog, opts) {
    opts = opts || {};
    const lenient = !!opts.lenient;
    // passUnknown: components the catalog doesn't define are kept as-is (rendered by a superset
    // renderer) instead of dropped. Lets a small "consequential contract" catalog run against a
    // rich renderer — only the consequential components it DOES define are enforced.
    const passUnknown = !!opts.passUnknown;
    const validate = makeValidator(catalog);
    const conseq = consequentialSet(catalog, opts.consequential);
    const results = [], rejected = [];

    function process(value) {
      if (Array.isArray(value)) {
        const out = [];
        for (const item of value) { const r = process(item); if (r !== null && r !== undefined) out.push(r); }
        return out;
      }
      if (value && typeof value === "object" && value.component) {
        const node = JSON.parse(JSON.stringify(value));
        const name = node.component;
        const isConseq = conseq.has(name);
        const known = componentInfo(catalog, name) !== null;
        // A component the catalog doesn't define: keep it (rich renderer handles it) when passUnknown.
        if (!known && passUnknown) {
          results.push({ component: name, consequential: false, valid: true, action: "keep", errors: [] });
          for (const k of Object.keys(node)) {
            const v = node[k];
            if (Array.isArray(v) || (v && typeof v === "object")) node[k] = process(v);
          }
          return node;
        }
        let errors = validate(node);
        let action;
        if (errors.length === 0) action = "keep";
        else if (isConseq) action = "reject";
        else if (!known) action = "drop";
        else {
          const info = componentInfo(catalog, name);
          const rep = repair(node, info);
          const errors2 = validate(node);
          if (errors2.length === 0 && rep.missing.length === 0) {
            action = "repair";
            errors = errors.concat(rep.log.map((m) => ">> " + m));
          } else action = "drop";
        }
        results.push({ component: name, consequential: isConseq,
          valid: action === "keep", action, errors });

        if (action === "reject") { rejected.push({ component: name, errors }); return errorNode(name, errors.join("; ") || "invalid"); }
        if (action === "drop") return null;
        for (const k of Object.keys(node)) {
          const v = node[k];
          if (Array.isArray(v) || (v && typeof v === "object")) node[k] = process(v);
        }
        return node;
      }
      if (value && typeof value === "object") {
        const out = {};
        for (const k in value) out[k] = process(value[k]);
        return out;
      }
      return value;
    }

    const repaired = process(surface);
    const anyInvalid = results.some((r) => r.action !== "keep");
    const ok = lenient ? rejected.length === 0 : !anyInvalid;
    return { ok, lenient, results, rejected, repaired };
  }

  return { verifySurface, COMMON_TYPES, componentInfo, consequentialSet };
});
