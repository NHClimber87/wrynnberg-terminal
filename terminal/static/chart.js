"use strict";
// Price chart for Wrynnberg Terminal. Uses TradingView Lightweight Charts 4.2.3 (Apache 2.0, vendor/).
// Read only: it draws /api/series and never acts in the game.
const PriceChart = (() => {
  const LWC = window.LightweightCharts;
  const COLORS = ["#e6c15a", "#6fb3ff", "#5cc98a", "#ef6b6b", "#b48cf0"];
  const opt = { market: false, listed: true, trades: true, vendor: true, beta: true, old: true, each: false, log: false, range: "all" };
  const OLD = "#e08cd8";   // old fresh-start average line
  const AHEAD_H = 48;      // "All" shows this many hours of the old pattern after the newest scan
  let chart = null, floorS = null, marketS = null, qtyS = null, oldS = null, cmpS = [], eachS = [];
  let cur = null;                 // series payload of the main item
  let compare = [];               // payloads of compare items
  const recent = [];              // recently charted item ids
  const cache = new Map();
  let lastId = null, pending = null;

  const plain = h => String(h).replace(/<[^>]+>/g, "");
  const m = v => plain(money(v));
  const t = ts => plain(tm(ts));

  async function get(id) {
    if (cache.has(id)) return cache.get(id);
    const r = await fetch(`/api/series?id=${id}`);
    const j = r.ok ? await r.json() : null;
    cache.set(id, j);
    return j;
  }
  function clearCache() { cache.clear(); }

  function el() { return document.getElementById("chart"); }

  function frame() {
    const root = el();
    root.innerHTML = `
      <div class="ch-head">
        <span class="n">C</span><h2>Price chart</h2>
        <span id="ch-title" class="ch-title"></span>
        <span id="ch-stats" class="ch-stats"></span>
      </div>
      <div class="ch-tools">
        <span class="grp" aria-label="Time range">${["all", "24h", "12h", "6h"].map(r => `<button data-range="${r}">${r === "all" ? "All" : r}</button>`).join("")}</span>
        <span class="grp" aria-label="Layers">
          <button data-opt="listed" title="Quantity listed (supply) as bars">Listed</button>
          <button data-opt="market" title="Market price line">Market</button>
          <button data-opt="trades" title="Your Midas buys and sales">Trades</button>
          <button data-opt="vendor" title="Vendor sell price line">Vendor</button>
          <button data-opt="beta" title="Late-beta floor median line (BETA)">Beta ref</button>
          <button data-opt="old" title="Average of past fresh Classic starts, same hours after launch, scaled to the live level (key o)">Old starts</button>
          <button data-opt="each" title="One thin line per past fresh start, scaled the same way">Each start</button>
          <button data-opt="log" title="Log price scale (key l)">Log</button>
        </span>
        <span class="grp"><button data-act="fit" title="Fit all data (key f)">Fit</button></span>
        <span id="ch-cmp" class="grp cmp"></span>
      </div>
      <div class="ch-recent" id="ch-recent"></div>
      <div class="ch-body"><div id="ch-canvas"></div><div id="ch-legend" class="ch-legend"></div>
        <div id="ch-empty" class="ch-empty">Select a row in the list (click, or j / k). The chart follows the cursor.</div></div>
      <div class="sub" id="ch-foot"></div>`;
    root.addEventListener("click", onClick);
    chart = LWC.createChart(document.getElementById("ch-canvas"), {
      autoSize: true,
      layout: { background: { color: "#11161c" }, textColor: "#8592a0", fontFamily: getComputedStyle(document.body).fontFamily, fontSize: 11 },
      grid: { vertLines: { color: "#1a2129" }, horzLines: { color: "#1a2129" } },
      crosshair: { mode: LWC.CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#2d3742", scaleMargins: { top: 0.1, bottom: 0.04 } },
      timeScale: { borderColor: "#2d3742", timeVisible: true, secondsVisible: false, rightOffset: 2,
                   tickMarkFormatter: ts => t(ts).slice(6) },
      localization: { timeFormatter: ts => t(ts), priceFormatter: p => m(p) },
    });
    const pf = { type: "custom", minMove: 1, formatter: p => m(p) };
    qtyS = chart.addHistogramSeries({ priceScaleId: "qty", color: "rgba(111,179,255,.28)", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false, title: "listed" });
    chart.priceScale("qty").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });   // supply bars under the price, as a volume overlay
    oldS = chart.addLineSeries({ color: OLD, lineWidth: 2, lineStyle: LWC.LineStyle.LargeDashed, priceFormat: pf, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, title: "old starts" });
    marketS = chart.addLineSeries({ color: "#8592a0", lineWidth: 1, lineStyle: LWC.LineStyle.Dashed, priceFormat: pf, lastValueVisible: false, priceLineVisible: false, title: "market" });
    floorS = chart.addLineSeries({ color: COLORS[0], lineWidth: 2, pointMarkersVisible: true, pointMarkersRadius: 3, priceFormat: pf, title: "floor",
      autoscaleInfoProvider: orig => { const r = orig(); if (r && r.priceRange) r.priceRange.minValue = Math.max(0, r.priceRange.minValue); return r; } });
    chart.subscribeCrosshairMove(legend);
    syncButtons();
  }

  function onClick(e) {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.range) { opt.range = b.dataset.range; applyRange(); }
    else if (b.dataset.opt) { opt[b.dataset.opt] = !opt[b.dataset.opt]; draw(); }
    else if (b.dataset.act === "fit") { opt.range = "all"; applyRange(); }
    else if (b.dataset.rm) { compare = compare.filter(c => c.item_id !== +b.dataset.rm); draw(); }
    else if (b.dataset.go) show(+b.dataset.go, true);
    syncButtons();
  }

  function syncButtons() {
    const root = el(); if (!root) return;
    root.querySelectorAll("[data-range]").forEach(b => b.classList.toggle("on", b.dataset.range === opt.range));
    root.querySelectorAll("[data-opt]").forEach(b => b.classList.toggle("on", !!opt[b.dataset.opt]));
  }

  function applyRange() {
    if (!chart || !cur) return;
    const times = cur.scan_times;
    if (opt.range === "all" && times.length && oldOn()) {
      // Live data plus the next AHEAD_H hours of the old pattern, not all 60 days of it.
      const first = Math.min(times[0], ...cur.trades.map(x => x.ts));
      chart.timeScale().setVisibleRange({ from: first, to: times[times.length - 1] + AHEAD_H * 3600 }); return;
    }
    if (opt.range === "all" || !times.length) { chart.timeScale().fitContent(); return; }
    const last = times[times.length - 1], h = parseInt(opt.range, 10);
    chart.timeScale().setVisibleRange({ from: last - h * 3600, to: last });
  }

  function pctBase(p) { const f = p.points.find(x => x.floor !== null); return f ? f.floor : null; }

  function draw() {
    if (!chart) frame();
    const has = !!cur && cur.points.length + cur.trades.length > 0;
    const empty = document.getElementById("ch-empty");
    empty.style.display = has ? "none" : "flex";
    if (cur && !has) empty.textContent = `No live scan lists ${cur.name || "item " + cur.item_id}, and no Midas trade has it. Nothing to chart.`;
    cmpS.forEach(s => chart.removeSeries(s)); cmpS = [];
    eachS.forEach(s => chart.removeSeries(s)); eachS = [];
    if (!cur) { floorS.setData([]); marketS.setData([]); qtyS.setData([]); oldS.setData([]); return; }
    const H = cur.history;
    oldS.setData(oldOn() ? H.avg_scaled.map(([time, value]) => ({ time, value })) : []);
    if (oldOn() && opt.each) {
      const k = H.match.ratio || 1;
      // Each start scaled by the same live/average ratio, so the shapes share one scale.
      H.starts.forEach(st => {
        const s = chart.addLineSeries({ color: "rgba(224,140,216,.35)", lineWidth: 1, priceFormat: { type: "custom", minMove: 1, formatter: p => m(p) }, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, title: st.key || "" });
        s.setData(st.line.map(([time, v]) => ({ time, value: Math.round(v * k) })));
        eachS.push(s);
      });
    }
    // Trade times join the time axis as empty points, so markers sit at their own time.
    // Trades group into 30-minute buckets per kind. Each bucket joins the time axis as one empty point.
    const groups = opt.trades ? bucketTrades(cur.trades) : [];
    const times = new Set(cur.points.map(p => p.ts));
    const fl = cur.points.filter(p => p.floor !== null).map(p => ({ time: p.ts, value: p.floor }));
    const ws = [...new Set(groups.map(g => g.time).filter(x => !times.has(x)))].map(ts => ({ time: ts }));
    floorS.setData([...fl, ...ws].sort((a, b) => a.time - b.time));
    marketS.setData(opt.market ? cur.points.filter(p => p.market !== null).map(p => ({ time: p.ts, value: p.market })) : []);
    qtyS.setData(opt.listed ? cur.points.map(p => ({ time: p.ts, value: p.qty || 0 })) : []);
    floorS.setMarkers(groups.map(g => g.kind === "sold"
      ? { time: g.time, position: "aboveBar", color: "#5cc98a", shape: "arrowDown", text: `S${g.units} ${m(g.avg)}` }
      : g.kind === "bought"
      ? { time: g.time, position: "belowBar", color: "#6fb3ff", shape: "arrowUp", text: `B${g.units} ${m(g.avg)}` }
      : { time: g.time, position: "aboveBar", color: "#8592a0", shape: "circle", text: `R${g.units}` }));
    floorS.priceLines?.forEach(l => floorS.removePriceLine(l));
    floorS.priceLines = [];
    if (opt.vendor && cur.vendor_sell) floorS.priceLines.push(floorS.createPriceLine({ price: cur.vendor_sell, color: "#8592a0", lineStyle: LWC.LineStyle.Dotted, lineWidth: 1, axisLabelVisible: true, title: "vendor sell" }));
    if (opt.beta && cur.beta_floor_median && !compare.some(c => c.item_id !== cur.item_id)) floorS.priceLines.push(floorS.createPriceLine({ price: cur.beta_floor_median, color: "#b48cf0", lineStyle: LWC.LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: "late-beta BETA" }));
    // Compare: every line as % change from its first listed scan.
    const shown = compare.filter(c => c.item_id !== cur.item_id);
    shown.forEach(c => {
      const i = compare.indexOf(c);
      const s = chart.addLineSeries({ color: COLORS[(i + 1) % COLORS.length], lineWidth: 2, pointMarkersVisible: true, title: c.name || String(c.item_id), priceFormat: { type: "custom", minMove: 1, formatter: p => m(p) } });
      s.setData(c.points.filter(p => p.floor !== null).map(p => ({ time: p.ts, value: p.floor })));
      cmpS.push(s);
    });
    chart.priceScale("right").applyOptions({ mode: shown.length ? LWC.PriceScaleMode.Percentage : opt.log ? LWC.PriceScaleMode.Logarithmic : LWC.PriceScaleMode.Normal });
    header();
    legend({});
    applyRange();
  }

  // Many buys and mail reads land seconds apart: one marker per 30 minutes, kind and source.
  function bucketTrades(trades) {
    const g = new Map();
    for (const x of trades) {
      const k = `${Math.floor(x.ts / 1800)}|${x.kind}|${x.src || ""}`;
      if (!g.has(k)) g.set(k, { time: x.ts, kind: x.kind, src: x.src, units: 0, total: 0, rows: [] });
      const b = g.get(k);
      b.units += x.count; b.rows.push(x);
      b.total += x.kind === "sold" ? (x.net || 0) : x.kind === "bought" ? (x.gross || 0) : 0;
    }
    return [...g.values()].map(b => ({ ...b, avg: b.units ? Math.round(b.total / b.units) : null })).sort((a, b) => a.time - b.time);
  }

  function oldOn() { return opt.old && !!(cur && cur.history && cur.history.avg_scaled.length); }
  function oldAt(line, ts) {
    if (!line || !line.length || ts < line[0][0] || ts > line[line.length - 1][0]) return null;
    for (let i = 1; i < line.length; i++) if (ts <= line[i][0]) {
      const [t0, p0] = line[i - 1], [t1, p1] = line[i];
      return t1 === t0 ? p0 : p0 + (p1 - p0) * (ts - t0) / (t1 - t0);
    }
    return null;
  }
  function oldStat() {
    const H = cur.history;
    if (!H) return `<span class="lbl">old starts</span> <span class="dim">no data for this item</span>`;
    const x = H.match, names = H.starts.map(s => s.label || s.key).join(", ");
    const parts = [`<span class="lbl" title="${esc(names)}">old starts (${H.starts.length})</span>`];
    if (!x.n) parts.push('<span class="dim">no overlap with live scans yet</span>');
    else {
      parts.push(`live ×${x.ratio.toFixed(2)} of old`);
      if (x.r !== undefined && x.r !== null) parts.push(`shape r ${x.r.toFixed(2)}`);
      if (x.same_direction !== undefined) parts.push(`${x.same_direction ? '<span class="up">same way</span>' : '<span class="warn">other way</span>'} (live ${pct(x.live_change * 100)}, old ${pct(x.old_change * 100)})`);
      parts.push(`<span class="dim">n ${x.n}</span>`);
    }
    return parts.join(" ");
  }

  function header() {
    const p = cur.points.filter(x => x.floor !== null);
    const last = p[p.length - 1], first = p[0];
    const ch = last && first && first.floor ? (last.floor - first.floor) / first.floor * 100 : null;
    const lastScan = cur.scan_times[cur.scan_times.length - 1];
    const listedNow = cur.points.length && cur.points[cur.points.length - 1].ts === lastScan;
    document.getElementById("ch-title").innerHTML = `${esc(cur.name ?? "item " + cur.item_id)} <span class="dim">#${cur.item_id}</span>`;
    const sold = cur.trades.filter(x => x.kind === "sold"), bought = cur.trades.filter(x => x.kind === "bought");
    document.getElementById("ch-stats").innerHTML = [
      `<span class="lbl">floor</span> ${listedNow ? money(last.floor) : '<span class="warn">not listed now</span>'}`,
      `<span class="lbl">since ${tm(first?.ts)}</span> ${pct(ch)}`,
      `<span class="lbl">listed</span> ${listedNow ? num(cur.points[cur.points.length - 1].qty) : "0"}`,
      `<span class="lbl">vendor sell</span> ${money(cur.vendor_sell)}`,
      `<span class="lbl">late-beta floor</span> ${bmoney(cur.beta_floor_median)}`,
      `<span class="lbl">your sales</span> ${sold.length} · <span class="lbl">buys</span> ${bought.length}`,
      oldStat(),
    ].join(" · ");
    document.getElementById("ch-cmp").innerHTML = compare.length
      ? `<span class="lbl">compare (% from first scan):</span> ` + compare.map((c, i) => `<button data-rm="${c.item_id}" style="color:${COLORS[(i + 1) % COLORS.length]}" title="Remove from compare">${esc(c.name ?? c.item_id)} ×</button>`).join("")
      : `<span class="lbl">a = hold this item as a compare line, then move the cursor</span>`;
    document.getElementById("ch-recent").innerHTML = `<span class="lbl">recent</span> ` + recent.map(id => `<button data-go="${id}" class="${id === cur.item_id ? "on" : ""}">${esc(cache.get(id)?.name ?? (D && D.item_names[id]) ?? id)}</button>`).join("");
    document.getElementById("ch-foot").innerHTML = `${p.length} of ${cur.scan_times.length} live scans list this item · ${cur.trades.length} Midas trades (sale times are mail-read times) · scroll = zoom, drag = pan, f = fit`;
  }

  function legend(param) {
    const box = document.getElementById("ch-legend"); if (!box || !cur) return;
    let ts = param && param.time;
    let pt = ts ? cur.points.find(p => p.ts === ts) : cur.points[cur.points.length - 1];
    const H = cur.history, oldTs = ts || (pt && pt.ts);
    const day = oldTs && H ? ((oldTs - H.launch_ts) / 86400).toFixed(1) : null;
    const old = oldOn() && oldTs ? (() => {
      const raw = oldAt(H.avg, oldTs);
      return raw === null ? "" : ` · <span style="color:${OLD}">old starts day ${day}</span> ${money(Math.round(raw))} <span class="dim">(scaled ${money(Math.round(raw * (H.match.ratio || 1)))})</span>`;
    })() : "";
    if (!pt) { box.innerHTML = ts ? `${tm(ts)} <span class="dim">· no scan at this time</span>` + old + tradesAt(ts) : ""; return; }
    box.innerHTML = `${tm(pt.ts)} · <span class="lbl">floor</span> ${money(pt.floor)} · <span class="lbl">floor qty</span> ${num(pt.floor_qty)} · <span class="lbl">listed</span> ${num(pt.qty)} · <span class="lbl">listings</span> ${num(pt.listings)} · <span class="lbl">market</span> ${money(pt.market)}` + old
      + compare.map((c, i) => {
          if (c.item_id === cur.item_id) return "";
          const q = c.points.find(p => p.ts === pt.ts);
          return ` · <span style="color:${COLORS[(i + 1) % COLORS.length]}">${esc(c.name)}</span> ${q ? money(q.floor) : "—"}`;
        }).join("")
      + tradesAt(pt.ts);
  }
  function tradesAt(ts) {
    const x = (opt.trades ? bucketTrades(cur.trades) : []).filter(b => b.time === ts).flatMap(b => b.rows);
    if (!x.length) return "";
    return "<br><span class=\"lbl\">S = sold (net each), B = bought (price each), R = returned</span><br>" + x.map(e => `<span class="${e.kind === "sold" ? "up" : e.kind === "bought" ? "acc" : "dim"}">${e.kind}</span> ${e.count} by ${esc(e.char)} ${e.kind === "sold" ? "net " + money(e.net) : e.kind === "bought" ? money(e.gross) + " " + e.src : ""}`).join(" · ");
  }

  // Follow the list cursor: the newest request wins, so fast j/k does not flicker.
  async function show(id, force) {
    if (!id || (!force && id === lastId)) return;
    lastId = id;
    const req = pending = get(id);
    const s = await req;
    if (req !== pending) return;
    cur = s || { item_id: id, name: (D && D.item_names[id]) || null, points: [], trades: [], scan_times: [], vendor_sell: null, beta_floor_median: null };
    const k = recent.indexOf(id); if (k >= 0) recent.splice(k, 1);
    recent.unshift(id); recent.length = Math.min(recent.length, 8);
    draw();
  }

  async function addCompare(id) {
    if (!id || !cur || compare.some(c => c.item_id === id)) return false;
    if (compare.length >= 4) compare.shift();
    const s = await get(id); if (!s) return false;
    compare.push(s); draw(); return true;
  }

  return {
    mount() { if (!chart) frame(); },
    show, addCompare, clearCache,
    clearCompare() { compare = []; draw(); },
    fit() { opt.range = "all"; applyRange(); syncButtons(); },
    toggleOld() { opt.old = !opt.old; draw(); syncButtons(); },
    toggleLog() { opt.log = !opt.log; draw(); syncButtons(); },
    redraw() { if (cur) { const id = cur.item_id; cur = null; lastId = null; show(id, true); } },
    current: () => cur && cur.item_id,
  };
})();
