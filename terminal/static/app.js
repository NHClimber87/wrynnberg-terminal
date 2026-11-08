"use strict";
// Wrynnberg Terminal page. Read only: it shows /api/data and never acts in the game.
let D = null;
let active = 0;
let zoomed = false;
let chartMode = false;   // c: list on the left, price chart on the right, a second panel under the list
let dock = 1;            // panel index shown under the list in chart mode
const state = {};   // per panel: {tab, sortKey, sortDir, filter, cursor}
const $ = (s, el = document) => el.querySelector(s);

// ---------- formatting
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function money(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '<span class="dim">—</span>';
  const neg = v < 0; let c = Math.round(Math.abs(v));
  const g = Math.floor(c / 10000), s = Math.floor(c / 100) % 100, cc = c % 100;
  const txt = g ? `${g}g ${String(s).padStart(2, "0")}s ${String(cc).padStart(2, "0")}c`
    : s ? `${s}s ${String(cc).padStart(2, "0")}c` : `${cc}c`;
  return (neg ? "-" : "") + txt;
}
const BETA = '<span class="beta" title="Beta realm value. Not in any live total.">BETA</span>';
const bmoney = v => (v === null || v === undefined) ? money(v) : money(v) + BETA;
const bnum = v => (v === null || v === undefined) ? money(v) : num(v) + BETA;
const num = v => (v === null || v === undefined) ? '<span class="dim">—</span>' : Number(v).toLocaleString("en-US");
function pct(v, invert) {
  if (v === null || v === undefined) return '<span class="dim">—</span>';
  const cls = v > 0 ? (invert ? "down" : "up") : v < 0 ? (invert ? "up" : "down") : "dim";
  return `<span class="${cls}">${v > 0 ? "+" : ""}${v.toFixed(1)}%</span>`;
}
function signed(v) {
  if (v === null || v === undefined) return '<span class="dim">—</span>';
  return `<span class="${v > 0 ? "up" : v < 0 ? "down" : "dim"}">${v > 0 ? "+" : ""}${v.toLocaleString("en-US")}</span>`;
}
function smoney(v) {
  if (v === null || v === undefined) return money(v);
  return `<span class="${v > 0 ? "up" : v < 0 ? "down" : "dim"}">${v > 0 ? "+" : ""}${money(v)}</span>`;
}
function tm(ts) {
  if (!ts) return '<span class="dim">—</span>';
  const d = new Date(ts * 1000), p = n => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function age(ts) {
  if (!ts) return "—";
  const s = Date.now() / 1000 - ts;
  if (s < 3600) return `${Math.max(0, Math.round(s / 60))}m`;
  if (s < 86400 * 2) return `${(s / 3600).toFixed(1)}h`;
  return `${Math.round(s / 86400)}d`;
}
const stp = v => (v === null || v === undefined) ? '<span class="dim">—</span>' : `${Math.round(v * 100)}%`;
// Random-suffix gear: the item-id floor is the lowest listing of ANY suffix ("of the Whale", "of Power").
function sfxTitle(x) {
  const lines = [x.status === "seen" ? "Random suffixes. The floor here mixes all suffixes." : "Green weapon or armor: can carry a random suffix. Check the suffix by hand."];
  if (x.variants && x.variants.length) {
    lines.push(`Floor by suffix (scan ${tm(x.variants_ts).replace(/<[^>]+>/g, "")}):`);
    for (const v of x.variants) lines.push(`  ${v.suffix || v.name}: ${money(v.floor).replace(/<[^>]+>/g, "")} · ${v.qty} listed`);
  } else if (x.status === "seen") lines.push("No floor by suffix yet: AHResearch 1.1.1 records it.");
  if (x.trade_suffixes && x.trade_suffixes.length) lines.push("Suffixes in your trade log: " + x.trade_suffixes.join(", "));
  return lines.join("\n");
}
const sfxTag = id => {
  const x = D && D.suffix && D.suffix[String(id)];
  if (!x) return "";
  return x.status === "seen" ? ` <span class="tag sfx" title="${esc(sfxTitle(x))}">SFX${x.variants.length ? " " + x.variants.length : ""}</span>`
    : ` <span class="tag sfx2" title="${esc(sfxTitle(x))}">sfx?</span>`;
};
const nm = (r, k = "name") => `<span title="${esc(r[k])} (item ${r.item_id ?? r.product_id ?? ""})">${esc(r[k] ?? ("item " + (r.item_id ?? "?")))}</span>${sfxTag(r.item_id ?? r.product_id)}`;
const pinned = id => D && D.watchlist.some(w => w.item_id === id);
const pinMark = id => pinned(id) ? ' <span class="pin" title="On the watchlist">◆</span>' : "";

function spark(series) {
  const pts = series.map(p => p.floor).filter(v => v !== null);
  if (pts.length < 2) return '<span class="dim">—</span>';
  const w = 90, h = 18, lo = Math.min(...pts), hi = Math.max(...pts), n = series.length;
  const xy = series.map((p, i) => p.floor === null ? null : [i * (w - 2) / (n - 1) + 1, h - 2 - (hi === lo ? (h - 4) / 2 : (p.floor - lo) / (hi - lo) * (h - 4))]);
  const path = xy.filter(Boolean).map((q, i) => (i ? "L" : "M") + q[0].toFixed(1) + " " + q[1].toFixed(1)).join(" ");
  const last = pts[pts.length - 1], first = pts[0];
  const col = last > first ? "var(--up)" : last < first ? "var(--down)" : "var(--dim)";
  const title = series.map(p => `${tm(p.ts)}  ${p.floor === null ? "not listed" : money(p.floor).replace(/<[^>]+>/g, "")}`).join("\n");
  return `<svg class="spark" width="${w}" height="${h}" role="img" aria-label="Floor over ${n} live scans"><title>Floor over ${n} live scans (low ${money(lo)}, high ${money(hi)})\n${esc(title)}</title><path d="${path}" fill="none" stroke="${col}" stroke-width="1.3"/></svg>`;
}

// ---------- panels
const C = (k, label, fmt, opt = {}) => ({ k, label, fmt, ...opt });
const lastScan = () => D.scans.length ? D.scans[D.scans.length - 1].ts : null;

const PANELS = [
  {
    id: "movers", title: "Movers",
    asof: () => { const m = D.movers; return `scan ${tm(m.latest_ts)} vs ${tm(m.prev_ts)} · 24h base ${tm(m.base24_ts)} (${m.base24_hours ?? "—"}h)`; },
    tabs: ["Supply falls", "Price moves", "24h supply", "24h price"],
    note: t => t < 2 ? "Last scan against the scan before it. Supply = quantity listed, not sales. Signal: 20+ listed before and 40% or more gone."
      : `Newest scan against the scan nearest 24 hours earlier. Only ${D.movers.base24_hours}h of live scans exist, so the base is the oldest scan.`,
    rows: t => {
      const R = D.movers.rows;
      if (t === 0) return R.filter(r => r.prev_qty >= 10);
      if (t === 1) return R.filter(r => r.floor !== null && r.prev_floor !== null && r.floor !== r.prev_floor);
      if (t === 2) return R.filter(r => r.base_qty >= 10);
      return R.filter(r => r.floor !== null && r.base_floor !== null && r.floor !== r.base_floor);
    },
    cols: t => t === 0 || t === 2 ? [
      C("name", "Item", r => nm(r) + pinMark(r.item_id), { l: 1, cls: "name" }),
      C(t ? "supply_fall24" : "supply_fall", "Signal", r => (t ? r.supply_fall24 : r.supply_fall) ? '<span class="sig">SUPPLY FALL</span>' : ""),
      C(t ? "qty_chg24_pct" : "qty_chg_pct", "Change %", r => pct(t ? r.qty_chg24_pct : r.qty_chg_pct)),
      C(t ? "base_qty" : "prev_qty", "Listed before", r => num(t ? r.base_qty : r.prev_qty)),
      C("qty", "Listed now", r => num(r.qty)),
      C("floor", "Floor now", r => money(r.floor)),
      C(t ? "qty_chg24" : "qty_chg", "Change", r => signed(t ? r.qty_chg24 : r.qty_chg)),
      C(t ? "floor_chg24_pct" : "floor_chg_pct", "Floor %", r => pct(t ? r.floor_chg24_pct : r.floor_chg_pct)),
    ] : [
      C("name", "Item", r => nm(r) + pinMark(r.item_id), { l: 1, cls: "name" }),
      C(t === 1 ? "prev_floor" : "base_floor", "Floor before", r => money(t === 1 ? r.prev_floor : r.base_floor)),
      C("floor", "Floor now", r => money(r.floor)),
      C(t === 1 ? "floor_chg_pct" : "floor_chg24_pct", "Floor %", r => pct(t === 1 ? r.floor_chg_pct : r.floor_chg24_pct)),
      C("qty", "Listed now", r => num(r.qty)),
      C(t === 1 ? "qty_chg" : "qty_chg24", "Listed change", r => signed(t === 1 ? r.qty_chg : r.qty_chg24)),
    ],
    sort: t => t === 0 ? ["qty_chg_pct", 1] : t === 2 ? ["qty_chg24_pct", 1] : t === 1 ? ["floor_chg_pct", -1] : ["floor_chg24_pct", -1],
  },
  {
    id: "watch", title: "Watchlist",
    // Tab 0 = pins. Each item category (data/terminal/categories/) adds one tab with its own index rows.
    get tabs() { return ["Pins", ...((D && D.categories) || []).map(c => c.key === "2h-str-leveling" ? "2H Str 15-32" : c.label)]; },
    asof: t => t ? `${D.categories[t - 1].seen} of ${D.categories[t - 1].members} items seen on the AH · scan ${tm(lastScan())}`
      : `${D.scans.length} live scans ${tm(D.scans[0]?.ts)} → ${tm(lastScan())}`,
    note: t => t ? "Index rows: chain-linked price index of the group (trimmed mean floor change of the items that two scans share), started at the median floor. A new or gone item does not move it. Enter opens the chart: S/B markers are your trades in the whole group. Floor mixes suffixes."
      : "Pins: Midas watch list on any account, plus items pinned here with p. Floor qty = units at the lowest price.",
    rows: t => t ? D.categories[t - 1].rows : D.watchlist,
    cols: t => t ? [
      C("name", "Item", r => r.is_index ? `<b>${esc(r.name)}</b>` : nm(r) + pinMark(r.item_id), { l: 1, cls: "name", sv: r => (r.is_index ? "0" : "1") + (r.name || "") }),
      C("spark", "Floor, all scans", r => spark(r.spark), { nosort: 1 }),
      C("req", "Req", r => r.is_index ? "" : num(r.req), { sv: r => r.is_index ? -1 : r.req }),
      C("type", "Type", r => r.is_index ? "" : esc(r.type), { l: 1 }),
      C("dps", "DPS", r => r.dps ?? ""),
      C("floor", "Floor", r => r.floor === null ? `<span class="warn" title="Not listed in the newest scan">not listed</span>` : money(r.floor)),
      C("floor_chg24_pct", "Floor 24h %", r => pct(r.floor_chg24_pct)),
      C("qty", "Listed", r => num(r.qty)),
      C("qty_chg24", "Listed 24h", r => signed(r.qty_chg24)),
      C("source", "Source", r => r.source ? `<span class="dim">${esc(r.source)}</span>` : "", { l: 1 }),
    ] : [
      C("name", "Item", r => nm(r), { l: 1, cls: "name" }),
      C("spark", "Floor, all scans", r => spark(r.series), { nosort: 1 }),
      C("floor", "Floor", r => r.listed_in_latest ? money(r.floor) : `<span class="warn" title="Not listed in the newest scan">not listed</span>`),
      C("floor_qty", "Floor qty", r => num(r.floor_qty)),
      C("qty", "Listed", r => num(r.qty)),
      C("vendor_sell", "Vendor sell", r => money(r.vendor_sell)),
      C("last_seen_ts", "Last listed", r => tm(r.last_seen_ts)),
      C("pin_source", "Pin", r => `<span class="tag">${esc(r.pin_source)}</span>`, { l: 1 }),
    ],
    sort: t => t ? ["req", 1] : ["name", 1],
  },
  {
    id: "recipes", title: "Recipe flip board",
    asof: () => `AH scan ${tm(lastScan())} · sales from Midas to ${tm(D.book.totals.last_trade_ts)}`,
    tabs: ["All", "Live vendor", "In a capital", "Beta vendor only"],
    note: () => "Margin = floor × 0.95 − vendor cost. Sales are Midas sold/returned rows (mail-read times). Sell-through = sold ÷ (sold + returned).",
    rows: t => D.recipes.filter(r => t === 0 || (t === 1 && r.cost_source === "live") || (t === 2 && r.in_capital) || (t === 3 && r.cost_source === "beta")),
    cols: () => [
      C("name", "Recipe", r => nm(r) + pinMark(r.item_id), { l: 1, cls: "name" }),
      C("margin", "Margin each", r => r.margin !== null ? smoney(r.margin) : (r.beta_margin_beta_cost !== undefined ? bmoney(r.beta_margin_beta_cost) : money(null)), { sv: r => r.margin ?? -1e12 }),
      C("roi_pct", "ROI", r => r.roi_pct === null ? money(null) : `${r.roi_pct}%`),
      C("sell_through", "Sell-thru", r => stp(r.sell_through)),
      C("cost", "Vendor cost", r => r.cost_source === "live" ? money(r.cost) : bmoney(r.beta_cost), { sv: r => r.cost ?? r.beta_cost }),
      C("floor", "Floor", r => money(r.floor)),
      C("qty", "Listed", r => num(r.qty)),
      C("in_capital", "Capital", r => r.in_capital ? '<span class="tag cap">CAPITAL</span>' : '<span class="dim">no</span>', { l: 1 }),
      C("vendor", "Vendor", r => `<span title="${esc(r.vendors.map(v => v.join(", ")).join("\n"))}">${esc(r.vendor)}${r.vendors.length > 1 ? ` +${r.vendors.length - 1}` : ""}</span>`, { l: 1 }),
      C("location", "Location", r => esc(r.location), { l: 1 }),
      C("sold", "Sold", r => num(r.sold)),
      C("returned", "Returned", r => num(r.returned)),
      C("beta_floor_median", "Late-beta floor", r => bmoney(r.beta_floor_median)),
      C("beta_sold", "Beta sold/ret", r => r.beta_sold || r.beta_returned ? `${r.beta_sold}/${r.beta_returned}${BETA}` : '<span class="dim">—</span>'),
    ],
    sort: () => ["margin", -1],
  },
  {
    id: "crafts", title: "Craft margins",
    asof: () => `AH floors to ${tm(lastScan())} · skills from AltsForever`,
    tabs: ["Alchemy", "Tailoring", "Enchanting", "Herbs & mats"],
    note: t => t === 3 ? "Raw sale: net = floor × 0.95. The late-beta floor is the upside reference."
      : t === 2 ? "No Enchanting recipe is captured yet. Enchanting mats are on the Herbs & mats tab."
      : "Reagent cost uses the live vendor price, else the AH floor. Forever recipes come from AltsForever. Classic recipes are not checked in Forever.",
    rows: t => t === 3 ? D.crafts.herbs : D.crafts.rows.filter(r => r.profession === ["Alchemy", "Tailoring", "Enchanting"][t]),
    cols: t => t === 3 ? [
      C("name", "Item", r => nm(r) + pinMark(r.item_id), { l: 1, cls: "name" }),
      C("group", "Group", r => esc(r.group), { l: 1 }),
      C("floor", "Floor", r => money(r.floor)),
      C("floor_qty", "Floor qty", r => num(r.floor_qty)),
      C("qty", "Listed", r => num(r.qty)),
      C("net_each", "Net each", r => money(r.net_each)),
      C("vendor_sell", "Vendor sell", r => money(r.vendor_sell)),
      C("seen_ts", "Last listed", r => tm(r.seen_ts)),
      C("beta_floor_median", "Late-beta floor", r => bmoney(r.beta_floor_median)),
    ] : [
      C("product", "Product", r => nm(r, "product") + pinMark(r.product_id), { l: 1, cls: "name" }),
      C("margin", "Margin each", r => smoney(r.margin), { sv: r => r.margin ?? -1e12 }),
      C("source", "Source", r => r.source.startsWith("classic") ? '<span class="tag unv" title="Classic recipe, not checked in Forever">classic, unverified</span>' : '<span class="tag" title="Captured from the game by AltsForever">Forever</span>', { l: 1 }),
      C("can_craft_now", "Can craft now", r => r.can_craft_now.length ? esc(r.can_craft_now.join(", ")) : '<span class="dim">nobody</span>', { l: 1 }),
      C("reagent_cost", "Reagent cost", r => r.reagent_cost !== null ? money(r.reagent_cost) : `<span class="warn">no price: ${esc(r.missing_prices.join(", "))}</span>`),
      C("floor", "Product floor", r => money(r.floor)),
      C("qty", "Listed", r => num(r.qty)),
      C("skill_needed", "Skill", r => r.skill_needed !== null ? num(r.skill_needed) : `<span class="dim" title="Known recipe. Skill-ups end at ${r.grey_at}.">known · grey ${r.grey_at ?? "—"}</span>`),
      C("reagents", "Reagents (unit cost)", r => r.reagents.map(x => `${x.count}× ${esc(x.name ?? ("item " + x.item_id))} <span class="dim">${money(x.unit_cost)}${x.source === "vendor" ? " vnd" : x.source === "AH floor" ? " AH" : ""}</span>`).join(", "), { l: 1, nosort: 1 }),
      C("beta_floor_median", "Late-beta floor", r => bmoney(r.beta_floor_median)),
    ],
    sort: t => t === 3 ? ["floor", -1] : ["margin", -1],
  },
  {
    id: "fv", title: "Floor vs vendor",
    asof: () => `scan ${tm(lastScan())} · trade goods only`,
    tabs: ["All", "100+ listed"],
    note: () => "Ratio = AH floor ÷ vendor sell price. Worst loss = buy at the floor, sell to a vendor. Upside = late-beta floor × 0.95 − floor.",
    rows: t => t ? D.floor_vendor.filter(r => r.qty >= 100) : D.floor_vendor,
    cols: () => [
      C("name", "Item", r => nm(r) + pinMark(r.item_id), { l: 1, cls: "name" }),
      C("ratio", "Ratio", r => `<span class="${r.ratio < 1.5 ? "up" : r.ratio > 5 ? "dim" : ""}">×${r.ratio.toFixed(2)}</span>`),
      C("floor", "Floor", r => money(r.floor)),
      C("vendor_sell", "Vendor sell", r => money(r.vendor_sell)),
      C("worst_loss_each", "Worst loss each", r => smoney(r.worst_loss_each)),
      C("floor_qty", "Floor qty", r => num(r.floor_qty)),
      C("worst_loss_floor_stack", "Worst loss, floor qty", r => smoney(r.worst_loss_floor_stack)),
      C("qty", "Listed", r => num(r.qty)),
      C("beta_floor_median", "Late-beta floor", r => bmoney(r.beta_floor_median)),
      C("beta_upside_each", "Upside each", r => r.beta_upside_each === null ? money(null) : bmoney(r.beta_upside_each)),
    ],
    sort: () => ["ratio", 1],
  },
  {
    id: "book", title: "Book",
    asof: () => `trade log ${tm(D.book.totals.first_trade_ts)} → ${tm(D.book.totals.last_trade_ts)}`,
    tabs: ["Profit by item", "Inventory", "Auctions", "Characters"],
    note: t => [
      "Live Midas trade log. Sale times are mail-read times. Realized = net sales − average purchase cost of the units sold (only where purchases cover the units).",
      "Bags, bank and mail of every live character. Value = units × floor × 0.95 at the newest scan.",
      "Open auctions from Midas. Buyout is the total for the stack. Undercut = the floor is lower than your price each. SFX items: \"check suffix\" until AHResearch 1.1.1 gives the floor of your suffix.",
      "Each character with the time of its newest bags, bank, mail and auctions data.",
    ][t],
    rows: t => [D.book.pnl, D.book.inventory, D.book.auctions, D.book.chars][t],
    cols: t => [[
      C("name", "Item", r => nm(r) + pinMark(r.item_id), { l: 1, cls: "name" }),
      C("net", "Net sales", r => money(r.net)),
      C("realized", "Realized", r => r.realized !== null ? smoney(r.realized) : `<span class="dim" title="Cost basis: ${r.cost_basis}">n/a</span>`, { sv: r => r.realized ?? -1e12 }),
      C("sold", "Sales", r => num(r.sold)),
      C("units", "Units sold", r => num(r.units)),
      C("net_per_sale", "Net per sale", r => money(r.net_per_sale)),
      C("returned", "Returned", r => num(r.returned)),
      C("sell_through", "Sell-thru", r => stp(r.sell_through)),
      C("bought", "Bought units", r => num(r.ah_units + r.vendor_units), { sv: r => r.ah_units + r.vendor_units }),
      C("cost", "Spent", r => money(r.ah_cost + r.vendor_cost), { sv: r => r.ah_cost + r.vendor_cost }),
      C("unit_cost", "Avg cost", r => money(r.unit_cost)),
      C("last", "Last sale", r => tm(r.last)),
    ], [
      C("name", "Item", r => nm(r) + pinMark(r.item_id), { l: 1, cls: "name" }),
      C("count", "Units", r => num(r.count)),
      C("where", "Bags / bank / mail", r => `${r.where.bags || 0} / ${r.where.bank || 0} / ${r.where.mail || 0}`, { nosort: 1 }),
      C("by_char", "Held by", r => esc(Object.entries(r.by_char).map(([k, v]) => `${k} ${v}`).join(", ")), { l: 1, nosort: 1 }),
      C("floor", "Floor", r => r.floor_mixed ? `<span class="warn" title="Lowest listing of any suffix: a low bound.${r.suffixes_held.length ? " You hold: " + esc(r.suffixes_held.join(", ")) : ""}">≥ ${money(r.floor)}</span>` : money(r.floor), { sv: r => r.floor }),
      C("value_net", "Value net", r => r.floor_mixed ? `<span class="warn">≥ ${money(r.value_net)}</span>` : money(r.value_net), { sv: r => r.value_net }),
    ], [
      C("char", "Character", r => esc(r.char), { l: 1 }),
      C("name", "Item", r => nm(r) + pinMark(r.item_id), { l: 1, cls: "name" }),
      C("qty", "Qty", r => num(r.qty)),
      C("buyout_total", "Buyout total", r => money(r.buyout_total)),
      C("buyout_each", "Buyout each", r => money(r.buyout_each)),
      C("floor", "Floor now", r => r.undercut_note === "same suffix" ? `<span title="Floor of ${esc(r.suffix_name)}">${money(r.floor)}</span>` : r.suffix_status ? `<span class="warn" title="Lowest listing of any suffix">${money(r.floor)}</span>` : money(r.floor)),
      C("undercut", "Undercut", r => r.undercut === null ? `<span class="warn" title="${esc(r.undercut_note)}">check suffix</span>` : r.undercut ? `<span class="sig"${r.suffix_name ? ` title="${esc(r.suffix_name)}: same suffix"` : ""}>UNDERCUT</span>` : '<span class="dim">no</span>', { sv: r => r.undercut === null ? 0.5 : +r.undercut }),
      C("net_if_sold", "Net if sold", r => money(r.net_if_sold)),
      C("auctions_ts", "Seen", r => tm(r.auctions_ts)),
    ], [
      C("name", "Character", r => esc(r.name), { l: 1 }),
      C("role", "Role", r => `<span class="tag">${r.role === "main" ? "main" : "AH/bank alt"}</span>`, { l: 1 }),
      C("class", "Class", r => esc((r.class || "").toLowerCase()), { l: 1 }),
      C("level", "Level", r => num(r.level)),
      C("profs", "Professions (skill)", r => esc(Object.entries(r.profs || {}).map(([k, v]) => `${k} ${v}`).join(", ")), { l: 1, nosort: 1 }),
      C("money", "Money", r => `<span title="${r.money_source}, ${tm(r.money_ts)}">${money(r.money)}</span>`),
      C("units", "Units held", r => num(r.units)),
      C("bags_ts", "Bags", r => tm(r.bags_ts)),
      C("bank_ts", "Bank", r => tm(r.bank_ts)),
      C("mail_ts", "Mail", r => tm(r.mail_ts)),
      C("auctions_ts", "Auctions", r => tm(r.auctions_ts)),
    ]][t],
    sort: t => [["net", -1], ["value_net", -1], ["char", 1], ["name", 1]][t],
  },
];

// ---------- render
function header() {
  $("#realm").textContent = `${D.live_realm} · ${D.faction}`;
  const stale = D.stale_seconds;
  $("#accounts").innerHTML = D.accounts.map(a => {
    const old = !a.data_ts || Date.now() / 1000 - a.data_ts > stale;
    const errs = Object.keys(a.copy_errors || {}).length;
    return `<span class="acct${old ? " stale" : ""}" title="SavedVariables written ${tm(a.data_ts)} (game writes on /reload or logout). Copied to G2 ${tm(a.copied_at)}.${errs ? " Copy errors: " + esc(JSON.stringify(a.copy_errors)) : ""}">
      <b>${esc(a.account.replace("acct-", "").replace("_", "#"))}</b>
      <span class="lbl">data</span> ${tm(a.data_ts).slice(6)} <span class="age">${age(a.data_ts)}${old ? " STALE" : ""}</span>
      <span class="lbl">scan</span> ${tm(a.newest_live_scan_ts)}${errs ? ' <span class="down">copy error</span>' : ""}</span>`;
  }).join("");
  const f = D.fetch || {};
  const g = $("#g1");
  g.className = "g1 " + (f.g1 === "ok" ? "ok" : f.g1 ? "bad" : "");
  g.textContent = f.g1 ? `G1 ${f.g1 === "ok" ? "ok" : "asleep — showing last copy"} · tried ${tm(f.last_attempt).replace(/<[^>]+>/g, "")}` : "G1 not tried";
  g.title = f.g1_error || "";
  const T = D.book.totals;
  const charTs = Math.max(...D.book.chars.map(c => Math.max(c.bags_ts || 0, c.bank_ts || 0, c.mail_ts || 0, c.auctions_ts || 0)));
  const undercut = D.book.auctions.filter(a => a.undercut).length;
  const staleAccts = D.accounts.filter(a => !a.data_ts || Date.now() / 1000 - a.data_ts > D.stale_seconds).map(a => a.account.replace("acct-", "").replace("_", "#"));
  const k = (label, v, d, neg) => `<div class="kpi"><div class="k">${label}</div><div class="v${neg ? " neg" : ""}">${v}</div><div class="d">${d}</div></div>`;
  $("#kpis").innerHTML = [
    k("Money on characters", money(T.money), `${D.book.chars.length} characters · to ${tm(charTs)}`),
    k("Inventory value (net)", money(T.inventory_value_net), `at scan ${tm(lastScan())} · ${T.inventory_unpriced_items} items not listed`),
    k("Open auctions (net if sold)", money(T.auctions_value_net), `${D.book.auctions.length} auctions · <span class="${undercut ? "down" : ""}">${undercut} undercut</span>${T.auctions_suffix_check ? ` · <span class="warn">${T.auctions_suffix_check} check suffix</span>` : ""} (Book tab 3)`),
    k("Sales (net)", money(T.sales_net), `${T.sales} sales · ${T.returns} returned · fees ${money(T.fees)}`),
    k("Realized profit", money(T.realized_covered), "items with a purchase cost basis"),
    k("Trade cash flow", money(T.cash_flow), `sales − buys (AH ${money(T.ah_buys)}, vendor ${money(T.vendor_buys)})`, T.cash_flow < 0),
    k("Live AH scans", num(D.scans.length), `newest ${tm(lastScan())} · ${num(D.scans.at(-1)?.items)} items`),
  ].join("") + (staleAccts.length ? `<div class="kpi stalebar"><div class="k down">STALE data in totals</div><div class="d">${esc(staleAccts.join(", "))}: data older than 6h. Totals include their last copy. /reload in game, then press r.</div></div>` : "");
}

function sortRows(rows, cols, key, dir) {
  const col = cols.find(c => c.k === key);
  const sv = col && col.sv ? col.sv : r => r[key];
  return rows.slice().sort((a, b) => {
    let x = sv(a), y = sv(b);
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === "string") return x.localeCompare(y) * dir;
    if (typeof x === "boolean") { x = +x; y = +y; }
    return (x - y) * dir;
  });
}

function panelRows(i) {
  const P = PANELS[i], S = state[P.id];
  const cols = P.cols(S.tab);
  let rows = P.rows(S.tab);
  if (S.filter) {
    const f = S.filter.toLowerCase();
    rows = rows.filter(r => String(r.name ?? r.product ?? r.char ?? "").toLowerCase().includes(f) || String(r.char ?? "").toLowerCase().includes(f));
  }
  return { cols, rows: sortRows(rows, cols, S.sortKey, S.sortDir) };
}

function renderPanel(i) {
  const P = PANELS[i], S = state[P.id];
  const el = document.getElementById("p-" + P.id);
  const { cols: allCols, rows } = panelRows(i);
  S.cursor = Math.min(S.cursor, Math.max(0, rows.length - 1));
  const tabs = P.tabs ? `<div class="tabs">${P.tabs.map((t, j) => `<button data-tab="${j}" class="${j === S.tab ? "on" : ""}">${t}</button>`).join("")}</div>` : "";
  const note = P.note ? `<div class="note">${P.note(S.tab)}</div>` : "";
  const zoom = zoomed && i === active;
  el.classList.toggle("active", i === active);
  el.classList.toggle("zoom", zoom);
  // Show whole columns only: when the table is wider than the panel, drop columns from the right
  // (the key columns sit next to the name). Full screen (key 1-6) uses a denser text and fits more.
  let n = allCols.length;
  for (;;) {
    const cols = allCols.slice(0, n);
    const head = cols.map(c => `<th class="${c.l ? "l" : ""} ${c.k === S.sortKey ? "sorted" : ""}" data-k="${c.nosort ? "" : c.k}">${c.label}${c.k === S.sortKey ? (S.sortDir > 0 ? " ▲" : " ▼") : ""}</th>`).join("");
    const body = rows.length ? rows.map((r, j) => `<tr class="${j === S.cursor && i === active ? "cur" : ""}" data-j="${j}">${cols.map(c => `<td class="${c.l ? "l" : ""} ${c.cls || ""}">${c.fmt(r)}</td>`).join("")}</tr>`).join("")
      : `<tr><td class="l empty" colspan="${cols.length}">No rows${S.filter ? " match the filter" : ""}.</td></tr>`;
    const hidden = allCols.length - n;
    el.innerHTML = `<div class="ph"><span class="n">${i + 1}</span><h2>${P.title}</h2><span class="asof">${P.asof(S.tab)}</span>
      <input type="search" placeholder="/ filter" value="${esc(S.filter)}" aria-label="Filter ${P.title}">${tabs}</div>${note}
    <div class="body"><table class="t"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
    <div class="sub">${rows.length} rows${hidden ? ` <span class="dim">· ${hidden} more column${hidden > 1 ? "s" : ""} (${esc(allCols.slice(n).map(c => c.label).join(", "))}): ${zoom ? "use a wider window to see them" : `press ${i + 1} for full screen`}</span>` : ""}</div>`;
    const bd = $(".body", el);
    if (n <= 2 || !bd || bd.scrollWidth <= bd.clientWidth + 1) break;
    n--;
  }
}

function cursorItem() {
  const { rows } = panelRows(active);
  const r = rows[state[PANELS[active].id].cursor];
  return r ? (r.item_id ?? r.product_id ?? null) : null;
}
function follow() { if (chartMode) PriceChart.show(cursorItem()); }

function renderAll() {
  if (dock === active) dock = (active + 1) % PANELS.length;
  document.body.classList.toggle("chartmode", chartMode);
  PANELS.forEach((P, i) => {
    const el = document.getElementById("p-" + P.id);
    el.classList.toggle("list", chartMode && i === active);
    el.classList.toggle("dock", chartMode && i === dock);
  });
  if (chartMode) zoomed = false;
  header();
  document.documentElement.style.setProperty("--chrome", ($("#top").offsetHeight + $("#kpis").offsetHeight + 2) + "px");
  document.body.classList.toggle("zoomed", zoomed);
  PANELS.forEach((_, i) => renderPanel(i));
}

function build() {
  const g = $("#grid");
  g.innerHTML = PANELS.map(P => `<section class="panel" id="p-${P.id}" tabindex="-1" aria-label="${P.title}"></section>`).join("")
    + `<section class="chartpanel" id="chart" aria-label="Price chart"></section>`;
  PANELS.forEach((P, i) => {
    const [k, d] = P.sort(0);
    state[P.id] = state[P.id] || { tab: 0, sortKey: k, sortDir: d, filter: "", cursor: 0 };
    const el = document.getElementById("p-" + P.id);
    el.addEventListener("click", e => {
      active = i;
      const S = state[P.id];
      const tab = e.target.closest("[data-tab]");
      if (tab) { S.tab = +tab.dataset.tab; [S.sortKey, S.sortDir] = P.sort(S.tab); S.cursor = 0; }
      const th = e.target.closest("th[data-k]");
      if (th && th.dataset.k) { if (S.sortKey === th.dataset.k) S.sortDir *= -1; else { S.sortKey = th.dataset.k; S.sortDir = -1; } }
      const tr = e.target.closest("tr[data-j]");
      if (tr) S.cursor = +tr.dataset.j;
      if (chartMode && i === dock && !tr) return;   // clicks on the dock header do not swap
      if (chartMode && i === dock) { dock = active; active = i; }
      if (e.target.tagName !== "INPUT") renderAll();
      if (tr) follow();
    });
    el.addEventListener("input", e => {
      if (e.target.tagName !== "INPUT") return;
      state[P.id].filter = e.target.value; state[P.id].cursor = 0;
      const pos = e.target.selectionStart;
      renderPanel(i);
      const inp = $("input", el); inp.focus(); inp.setSelectionRange(pos, pos);
    });
  });
}

// ---------- keys
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("show"), 3500); }

async function togglePin() {
  const P = PANELS[active], S = state[P.id];
  const { rows } = panelRows(active);
  const r = rows[S.cursor]; if (!r) return;
  const id = r.item_id ?? r.product_id;
  if (!id) return toast("This row has no item id.");
  if (id < 0) return toast("An index row cannot be pinned. Its tab is always on.");
  const cur = await (await fetch("/api/pins")).json();
  const add = new Set(cur.add), rem = new Set(cur.remove);
  if (pinned(id)) { add.delete(id); rem.add(id); } else { rem.delete(id); add.add(id); }
  await fetch("/api/pins", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ add: [...add], remove: [...rem] }) });
  toast(`${pinned(id) ? "Unpinned" : "Pinned"} ${r.name ?? r.product ?? id}`);
  await load();
}

async function refresh() {
  toast("Copying changed files from G1…");
  try {
    const r = await (await fetch("/api/refresh", { method: "POST" })).json();
    toast(r.g1 === "ok" ? `G1 ok · ${r.last_copied.length} files copied` : `G1 not reachable — showing the last copy`);
  } catch (e) { toast("Refresh failed: " + e); }
  await load();
}

document.addEventListener("keydown", e => {
  const inInput = e.target.tagName === "INPUT";
  const help = $("#help");
  if (e.key === "Escape") {
    if (!help.hidden) { help.hidden = true; return; }
    if (inInput) { e.target.blur(); if (!e.target.value) return; state[PANELS[active].id].filter = ""; renderPanel(active); return; }
    if (zoomed) { zoomed = false; renderAll(); return; }
    if (chartMode) { chartMode = false; renderAll(); return; }
    state[PANELS[active].id].filter = ""; renderAll(); return;
  }
  if (inInput || e.ctrlKey || e.metaKey || e.altKey) return;
  if (!help.hidden && e.key !== "?") return;   // help is open: only ? and Esc act
  const S = state[PANELS[active].id], P = PANELS[active];
  const dig = /^Digit([1-6])$/.exec(e.code);
  if (chartMode && dig && e.shiftKey) { const i = +dig[1] - 1; if (i === active) { active = dock; } dock = i; renderAll(); follow(); }
  else if (chartMode && /^[1-6]$/.test(e.key)) { const i = +e.key - 1; if (i === dock) dock = active; active = i; renderAll(); follow(); }
  else if (/^[1-6]$/.test(e.key)) { const i = +e.key - 1; if (i === active) zoomed = !zoomed; else { active = i; if (zoomed) zoomed = true; } renderAll(); document.getElementById("p-" + PANELS[active].id).scrollIntoView({ block: "nearest" }); }
  else if (e.key === "c") { chartMode = !chartMode; if (chartMode) PriceChart.mount(); renderAll(); follow(); }
  else if (chartMode && e.key === "Tab") { e.preventDefault(); [active, dock] = [dock, active]; renderAll(); follow(); }
  else if (chartMode && e.key === "b") { do { dock = (dock + 1) % PANELS.length; } while (dock === active); renderAll(); }
  else if (chartMode && e.key === "a") { PriceChart.addCompare(cursorItem()).then(ok => toast(ok ? "Held as a compare line. Move the cursor to compare." : "Not held (already held, no data, or no item id)")); }
  else if (chartMode && e.key === "x") PriceChart.clearCompare();
  else if (chartMode && e.key === "f") PriceChart.fit();
  else if (chartMode && e.key === "l") PriceChart.toggleLog();
  else if (chartMode && e.key === "o") PriceChart.toggleOld();
  else if (e.key === "Enter") { if (!chartMode) { chartMode = true; PriceChart.mount(); renderAll(); } PriceChart.show(cursorItem(), true); }
  else if (e.key === "?") help.hidden = !help.hidden;
  else if (e.key === "/") { e.preventDefault(); $("input", document.getElementById("p-" + P.id)).focus(); }
  else if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); S.cursor++; renderPanel(active); scrollCur(); follow(); }
  else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); S.cursor = Math.max(0, S.cursor - 1); renderPanel(active); scrollCur(); follow(); }
  else if (e.key === "]" || e.key === "[") { if (P.tabs) { S.tab = (S.tab + (e.key === "]" ? 1 : P.tabs.length - 1)) % P.tabs.length; [S.sortKey, S.sortDir] = P.sort(S.tab); S.cursor = 0; renderPanel(active); follow(); } }
  else if (e.key === "s" || e.key === "S") {
    const cols = P.cols(S.tab).filter(c => !c.nosort);
    if (e.key === "S") S.sortDir *= -1;
    else { const j = cols.findIndex(c => c.k === S.sortKey); S.sortKey = cols[(j + 1) % cols.length].k; S.sortDir = -1; }
    renderPanel(active);
  }
  else if (e.key === "p") togglePin();
  else if (e.key === "r") refresh();
});
function scrollCur() { const tr = $(`#p-${PANELS[active].id} tr.cur`); if (tr) tr.scrollIntoView({ block: "nearest" }); }

$("#refresh").addEventListener("click", refresh);
$("#chartbtn").addEventListener("click", () => { chartMode = !chartMode; if (chartMode) PriceChart.mount(); renderAll(); follow(); });
$("#helpbtn").addEventListener("click", () => { $("#help").hidden = false; });
$("#help").addEventListener("click", e => { if (e.target.id === "help") e.target.hidden = true; });

async function load() {
  const r = await fetch("/api/data");
  if (!r.ok) throw new Error(`/api/data answered HTTP ${r.status}`);
  D = await r.json();
  if (!$("#grid").children.length) {
    build();
    const z = /zoom=([1-6])/.exec(location.hash);   // #zoom=3 opens panel 3 in full screen
    if (z) { active = +z[1] - 1; zoomed = true; }
    const c = /chart=(-?\d+)(?:,list=([1-6]))?(?:,dock=([1-6]))?/.exec(location.hash);   // #chart=2842,list=1,dock=2
    if (c) { chartMode = true; if (c[2]) active = +c[2] - 1; if (c[3]) dock = +c[3] - 1; }
    renderAll();
    if (c) { PriceChart.mount(); PriceChart.show(+c[1], true); }
    return;
  }
  renderAll();
  PriceChart.clearCache();          // new data: the chart reloads its item
  if (chartMode) PriceChart.redraw();
}
load().catch(e => { $("#grid").innerHTML = `<div class="empty">Could not load /api/data: ${esc(e)}</div>`; });
setInterval(() => { if (D) header(); }, 30000);
// New data: the server copies new G1 files after a /reload (--watch). Reload the page data when the stamp changes.
let stamp = null;
setInterval(async () => {
  try {
    const j = await (await fetch("/api/stamp")).json();
    if (stamp !== null && j.stamp !== stamp) { await load(); toast("New data from G1 loaded"); }
    stamp = j.stamp;
  } catch (e) { /* server restarting: try again */ }
}, 15000);   // ages and STALE marks move with the clock
