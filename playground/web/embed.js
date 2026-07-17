/*
 * Catalyst UI — embeddable renderer.
 * Drop this into your app to render your agent's A2UI trees as real, interactive UI
 * in the Catalyst design system — your agent stays yours, we just paint the screen.
 *
 *   <script src="https://playground-eta-eight.vercel.app/embed.js"></script>
 *
 *   // 1) render a tree your agent returned:
 *   Catalyst.render("#surface", agentResponse.components, { onAction: label => sendToMyAgent(label) });
 *
 *   // 2) or run the whole loop against your own agent endpoint:
 *   const chat = Catalyst.connect({ endpoint: "https://my-api.com/agent", mount: "#surface" });
 *   chat.send("a login screen");
 *
 * Your endpoint contract:  POST { history, components, mode }  ->  { reply, components }
 */
(function () {
  var cs = document.currentScript;
  var ORIGIN = cs && cs.src ? new URL(cs.src).origin : "https://playground-eta-eight.vercel.app";

  function injectCSS() {
    if (document.getElementById("catalyst-embed-css")) return;
    var l = document.createElement("link");
    l.id = "catalyst-embed-css"; l.rel = "stylesheet"; l.href = ORIGIN + "/catalyst-embed.css";
    document.head.appendChild(l);
  }
  function loadRenderer() {
    return new Promise(function (res, rej) {
      if (window.renderA2UI) return res();
      var s = document.createElement("script");
      s.src = ORIGIN + "/renderer.js"; s.onload = res; s.onerror = rej;
      document.head.appendChild(s);
    });
  }
  function el(target) { return typeof target === "string" ? document.querySelector(target) : target; }

  var Catalyst = {
    /* Render an A2UI tree into `target` in the Catalyst design system.
       opts: { onAction(label, el), theme:"light|dark", interactive:true, data } */
    render: function (target, tree, opts) {
      opts = opts || {};
      injectCSS();
      var mount = el(target);
      if (!mount) return Promise.reject(new Error("Catalyst.render: mount element not found"));
      return loadRenderer().then(function () {
        mount.classList.add("gen-surface");
        mount.classList.toggle("ui-dark", opts.theme === "dark");
        if (opts.interactive !== false) mount.setAttribute("data-agent", "");
        var comps = Array.isArray(tree) ? tree : (tree && tree.components) || tree;
        window.renderA2UI(comps, opts.data || null, mount);
        if (opts.onAction && !mount.__catBound) {           // taps flow back to your agent
          mount.__catBound = true;
          mount.addEventListener("click", function (e) {
            var b = e.target.closest(".ui-btn, .ui-card");
            if (!b) return;
            var lbl = b.classList.contains("ui-btn") ? b.textContent.trim()
                    : (b.querySelector(".ui-title, .ui-text") || b).textContent.trim();
            if (lbl) opts.onAction(lbl, b);
          });
        }
        return mount;
      });
    },

    /* Run the full conversation loop against YOUR agent endpoint and render each reply.
       opts: { endpoint, mount, mode, theme, headers, onReply(reply, components) }
       Returns { send(text), history }. */
    connect: function (opts) {
      var self = this, history = [];
      function send(text) {
        history.push({ role: "user", content: text });
        return fetch(opts.endpoint, {
          method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, opts.headers || {}),
          body: JSON.stringify({ history: history, components: null, mode: opts.mode || "chat" })
        }).then(function (r) { return r.json(); }).then(function (out) {
          history.push({ role: "assistant", content: out.reply || "" });
          if (opts.onReply) opts.onReply(out.reply, out.components);
          if (out.components && out.components.length) {
            self.render(opts.mount, out.components, { theme: opts.theme, onAction: function (label) { send(label); } });
          }
          return out;
        });
      }
      return { send: send, history: history };
    },

    /* Use CATALYST'S generation (metered by your API key) instead of your own agent.
       opts: { apiKey, prompt|history, mode, model, components } -> Promise<{reply, components, credits_remaining}> */
    generate: function (opts) {
      return fetch(ORIGIN + "/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": "Bearer " + opts.apiKey },
        body: JSON.stringify({ prompt: opts.prompt, history: opts.history, mode: opts.mode || "chat",
          model: opts.model, components: opts.components })
      }).then(function (r) { return r.json(); });
    },

    /* Full hosted experience: Catalyst generates AND renders. opts: { apiKey, mount, mode, theme }
       Returns { send(text) } — send intent, we generate + paint, taps continue the conversation. */
    app: function (opts) {
      var self = this, history = [];
      function send(text) {
        history.push({ role: "user", content: text });
        return self.generate({ apiKey: opts.apiKey, history: history, mode: opts.mode }).then(function (out) {
          if (out.error) { if (opts.onError) opts.onError(out.error); return out; }
          history.push({ role: "assistant", content: out.reply || "" });
          if (opts.onReply) opts.onReply(out.reply, out.credits_remaining);
          if (out.components && out.components.length) {
            self.render(opts.mount, out.components, { theme: opts.theme, onAction: function (label) { send(label); } });
          }
          return out;
        });
      }
      return { send: send, history: history };
    }
  };

  window.Catalyst = Catalyst;
})();
