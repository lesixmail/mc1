/* OTC 雷达 - 前端逻辑 (USDT/CNY 优先 + 商家数据库视图) */
"use strict";

const OWNER_REPO = "lesixmail/mc1";
const DATA_SOURCES = [
  `https://raw.githubusercontent.com/${OWNER_REPO}/otc-data/`, // 5 分钟级最新数据
  "./data/",                                                   // Pages 内置兜底快照
];
const REFRESH_SEC = 60;
const SLOW_REFRESH_SEC = 300;

const EX_META = {
  binance: { name: "Binance 币安", short: "币安", color: "#F0B90B" },
  okx:     { name: "OKX 欧易",     short: "OKX",  color: "#9aa6ff" },
  bybit:   { name: "Bybit",        short: "Bybit", color: "#f7a600" },
  gate:    { name: "Gate",         short: "Gate", color: "#5b7cfa" },
  bitget:  { name: "Bitget",       short: "Bitget", color: "#00d4c5" },
  htx:     { name: "HTX 火币",     short: "HTX",  color: "#2e8bff" },
};
const FIAT_SYMBOL = { CNY: "¥", USD: "$", EUR: "€", HKD: "HK$" };

const state = {
  asset: "USDT",
  fiat: "CNY",
  megaEx: "binance",
  megaSide: "buy",
  megaSearch: "",
  megaSort: { key: "price", dir: 1 },
  megaFilters: {},          // {colKey: value}
  suspMode: "now",
  trendMode: "rate",
  latest: null,
  history: null,
  series: null,
  suspicious: null,
  channels: null,
  merchants: null,
  countdown: REFRESH_SEC,
  activeSource: "-",
};

const $ = (s) => document.querySelector(s);
const el = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h !== undefined) e.innerHTML = h; return e; };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------------- 数据获取 ---------------- */
async function fetchJSON(name) {
  for (const base of DATA_SOURCES) {
    try {
      const r = await fetch(`${base}${name}?_=${Date.now()}`, { cache: "no-store" });
      if (r.ok) { state.activeSource = base.startsWith("http") ? "实时数据分支" : "站内快照"; return await r.json(); }
    } catch (e) { /* 下一个源 */ }
  }
  return null;
}
async function loadLatest() {
  const j = await fetchJSON("latest.json");
  if (j) { state.latest = j; renderAll(); checkAlerts(); }
  updateStatusBar();
}
async function loadSlow() {
  const [hist, series, susp, chan, mer] = await Promise.all([
    fetchJSON("history.json"), fetchJSON("series.json"),
    fetchJSON("suspicious.json"), fetchJSON("channels.json"), fetchJSON("merchants.json"),
  ]);
  if (hist) state.history = hist;
  if (series) state.series = series;
  if (susp) state.suspicious = susp;
  if (chan) state.channels = chan;
  if (mer) state.merchants = mer;
  renderTrend(); renderSuspicious(); renderChannels(); renderDbCards();
}

/* ---------------- 工具 ---------------- */
function curMarkets() { return state.latest?.markets?.[state.fiat]?.[state.asset] || {}; }
function analytics() { return state.latest?.analytics || {}; }
function fairPrice() {
  const L = state.latest; if (!L) return null;
  const fx = L.fx?.[state.fiat], spot = L.spot?.[state.asset];
  return fx && spot ? fx * spot : null;
}
function premiumOf(p) { const f = fairPrice(); return f && p ? (p / f - 1) * 100 : null; }
function best(m, side) { const a = m?.[side]; return a && a.length ? a[0].price : null; }
function fmtP(v, d) {
  if (v == null || isNaN(v)) return "--";
  const dd = d ?? (v >= 1000 ? 0 : v >= 10 ? 2 : v >= 1 ? 3 : 4);
  return v.toLocaleString("en-US", { minimumFractionDigits: Math.min(2, dd), maximumFractionDigits: dd });
}
function fmtPct(v, signed = true) { if (v == null || isNaN(v)) return "--"; return (signed && v > 0 ? "+" : "") + v.toFixed(2) + "%"; }
function fmtAmt(v) {
  if (v == null || isNaN(v)) return "--";
  if (v >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (v >= 1e4) return (v / 1e4).toFixed(1) + "万";
  return v.toFixed(v >= 10 ? 0 : 2);
}
function fmtBig(v) {
  if (v == null || isNaN(v)) return "--";
  if (v >= 1e8) return (v / 1e8).toFixed(2) + " 亿";
  if (v >= 1e4) return (v / 1e4).toFixed(0) + " 万";
  return Math.round(v).toLocaleString();
}
function payClass(m) {
  if (/支付宝|alipay/i.test(m)) return "alipay";
  if (/微信|wechat|wxpay|weixin/i.test(m)) return "wechat";
  if (/qq/i.test(m)) return "qq";
  if (/银行|bank|card|transfer|转账|sepa|swift/i.test(m)) return "bank";
  return "";
}
function payTags(methods, limit = 4) {
  return (methods || []).slice(0, limit).map((m) => `<span class="pay-tag ${payClass(m)}">${esc(m)}</span>`).join("");
}

/* ---------------- 顶部控件 ---------------- */
function buildSeg(c, items, cur, onPick) {
  c.innerHTML = "";
  for (const it of items) {
    const b = el("button", it.value === cur ? "on" : "", esc(it.label));
    b.disabled = !!it.disabled;
    b.onclick = () => onPick(it.value);
    c.appendChild(b);
  }
}
function buildTopControls() {
  const L = state.latest;
  const fiats = L?.meta?.fiats || ["CNY", "USD", "EUR", "HKD"];
  const abf = L?.meta?.assetsByFiat || { CNY: ["USDT", "BTC", "ETH", "USDC"] };
  const allAssets = [...new Set(Object.values(abf).flat())];
  const allowed = abf[state.fiat] || ["USDT"];
  buildSeg($("#asset-seg"),
    allAssets.map((a) => ({ value: a, label: a, disabled: !allowed.includes(a) })),
    state.asset, (v) => { state.asset = v; renderAll(); });
  buildSeg($("#fiat-seg"), fiats.map((f) => ({ value: f, label: f })), state.fiat, (v) => {
    state.fiat = v;
    const al = abf[v] || ["USDT"];
    if (!al.includes(state.asset)) state.asset = al[0];
    renderAll(); renderTrend();
  });
}

/* ---------------- 1. 汇率对照横幅 ---------------- */
function renderFxCompare() {
  const L = state.latest;
  const isCNY = state.fiat === "CNY" && state.asset === "USDT";
  const rate = analytics().rate?.CNY;
  const v = $("#prem-pboc");
  if (isCNY && rate) {
    const pp = rate.premium_pboc;
    v.textContent = pp == null ? "--" : fmtPct(pp);
    v.className = "banner-value " + (pp == null ? "" : pp >= 0 ? "up" : "down");
    $("#prem-pboc-tag").textContent = rate.pboc
      ? (pp >= 0 ? "场外比人行中间价高 · 入金/购汇需求偏强" : "场外比人行中间价低 · 出金需求偏强")
      : "人行中间价暂不可用,见下方市场对照";
  } else {
    const mid = crossMid();
    const prem = premiumOf(mid);
    v.textContent = prem == null ? "--" : fmtPct(prem);
    v.className = "banner-value " + (prem == null ? "" : prem >= 0 ? "up" : "down");
    $("#prem-pboc-tag").textContent = `${state.asset}/${state.fiat} 相对公允价(汇率×现货)`;
  }
  const g = $("#fx-grid"); g.innerHTML = "";
  const sym = FIAT_SYMBOL[state.fiat] || "";
  let cells;
  if (isCNY && rate) {
    cells = [
      { k: "人民银行中间价 USD/CNY", v: rate.pboc ? fmtP(rate.pboc, 4) : "暂无", s: "CFETS 授权发布" },
      { k: "场外 USDT/CNY 中间价", v: sym + fmtP(rate.otc_mid), s: "六所买卖中间价均值" },
      { k: "市场汇率 USD/CNY", v: rate.market ? fmtP(rate.market, 4) : "--", s: "离岸参考" },
      { k: "相对市场溢价", v: fmtPct(rate.premium_market), s: "场外 vs 市场汇率", cls: prCls(rate.premium_market) },
    ];
  } else {
    const mid = crossMid();
    cells = [
      { k: "全市场中间价", v: mid ? sym + fmtP(mid) : "--", s: "六所均值" },
      { k: `${state.asset} 现货指数`, v: L?.spot?.[state.asset] ? "$" + fmtP(L.spot[state.asset]) : "--", s: "Binance/Bybit" },
      { k: `USD/${state.fiat} 汇率`, v: L?.fx?.[state.fiat] ? fmtP(L.fx[state.fiat], 4) : "--", s: "open.er-api" },
      { k: "公允参考价", v: fairPrice() ? sym + fmtP(fairPrice()) : "--", s: "汇率×现货" },
    ];
  }
  for (const c of cells)
    g.appendChild(el("div", "banner-cell", `<div class="k">${esc(c.k)}</div><div class="v ${c.cls || ""}">${c.v}</div><div class="s">${esc(c.s)}</div>`));
}
function prCls(v) { return v == null ? "" : v >= 0 ? "pos" : "neg"; }
function crossMid() {
  const ms = curMarkets(); const mids = [];
  for (const ex of Object.keys(EX_META)) {
    const m = ms[ex]; if (!m?.ok) continue;
    const b = best(m, "buy"), s = best(m, "sell");
    const mid = b && s ? (b + s) / 2 : (b || s);
    if (mid) mids.push(mid);
  }
  return mids.length ? mids.reduce((a, b) => a + b) / mids.length : null;
}

/* ---------------- 2. 成交量估算 ---------------- */
function renderVolume() {
  const vol = analytics().volume?.CNY;
  const showable = state.fiat === "CNY" && state.asset === "USDT" && vol;
  if (!showable) {
    $("#vol-fiat").textContent = "仅 USDT/CNY 提供";
    $("#vol-usdt").textContent = "";
    $("#vol-meta").innerHTML = "";
    $("#vol-method").textContent = "切换到 USDT / 人民币 查看成交量估算。";
    return;
  }
  $("#vol-fiat").innerHTML = `¥ ${fmtBig(vol.est_low_fiat)} ~ ${fmtBig(vol.est_high_fiat)}`;
  $("#vol-usdt").textContent = `≈ ${fmtBig(vol.est_low)} ~ ${fmtBig(vol.est_high)} USDT / 日`;
  const meta = [
    { k: "当前可见挂单深度", v: fmtBig(vol.standing_liq) + " USDT" },
    { k: "在册商家数", v: vol.merchants },
    { k: "商家月成交单合计", v: vol.monthly_orders.toLocaleString() },
    { k: "换手率假设", v: `${vol.turn_low}× ~ ${vol.turn_high}×/日` },
  ];
  $("#vol-meta").innerHTML = meta.map((m) => `<div class="vol-cell"><div class="k">${esc(m.k)}</div><div class="v">${m.v}</div></div>`).join("");
  $("#vol-method").innerHTML = "方法:估算 = 当前可见挂单深度 × 日换手率区间(" + vol.turn_low + "–" + vol.turn_high +
    "×)。仅覆盖六所公开盘口,不含场外大宗/OTC 柜台/未挂单撮合,故为<strong>下限级数量级估算</strong>,真实规模通常更大。";
}

/* ---------------- 3. 30日走势 ---------------- */
let trendChart = null, depthChart = null;
function ensureCharts() {
  if (typeof echarts === "undefined") return false;
  if (!trendChart) trendChart = echarts.init($("#trend-chart"));
  if (!depthChart) depthChart = echarts.init($("#depth-chart"));
  return true;
}
window.__echartsReady = () => { ensureCharts(); renderTrend(); renderDepth(); };
function chartBase() {
  return {
    backgroundColor: "transparent", textStyle: { color: "#8a94ad" },
    tooltip: { trigger: "axis", backgroundColor: "#1b2540", borderColor: "#25304d", textStyle: { color: "#e8edf7", fontSize: 12 } },
    grid: { left: 64, right: 24, top: 42, bottom: 50 },
    legend: { textStyle: { color: "#8a94ad", fontSize: 11 }, type: "scroll", top: 4 },
  };
}
function renderTrend() {
  if (!ensureCharts()) return;
  // 优先用 series.json 的日级数据; 若不足则用 history.json 的 intraday
  const daily = state.series?.rate || [];
  const isPrem = state.trendMode === "premium";
  let series = [], xAxis;
  if (daily.length >= 2) {
    xAxis = { type: "category", data: daily.map((r) => r.day), axisLine: { lineStyle: { color: "#25304d" } } };
    if (isPrem) {
      series = [
        lineS("相对人行溢价%", daily.map((r) => r.premium_pboc), "#f0b90b"),
        lineS("相对市场溢价%", daily.map((r) => r.premium_market), "#4f8cff"),
      ];
      $("#trend-note").textContent = "场外 USDT/CNY 相对人行/市场汇率的每日溢价";
    } else {
      series = [
        lineS("场外 USDT/CNY", daily.map((r) => r.otc_mid), "#2ecc8f"),
        lineS("人民银行中间价", daily.map((r) => r.pboc), "#ff5d6c"),
        lineS("市场汇率", daily.map((r) => r.market), "#6b7693"),
      ];
      $("#trend-note").textContent = "USDT≈1美元,场外人民币价与人行 USD/CNY 中间价直接可比";
    }
  } else {
    // 回退: intraday history 中的 mid|CNY|USDT 与 pboc|CNY
    const mid = (state.history?.series?.["mid|CNY|USDT"] || []);
    const pboc = (state.history?.series?.["pboc|CNY"] || []);
    if (mid.length < 2) {
      trendChart.setOption({ ...chartBase(), xAxis: { type: "time" }, yAxis: { type: "value" },
        series: [], graphic: [{ type: "text", left: "center", top: "middle", style: { text: "走势数据积累中,日级对照将在运行 1 天后显示", fill: "#5c677f", fontSize: 14 } }] }, true);
      $("#trend-note").textContent = "日级序列积累中,先看实时";
      return;
    }
    xAxis = { type: "time", axisLine: { lineStyle: { color: "#25304d" } } };
    series = [
      lineS("场外 USDT/CNY", mid.map((p) => [p[0] * 1000, p[1]]), "#2ecc8f", true),
      lineS("人民银行中间价", pboc.map((p) => [p[0] * 1000, p[1]]), "#ff5d6c", true),
    ];
    $("#trend-note").textContent = "(intraday 回退视图)";
  }
  trendChart.setOption({
    ...chartBase(), xAxis,
    yAxis: { type: "value", scale: true, name: isPrem ? "溢价 %" : "CNY",
             axisLine: { lineStyle: { color: "#25304d" } }, splitLine: { lineStyle: { color: "#1b2540" } } },
    series,
  }, true);
}
function lineS(name, data, color, timeMode) {
  return { name, type: "line", showSymbol: false, smooth: true, connectNulls: true,
           data, lineStyle: { color, width: 2 }, itemStyle: { color } };
}

/* ---------------- 4. 压价急售商家 ---------------- */
function renderSuspicious() {
  const thead = $("#susp-table thead"), tbody = $("#susp-table tbody");
  tbody.innerHTML = "";
  const sym = FIAT_SYMBOL[state.fiat] || "";
  if (state.suspMode === "now") {
    thead.innerHTML = `<tr><th>交易所</th><th>方向</th><th>商家</th><th>报价</th><th>低于Top2</th><th>压价幅度</th><th>可交易量</th><th>月成交</th><th>成单率</th></tr>`;
    const rows = (analytics().suspicious || []).filter((s) => s.asset === state.asset && s.fiat === state.fiat);
    if (!rows.length) { tbody.appendChild(el("tr", "", '<td colspan="9" class="empty">当前无明显压价商家(报价低于本平台 Top2 0.3% 以上)</td>')); return; }
    for (const s of rows) {
      const tr = el("tr", "susp-row");
      tr.innerHTML = `
        <td><span class="ex-chip" style="--ex-color:${EX_META[s.exchange]?.color}"><i></i>${esc(EX_META[s.exchange]?.short || s.exchange)}</span></td>
        <td>${s.side === "buy" ? "急售(卖出USDT)" : "急购(买入USDT)"}</td>
        <td class="l">${esc(s.merchant || "-")}</td>
        <td><strong>${sym}${fmtP(s.price)}</strong></td>
        <td class="worst">${sym}${fmtP(s.below_abs, 4)}</td>
        <td class="worst">-${(s.below_pct || 0).toFixed(2)}%</td>
        <td>${fmtAmt(s.amount)}</td>
        <td>${s.orders ?? "--"}</td>
        <td class="${rateCls(s.rate)}">${s.rate != null ? s.rate + "%" : "--"}</td>`;
      tbody.appendChild(tr);
    }
  } else if (state.suspMode === "flagged") {
    thead.innerHTML = `<tr><th>交易所</th><th>商家</th><th>方向</th><th>压价次数</th><th>样本数</th><th>平均低于Top2</th><th>最大压价</th><th>最近报价</th><th>最近量</th><th>成单率</th></tr>`;
    const rows = (state.suspicious?.flagged || []).filter((s) => s.asset === state.asset && s.fiat === state.fiat);
    if (!rows.length) { tbody.appendChild(el("tr", "", '<td colspan="10" class="empty">长期压价记录积累中</td>')); return; }
    for (const s of rows) {
      const tr = el("tr");
      tr.innerHTML = `
        <td><span class="ex-chip" style="--ex-color:${EX_META[s.exchange]?.color}"><i></i>${esc(EX_META[s.exchange]?.short || s.exchange)}</span></td>
        <td class="l">${esc(s.merchant)}</td>
        <td>${s.side === "buy" ? "急售" : "急购"}</td>
        <td class="worst"><strong>${s.susp_samples}</strong></td>
        <td>${s.samples}</td>
        <td class="worst">${sym}${fmtP(s.avg_below, 4)}</td>
        <td class="worst">-${(s.max_below_pct || 0).toFixed(2)}%</td>
        <td>${sym}${fmtP(s.last_price)}</td>
        <td>${fmtAmt(s.last_amount)}</td>
        <td class="${rateCls(s.last_rate)}">${s.last_rate != null ? s.last_rate + "%" : "--"}</td>`;
      tbody.appendChild(tr);
    }
  } else {
    thead.innerHTML = `<tr><th>时间</th><th>交易所</th><th>商家</th><th>方向</th><th>报价</th><th>Top2价</th><th>低于</th><th>幅度</th><th>量</th></tr>`;
    const rows = (state.suspicious?.events || []).filter((s) => s.asset === state.asset && s.fiat === state.fiat);
    if (!rows.length) { tbody.appendChild(el("tr", "", '<td colspan="9" class="empty">暂无历史压价事件</td>')); return; }
    for (const s of rows.slice(0, 120)) {
      const tr = el("tr");
      tr.innerHTML = `
        <td class="l">${new Date(s.ts * 1000).toLocaleString("zh-CN", { hour12: false })}</td>
        <td><span class="ex-chip" style="--ex-color:${EX_META[s.exchange]?.color}"><i></i>${esc(EX_META[s.exchange]?.short || s.exchange)}</span></td>
        <td class="l">${esc(s.merchant)}</td>
        <td>${s.side === "buy" ? "急售" : "急购"}</td>
        <td>${sym}${fmtP(s.price)}</td>
        <td>${sym}${fmtP(s.top2_price)}</td>
        <td class="worst">${sym}${fmtP(s.below_abs, 4)}</td>
        <td class="worst">-${(s.below_pct || 0).toFixed(2)}%</td>
        <td>${fmtAmt(s.amount)}</td>`;
      tbody.appendChild(tr);
    }
  }
}
function rateCls(r) { return r == null ? "" : r >= 97 ? "rate-hi" : "rate-lo"; }

/* ---------------- 5. 商家全字段大表 ---------------- */
const MEGA_COLS = [
  { k: "merchant", label: "商家", type: "str", frozen: true },
  { k: "userType", label: "类型", type: "str" },
  { k: "price", label: "价格", type: "num" },
  { k: "premium", label: "溢价%", type: "num", derived: (a) => premiumOf(a.price) },
  { k: "amount", label: "可交易量", type: "num" },
  { k: "min", label: "最小限额", type: "num" },
  { k: "max", label: "最大限额", type: "num" },
  { k: "methods", label: "支付方式", type: "list" },
  { k: "orders", label: "月成交单", type: "num" },
  { k: "rate", label: "成单率%", type: "num" },
  { k: "positiveRate", label: "好评率%", type: "num" },
  { k: "orderCount", label: "累计成交", type: "num" },
  { k: "vipLevel", label: "VIP", type: "num" },
  { k: "userGrade", label: "等级", type: "num" },
  { k: "registerDays", label: "注册天数", type: "num" },
  { k: "lastActiveMin", label: "活跃(分前)", type: "num" },
  { k: "margin", label: "保证金", type: "num" },
  { k: "payTimeLimit", label: "付款时限", type: "num" },
  { k: "minQuantity", label: "最小数量", type: "num" },
  { k: "buyerRegDays", label: "买家注册要求", type: "num" },
  { k: "buyerKyc", label: "需KYC", type: "bool" },
  { k: "priceType", label: "定价", type: "str" },
  { k: "remark", label: "备注", type: "str" },
  { k: "advNo", label: "广告号", type: "str" },
];
function megaRows() {
  const ms = curMarkets();
  let rows = [];
  const exs = state.megaEx === "all" ? Object.keys(EX_META) : [state.megaEx];
  for (const ex of exs) {
    const m = ms[ex];
    if (!m?.ok) continue;
    for (const a of m[state.megaSide] || []) rows.push({ ex, a });
  }
  // 派生列
  for (const r of rows) for (const c of MEGA_COLS) if (c.derived) r.a["__" + c.k] = c.derived(r.a);
  // 搜索
  const q = state.megaSearch.trim().toLowerCase();
  if (q) rows = rows.filter((r) => {
    const a = r.a;
    return (a.merchant || "").toLowerCase().includes(q) ||
           (a.methods || []).join(" ").toLowerCase().includes(q) ||
           (a.remark || "").toLowerCase().includes(q);
  });
  // 列筛选
  for (const [k, val] of Object.entries(state.megaFilters)) {
    if (val == null || val === "") continue;
    const col = MEGA_COLS.find((c) => c.k === k);
    rows = rows.filter((r) => {
      const v = cellValue(r.a, col);
      if (col.type === "num") { const n = parseFloat(v); return !isNaN(n) && n >= parseFloat(val); }
      if (col.type === "bool") return val === "y" ? !!v : !v;
      return String(v ?? "").toLowerCase().includes(String(val).toLowerCase());
    });
  }
  // 排序
  const sc = MEGA_COLS.find((c) => c.k === state.megaSort.key) || MEGA_COLS[2];
  rows.sort((x, y) => {
    let a = cellValue(x.a, sc), b = cellValue(y.a, sc);
    if (sc.type === "num") { a = a == null || isNaN(a) ? -Infinity : a; b = b == null || isNaN(b) ? -Infinity : b; return (a - b) * state.megaSort.dir; }
    a = String(a ?? ""); b = String(b ?? ""); return a.localeCompare(b) * state.megaSort.dir;
  });
  return rows;
}
function cellValue(a, col) {
  if (col.derived) return a["__" + col.k];
  if (col.type === "list") return (a[col.k] || []).join("/");
  return a[col.k];
}
function renderMega() {
  const thead = $("#mega-table thead"), tbody = $("#mega-table tbody");
  // 表头 + 过滤行
  const showEx = state.megaEx === "all";
  let h = "<tr>";
  if (showEx) h += `<th class="l">交易所</th>`;
  for (const c of MEGA_COLS) {
    const arrow = state.megaSort.key === c.k ? (state.megaSort.dir > 0 ? " ▲" : " ▼") : "";
    h += `<th class="sortable ${c.frozen ? "l" : ""}" data-k="${c.k}">${esc(c.label)}${arrow}</th>`;
  }
  h += "</tr><tr class='filter-row'>";
  if (showEx) h += "<td></td>";
  for (const c of MEGA_COLS) {
    if (c.type === "bool") h += `<td><select data-fk="${c.k}"><option value="">全</option><option value="y">是</option><option value="n">否</option></select></td>`;
    else h += `<td><input data-fk="${c.k}" placeholder="${c.type === "num" ? "≥" : "含"}" value="${esc(state.megaFilters[c.k] || "")}"></td>`;
  }
  h += "</tr>";
  thead.innerHTML = h;
  thead.querySelectorAll("th.sortable").forEach((th) => th.onclick = () => {
    const k = th.dataset.k;
    if (state.megaSort.key === k) state.megaSort.dir *= -1;
    else state.megaSort = { key: k, dir: k === "merchant" ? 1 : -1 };
    renderMega();
  });
  thead.querySelectorAll("[data-fk]").forEach((inp) => {
    const fk = inp.dataset.fk;
    inp.oninput = inp.onchange = () => { state.megaFilters[fk] = inp.value; renderMega(); };
    inp.onclick = (e) => e.stopPropagation();
  });

  const rows = megaRows();
  const sym = FIAT_SYMBOL[state.fiat] || "";
  tbody.innerHTML = "";
  const colspan = MEGA_COLS.length + (showEx ? 1 : 0);
  if (!rows.length) { tbody.appendChild(el("tr", "", `<td colspan="${colspan}" class="empty">无符合条件的商家</td>`)); }
  for (const { ex, a } of rows.slice(0, 200)) {
    const tr = el("tr");
    let cells = "";
    if (showEx) cells += `<td class="l"><span class="ex-chip" style="--ex-color:${EX_META[ex]?.color}"><i></i>${esc(EX_META[ex]?.short)}</span></td>`;
    for (const c of MEGA_COLS) cells += megaCell(a, c, sym);
    tr.innerHTML = cells;
    tbody.appendChild(tr);
  }
  $("#mega-count").textContent = `${rows.length} 条` + (rows.length > 200 ? "(显示前200)" : "") +
    (state.megaEx === "binance" ? " · 币安全字段" : "");
}
function megaCell(a, c, sym) {
  let v = cellValue(a, c);
  if (c.k === "merchant") return `<td class="l">${esc(v || "-")}</td>`;
  if (c.k === "methods") return `<td class="l">${payTags(a.methods) || "--"}</td>`;
  if (c.k === "price") return `<td><strong>${v ? sym + fmtP(v) : "--"}</strong></td>`;
  if (c.k === "premium") { const p = a["__premium"]; return `<td class="${prCls(p)}">${fmtPct(p)}</td>`; }
  if (c.type === "bool") return `<td>${v ? "✓" : ""}</td>`;
  if (c.k === "amount" || c.k === "min" || c.k === "max" || c.k === "margin") return `<td>${v == null ? "--" : fmtAmt(v)}</td>`;
  if (c.k === "rate" || c.k === "positiveRate") return `<td class="${rateCls(v)}">${v == null ? "--" : v + "%"}</td>`;
  if (c.type === "num") return `<td>${v == null || v === "" ? "--" : Number(v).toLocaleString()}</td>`;
  return `<td class="l">${esc(v ?? "--")}</td>`;
}
function buildMegaToolbar() {
  buildSeg($("#mega-ex"),
    [...Object.entries(EX_META).map(([k, m]) => ({ value: k, label: m.short })), { value: "all", label: "全部对照" }],
    state.megaEx, (v) => { state.megaEx = v; renderMega(); });
  buildSeg($("#mega-side"),
    [{ value: "buy", label: "我要买入(商家卖出)" }, { value: "sell", label: "我要卖出(商家买入)" }],
    state.megaSide, (v) => { state.megaSide = v; renderMega(); });
}

/* ---------------- 6. 渠道盘口 ---------------- */
function renderChannels() {
  const tbody = $("#chan-table tbody"); tbody.innerHTML = "";
  const sym = FIAT_SYMBOL[state.fiat] || "";
  const rows = (state.channels?.rows || []).filter((r) => r.asset === state.asset && r.fiat === state.fiat);
  // 按渠道聚合 buy/sell
  const byCh = {};
  for (const r of rows) { (byCh[r.channel] = byCh[r.channel] || {})[r.side] = r; }
  const order = ["支付宝", "微信", "银行卡", "QQ钱包"];
  const chans = Object.keys(byCh).sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99));
  if (!chans.length) { tbody.appendChild(el("tr", "", '<td colspan="9" class="empty">当日渠道数据积累中(或该交易对无渠道标签)</td>')); return; }
  for (const ch of chans) {
    const b = byCh[ch].buy, s = byCh[ch].sell;
    const tr = el("tr");
    tr.innerHTML = `
      <td class="l"><span class="pay-tag ${payClass(ch)}">${esc(ch)}</span></td>
      <td class="best">${b ? sym + fmtP(b.best_price) : "--"}</td>
      <td>${b ? sym + fmtP(b.avg_price) : "--"}</td>
      <td>${b ? sym + fmtP(b.min_price) : "--"}</td>
      <td class="muted">${b ? b.samples : 0}</td>
      <td class="best">${s ? sym + fmtP(s.best_price) : "--"}</td>
      <td>${s ? sym + fmtP(s.avg_price) : "--"}</td>
      <td>${s ? sym + fmtP(s.min_price) : "--"}</td>
      <td class="muted">${s ? s.samples : 0}</td>`;
    tbody.appendChild(tr);
  }
}

/* ---------------- 7. 跨所对比 ---------------- */
function renderCompare() {
  const ms = curMarkets(); const tb = $("#compare-table tbody"); tb.innerHTML = "";
  const sym = FIAT_SYMBOL[state.fiat] || "";
  const rows = Object.entries(EX_META).map(([ex, meta]) => {
    const m = ms[ex];
    return { ex, meta, m, b: best(m, "buy"), s: best(m, "sell"),
      sumB: (m?.buy || []).reduce((x, a) => x + (a.amount || 0), 0),
      sumS: (m?.sell || []).reduce((x, a) => x + (a.amount || 0), 0) };
  });
  const ok = rows.filter((r) => r.m?.ok);
  const minBuy = Math.min(...ok.filter((r) => r.b).map((r) => r.b));
  const maxSell = Math.max(...ok.filter((r) => r.s).map((r) => r.s));
  for (const r of rows) {
    const tr = el("tr");
    const spread = r.b && r.s ? r.b - r.s : null;
    tr.innerHTML = `
      <td><span class="ex-chip" style="--ex-color:${r.meta.color}"><i></i>${esc(r.meta.name)}</span></td>
      <td class="${r.b === minBuy ? "best" : ""}">${r.b ? sym + fmtP(r.b) : "--"}</td>
      <td class="${r.s === maxSell ? "best" : ""}">${r.s ? sym + fmtP(r.s) : "--"}</td>
      <td>${spread != null ? fmtP(spread, 4) : "--"}</td>
      <td class="${prCls(premiumOf(r.b))}">${fmtPct(premiumOf(r.b))}</td>
      <td>${fmtAmt(r.sumB)}</td><td>${fmtAmt(r.sumS)}</td>
      <td>${r.m?.ok ? '<span class="dot ok"></span>' : `<span class="dot err" title="${esc(r.m?.error || "")}"></span>`}</td>`;
    tb.appendChild(tr);
  }
}

/* ---------------- 8. 套利 ---------------- */
function renderArb() {
  const ms = curMarkets(); const tb = $("#arb-table tbody"); tb.innerHTML = "";
  const exs = Object.keys(EX_META).filter((e) => ms[e]?.ok);
  const ops = [];
  for (const a of exs) for (const b of exs) {
    if (a === b) continue;
    const buyP = best(ms[a], "buy"), sellP = best(ms[b], "sell");
    if (!buyP || !sellP) continue;
    ops.push({ a, b, buyP, sellP, diff: sellP - buyP, pct: ((sellP - buyP) / buyP) * 100 });
  }
  ops.sort((x, y) => y.pct - x.pct);
  state._maxArb = ops.length ? ops[0].pct : null;
  const sym = FIAT_SYMBOL[state.fiat] || "";
  if (!ops.length) { tb.appendChild(el("tr", "", '<td colspan="7" class="empty">暂无足够数据</td>')); return; }
  ops.slice(0, 6).forEach((o, i) => {
    const tr = el("tr");
    tr.innerHTML = `<td>${i + 1}</td>
      <td><span class="ex-chip" style="--ex-color:${EX_META[o.a].color}"><i></i>${esc(EX_META[o.a].short)}</span></td>
      <td>${sym}${fmtP(o.buyP)}</td>
      <td><span class="ex-chip" style="--ex-color:${EX_META[o.b].color}"><i></i>${esc(EX_META[o.b].short)}</span></td>
      <td>${sym}${fmtP(o.sellP)}</td>
      <td class="${o.diff >= 0 ? "best" : "worst"}">${fmtP(o.diff, 4)}</td>
      <td class="${o.pct >= 0 ? "best" : "worst"}">${fmtPct(o.pct)}</td>`;
    tb.appendChild(tr);
  });
}

/* ---------------- 9. 深度 ---------------- */
function renderDepth() {
  if (!ensureCharts()) return;
  const ms = curMarkets(); const series = [];
  for (const [ex, meta] of Object.entries(EX_META)) {
    const m = ms[ex]; if (!m?.ok) continue;
    for (const side of ["buy", "sell"]) {
      const ads = (m[side] || []).slice().sort((a, b) => side === "buy" ? a.price - b.price : b.price - a.price);
      let cum = 0;
      const data = ads.map((a) => { cum += a.amount || 0; return [a.price, +cum.toFixed(2)]; });
      if (!data.length) continue;
      series.push({ name: `${meta.short}·${side === "buy" ? "可买" : "可卖"}`, type: "line", step: "end", showSymbol: false,
        data: side === "buy" ? data : data.slice().reverse(),
        lineStyle: { color: meta.color, width: 1.8, type: side === "buy" ? "solid" : "dashed" }, itemStyle: { color: meta.color } });
    }
  }
  depthChart.setOption({ ...chartBase(),
    xAxis: { type: "value", scale: true, name: `价格(${state.fiat})`, axisLine: { lineStyle: { color: "#25304d" } }, splitLine: { lineStyle: { color: "#1b2540" } } },
    yAxis: { type: "value", name: `累计(${state.asset})`, axisLine: { lineStyle: { color: "#25304d" } }, splitLine: { lineStyle: { color: "#1b2540" } } },
    series }, true);
}

/* ---------------- 10. 数据库 / 数据源 ---------------- */
function renderDbCards() {
  const wrap = $("#db-cards"); if (!wrap) return;
  const db = state.latest?.meta?.db || {};
  const mer = state.merchants;
  const cards = [
    { k: "在册商家总数", v: (mer?.grand_total ?? db.merchants ?? 0).toLocaleString(), s: "SQLite 持久库累计" },
    { k: "盘口观测(72h)", v: (db.observations ?? 0).toLocaleString(), s: "滚动原始记录" },
    { k: "压价事件(60d)", v: (db.suspicious ?? 0).toLocaleString(), s: "急售/急购记录" },
    { k: "数据下载", v: '<a href="' + DATA_SOURCES[0] + 'otc.db" download>otc.db ↓</a>', s: "完整 SQLite 数据库" },
  ];
  wrap.innerHTML = cards.map((c) => `<div class="db-cell"><div class="k">${esc(c.k)}</div><div class="v">${c.v}</div><div class="s">${esc(c.s)}</div></div>`).join("");
  if (mer?.totals) {
    const byEx = Object.entries(mer.totals).map(([ex, n]) => `<span class="ex-chip" style="--ex-color:${EX_META[ex]?.color}"><i></i>${esc(EX_META[ex]?.short || ex)} ${n}</span>`).join(" ");
    wrap.innerHTML += `<div class="db-cell wide"><div class="k">各所收录商家</div><div class="v small">${byEx}</div></div>`;
  }
}
function renderSources() {
  const L = state.latest; const g = $("#src-grid"); g.innerHTML = "";
  const ms = curMarkets();
  for (const [ex, meta] of Object.entries(EX_META)) {
    const m = ms[ex]; const ok = m?.ok;
    g.appendChild(el("div", "src-cell", `<div class="name"><span class="dot ${ok ? "ok" : "err"}"></span>${esc(meta.name)}</div>
      <div class="msg">${ok ? `正常 · ${(m.buy || []).length} 买 / ${(m.sell || []).length} 卖广告` : esc(shortErr(m?.error))}</div>`));
  }
  if (L?.meta) g.appendChild(el("div", "src-cell", `<div class="name"><span class="dot ${L.meta.okMarkets ? "ok" : "err"}"></span>采集器</div>
    <div class="msg">本轮 ${L.meta.okMarkets} 市场成功 / ${L.meta.errMarkets} 失败 · ${L.meta.fetchSeconds}s · 源:${esc(state.activeSource)}</div>`));
}
function shortErr(err) {
  if (!err) return "无数据";
  if (/403|Access Denied/i.test(err)) return "交易所接口风控限制,暂不可用";
  if (/451|restricted/i.test(err)) return "接口对采集地区限制访问";
  if (/未开通此法币市场|无货币映射|不支持/.test(err)) return "该交易所未开通此交易对市场";
  if (/无广告数据/.test(err)) return "暂无挂单";
  return err.length > 80 ? err.slice(0, 80) + "…" : err;
}

/* ---------------- 11. 预警 ---------------- */
const ALERT_KEY = "otc-radar-alerts2";
const METRIC_NAME = { prem_pboc: "相对人行溢价%", prem_market: "相对市场溢价%", otc_mid: "场外中间价", arb: "最大跨所毛利率%", susp: "实时压价商家数" };
function getAlerts() { try { return JSON.parse(localStorage.getItem(ALERT_KEY)) || []; } catch { return []; } }
function saveAlerts(a) { localStorage.setItem(ALERT_KEY, JSON.stringify(a)); }
function buildAlertUI() {
  $("#alert-add").onclick = () => {
    const value = parseFloat($("#alert-value").value);
    if (isNaN(value)) { alert("请输入阈值"); return; }
    const list = getAlerts();
    list.push({ id: Date.now(), metric: $("#alert-metric").value, op: $("#alert-op").value, value, lastFired: 0 });
    saveAlerts(list); renderAlertList();
  };
  $("#alert-notify").onclick = async () => {
    if (!("Notification" in window)) { alert("浏览器不支持通知"); return; }
    const p = await Notification.requestPermission();
    $("#alert-notify").textContent = p === "granted" ? "✓ 通知已开启" : "开启浏览器通知";
  };
  renderAlertList();
}
function renderAlertList() {
  const ul = $("#alert-list"); ul.innerHTML = "";
  const list = getAlerts();
  if (!list.length) { ul.appendChild(el("li", "", '<span class="muted">暂无预警规则</span>')); return; }
  for (const a of list) {
    const li = el("li", a.lastFired && Date.now() - a.lastFired < 6e5 ? "fired" : "");
    li.innerHTML = `<span>${esc(METRIC_NAME[a.metric] || a.metric)} ${a.op === ">" ? "高于" : "低于"} <strong>${a.value}</strong></span>
      <span class="spacer"></span>${a.lastFired ? `<span class="muted">触发于 ${new Date(a.lastFired).toLocaleTimeString("zh-CN")}</span>` : ""}
      <button class="btn danger" data-id="${a.id}">删除</button>`;
    li.querySelector("button").onclick = (e) => { saveAlerts(getAlerts().filter((x) => x.id !== +e.target.dataset.id)); renderAlertList(); };
    ul.appendChild(li);
  }
}
function metricValue(metric) {
  const r = analytics().rate?.CNY;
  if (metric === "prem_pboc") return r?.premium_pboc;
  if (metric === "prem_market") return r?.premium_market;
  if (metric === "otc_mid") return r?.otc_mid;
  if (metric === "arb") return state._maxArb;
  if (metric === "susp") return (analytics().suspicious || []).filter((s) => s.fiat === "CNY" && s.asset === "USDT").length;
  return null;
}
function checkAlerts() {
  const list = getAlerts(); let changed = false;
  for (const a of list) {
    const v = metricValue(a.metric);
    if (v == null) continue;
    const hit = a.op === ">" ? v > a.value : v < a.value;
    if (hit && Date.now() - (a.lastFired || 0) > 6e5) {
      a.lastFired = Date.now(); changed = true;
      const msg = `${METRIC_NAME[a.metric]} 当前 ${(+v).toFixed(3)},${a.op === ">" ? "高于" : "低于"} ${a.value}`;
      if ("Notification" in window && Notification.permission === "granted") new Notification("📡 OTC 雷达预警", { body: msg });
      beep(); console.warn("[alert]", msg);
    }
  }
  if (changed) { saveAlerts(list); renderAlertList(); }
}
function beep() {
  try { const c = new (window.AudioContext || window.webkitAudioContext)(); const o = c.createOscillator(), g = c.createGain();
    o.connect(g); g.connect(c.destination); o.frequency.value = 880; g.gain.value = .08; o.start(); o.stop(c.currentTime + .35);
  } catch {}
}

/* ---------------- 状态栏 / 总渲染 ---------------- */
function updateStatusBar() {
  const L = state.latest; const dot = $("#conn-dot");
  if (!L) { dot.className = "dot err"; $("#update-time").textContent = "数据加载失败,等待重试…"; return; }
  const age = Math.floor(Date.now() / 1000) - L.updated;
  dot.className = "dot " + (age < 900 ? "ok" : age < 3600 ? "warn" : "err");
  const t = new Date(L.updated * 1000).toLocaleString("zh-CN", { hour12: false });
  $("#update-time").textContent = `更新于 ${t}` + (age >= 900 ? `(${Math.floor(age / 60)}分钟前)` : "");
  const db = L.meta?.db || {};
  $("#db-stat").textContent = `数据库 ${db.merchants ?? 0} 商家`;
}
function renderAll() {
  if (!state.latest) return;
  buildTopControls();
  renderFxCompare(); renderVolume(); renderCompare(); renderArb(); renderDepth();
  buildMegaToolbar(); renderMega();
  renderChannels(); renderSuspicious(); renderSources(); renderDbCards();
}

/* ---------------- 初始化 ---------------- */
function initToolbars() {
  $("#trend-mode").querySelectorAll("button").forEach((b) => b.onclick = () => {
    $("#trend-mode").querySelectorAll("button").forEach((x) => x.classList.remove("on"));
    b.classList.add("on"); state.trendMode = b.dataset.v; renderTrend();
  });
  $("#susp-mode").querySelectorAll("button").forEach((b) => b.onclick = () => {
    $("#susp-mode").querySelectorAll("button").forEach((x) => x.classList.remove("on"));
    b.classList.add("on"); state.suspMode = b.dataset.v; renderSuspicious();
  });
  $("#mega-search").oninput = (e) => { state.megaSearch = e.target.value; renderMega(); };
  $("#mega-reset").onclick = () => { state.megaSearch = ""; state.megaFilters = {}; $("#mega-search").value = ""; renderMega(); };
  buildAlertUI();
}
function tick() {
  state.countdown -= 1;
  if (state.countdown <= 0) { state.countdown = REFRESH_SEC; loadLatest(); }
  $("#countdown").textContent = state.countdown;
}
window.addEventListener("resize", () => { trendChart?.resize(); depthChart?.resize(); });

initToolbars();
loadLatest();
loadSlow();
setInterval(tick, 1000);
setInterval(loadSlow, SLOW_REFRESH_SEC * 1000);
