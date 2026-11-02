// Shared timeline kit for EvoGraph-Quant launch video scenes.
// Contract for every scene page (video/scenes/sNN_*.html):
//   - 1920x1080 stage: <div id="stage"> ... </div>
//   - window.SCENE = { duration: <seconds>, still: <seconds for the key still>, title: "..." }
//   - window.render(t): sets the WHOLE visual state for time t (seconds). Must be pure/deterministic:
//     same t -> same pixels, no Math.random() without a seed, no Date.now(), no CSS transitions.
//   - CSS @keyframes are allowed: the harness pauses every animation and seeks currentTime = t.
// Preview: open the HTML in a browser -> plays in real time, loops. Add ?t=4.2 to freeze a frame.
// Capture: video/capture.mjs sets window.__CAPTURE__ = true and calls window.__seek(t) per frame.

(function () {
  const K = {};
  K.clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  K.lerp = (a, b, p) => a + (b - a) * p;
  // progress of t inside [start, start+dur], 0..1
  K.prog = (t, start, dur) => K.clamp((t - start) / dur);
  K.ease = {
    linear: (p) => p,
    outCubic: (p) => 1 - Math.pow(1 - p, 3),
    inOutCubic: (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2),
    outBack: (p) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2); },
    outExpo: (p) => (p === 1 ? 1 : 1 - Math.pow(2, -10 * p)),
  };
  // eased progress helper
  K.ep = (t, start, dur, e = "outCubic") => K.ease[e](K.prog(t, start, dur));
  // typewriter: first n chars of text at time t
  K.type = (text, t, start, cps = 40) => text.slice(0, Math.max(0, Math.floor((t - start) * cps)));
  // count-up number
  K.count = (to, t, start, dur, from = 0, e = "outExpo") => K.lerp(from, to, K.ep(t, start, dur, e));
  K.money = (x) => "$" + Math.round(x).toLocaleString("en-US");
  K.moneyM = (x, d = 1) => "$" + (x / 1e6).toFixed(d) + "M";
  // seeded PRNG for deterministic "randomness"
  K.rng = (seed) => () => { seed |= 0; seed = (seed + 0x6D2B79F5) | 0; let r = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r; return ((r ^ (r >>> 14)) >>> 0) / 4294967296; };
  // set style shorthand
  K.css = (el, obj) => { if (!el) return; for (const k in obj) el.style[k] = obj[k]; };
  K.$ = (sel) => document.querySelector(sel);
  K.$$ = (sel) => Array.from(document.querySelectorAll(sel));
  // fade+rise in, optional fade out: returns {opacity, transform}
  K.enter = (t, start, dur = 0.6, rise = 24, outAt = null, outDur = 0.4) => {
    let p = K.ep(t, start, dur);
    if (outAt !== null) p *= 1 - K.ep(t, outAt, outDur, "inOutCubic");
    return { opacity: String(p), transform: `translateY(${(1 - K.ep(t, start, dur)) * rise}px)` };
  };
  // SVG path draw-on: el must be <path>/<line>/<polyline>; p in 0..1
  K.draw = (el, p) => { if (!el) return; const L = el.getTotalLength ? el.getTotalLength() : 1000;
    el.style.strokeDasharray = L; el.style.strokeDashoffset = String(L * (1 - p)); };
  // point along an SVG path at fraction p
  K.along = (el, p) => { const L = el.getTotalLength(); return el.getPointAtLength(L * K.clamp(p)); };
  window.K = K;

  function seek(t) {
    document.getAnimations().forEach((a) => { a.pause(); a.currentTime = t * 1000; });
    if (typeof window.render === "function") window.render(t);
  }
  window.__seek = seek;

  window.addEventListener("load", () => {
    // Fit the 1920x1080 stage to the window for preview (capture uses a 1920x1080 viewport -> scale 1).
    const stage = document.getElementById("stage");
    const fit = () => { if (!stage) return; const s = Math.min(innerWidth / 1920, innerHeight / 1080);
      stage.style.transform = `scale(${s})`; stage.style.transformOrigin = "0 0";
      document.body.style.width = 1920 * s + "px"; document.body.style.height = 1080 * s + "px"; };
    fit(); addEventListener("resize", fit);
    if (window.__CAPTURE__) return;
    const q = new URLSearchParams(location.search);
    if (q.has("t")) { seek(parseFloat(q.get("t"))); return; }
    const dur = (window.SCENE && window.SCENE.duration) || 6;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { seek((window.SCENE && window.SCENE.still) || dur * 0.7); return; }
    const t0 = performance.now();
    const loop = (now) => { seek(((now - t0) / 1000) % (dur + 0.8)); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  });
})();
