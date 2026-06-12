/* OTC 雷达 - 前端逻辑 */
"use strict";

const OWNER_REPO = "lesixmail/mc1";
const DATA_SOURCES = [
  `https://raw.githubusercontent.com/${OWNER_REPO}/otc-data/`, // 5 分钟级最新数据
  "./data/",                                                   // Pages 内置兜底快照
];
const REFRESH_SEC = 60;        // latest.json 拉取间隔
const HISTORY_REFRESH_SEC = 300;

const EX_META = {
  binance: { name: "Binance 币安", color: "#F0B90B" },
  okx:     { name: "OKX 欧易",     color: "#9aa6ff" },
  bybit:   { name: "Bybit",        color: "#f7a600" },
  gate:    { name: "Gate",         color: "#5b7cfa" },
  bitget:  { name: "Bitget",       color: "#00d4c5" },
  htx:     { name: "HTX 火币",     color: "#2e8bff" },
};
const FIAT_SYMBOL = { CNY: "¥", USD: "$", EUR: "€", HKD: "HK$" };

const state = {
  fiat: "CNY",
  asset: "USDT",
  side: "buy",
  exFilter: "",
  payFilter: "",
  amountFilter: null,
  histMode: "price",
  histRange: 86400,
  latest: null,
  history: null,
  countdown: REFRESH_SEC,
  activeSource: "-",
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, html) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------------- 数据获取 ---------------- */
async function fetchJSON(name) {
  for (const base of DATA_SOURCES) {
    try {
      const r = await fetch(`${base}${name}?_=${Date.now()}`, { cache: "no-store" });
      if (r.ok) {
        state.activeSource = base.startsWith("http") ? "实时数据分支" : "站内快照";
        return await r.json();
      }
    } catch (e) { /* 下一个数据源 */ }
  }
  return null;
}

async function loadLatest() {
  const j = await fetchJSON("latest.json");
  if (j) {
    state.latest = j;
    renderAll();
    checkAlerts();
  }
  updateStatusBar();
}

async function loadHistory() {
  const j = await fetchJSON("history.json");
  if (j) {
    state.history = j;
    renderSparklines();
    renderHistoryChart();
  }
}

/* ---------------- 工具 ---------------- */
function fairPrice() {
  // 公允价 = 法币汇率 × 现货指数价
  const L = state.latest;
  if (!L) return null;
  const fx = L.fx?.[state.fiat];
  const spot = L.spot?.[state.asset];
  if (!fx || !spot) return null;
  return fx * spot;
}
function premiumOf(price) {
  const fair = fairPrice();
  if (!fair || !price) return null;
  return (price / fair - 1) * 100;
}
function curMarkets() {
  return state.latest?.markets?.[state.fiat]?.[state.asset] || {};
}
function best(m, side) {
  const a = m?.[side];
  return a && a.length ? a[0].price : null;
}
function fmtP(v, digits) {
  if (v == null || isNaN(v)) return "--";
  const d = digits ?? (v >= 1000 ? 0 : v >= 10 ? 2 : v >= 1 ? 3 : 4);
  return v.toLocaleString("en-US", { minimumFractionDigits: Math.min(2, d), maximumFractionDigits: d });
}
function fmtPct(v, signed = true) {
  if (v == null || isNaN(v)) return "--";
  return (signed && v > 0 ? "+" : "") + v.toFixed(2) + "%";
}
function fmtAmt(v) {
  if (v == null || isNaN(v)) return "--";
  if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return v.toFixed(v >= 10 ? 0 : 2);
}
function payClass(m) {
  if (/支付宝|alipay/i.test(m)) return "alipay";
  if (/微信|wechat|wxpay|weixin/i.test(m)) return "wechat";
  if (/银行|bank|card|transfer|转账/i.test(m)) return "bank";
  return "";
}
function payTags(methods, limit = 3) {
  return (methods || []).slice(0, limit)
    .map((m) => `<span class="pay-tag ${payClass(m)}">${esc(m)}</span>`).join("");
}

/* ---------------- 顶部控件 ---------------- */
function buildSeg(container, items, current, onPick) {
  container.innerHTML = "";
  for (const it of items) {
    const b = el("button", it.value === current ? "on" : "", esc(it.label));
    b.disabled = !!it.disabled;
    b.onclick = () => onPick(it.value);
    container.appendChild(b);
  }
}
function buildTopControls() {
  const L = state.latest;
  const fiats = L?.meta?.fiats || ["CNY", "USD", "EUR", "HKD"];
  const abf = L?.meta?.assetsByFiat || { CNY: ["USDT", "BTC", "ETH", "USDC"] };
  buildSeg($("#fiat-seg"), fiats.map((f) => ({ value: f, label: f })), state.fiat, (v) => {
    state.fiat = v;
    const allowed = abf[v] || ["USDT"];
    if (!allowed.includes(state.asset)) state.asset = allowed[0];
    buildTopControls(); renderAll(); renderHistoryChart(); renderSparklines();
  });
  const allowed = abf[state.fiat] || ["USDT"];
  const allAssets = [...new Set(Object.values(abf).flat())];
  buildSeg($("#asset-seg"),
    allAssets.map((a) => ({ value: a, label: a, disabled: !allowed.includes(a) })),
    state.asset, (v) => { state.asset = v; renderAll(); renderHistoryChart(); renderSparklines(); });
}

/* ---------------- 指数横幅 ---------------- */
function renderBanner() {
  const L = state.latest;
  const ms = curMarkets();
  const mids = [];
  for (const ex of Object.keys(EX_META)) {
    const m = ms[ex];
    if (!m?.ok) continue;
    const b = best(m, "buy"), s = best(m, "sell");
    const mid = b && s ? (b + s) / 2 : (b || s);
    if (mid) mids.push(mid);
  }
  const avgMid = mids.length ? mids.reduce((x, y) => x + y) / mids.length : null;
  const fair = fairPrice();
  const idx = avgMid && fair ? (avgMid / fair) * 100 : null;

  const v = $("#premium-index");
  v.textContent = idx ? idx.toFixed(2) : "--";
  v.className = "banner-value " + (idx ? (idx >= 100 ? "up" : "down") : "");
  $("#premium-tag").textContent = idx
    ? (idx >= 100
        ? `场外溢价 ${fmtPct(idx - 100)} · 入金需求偏强`
        : `场外折价 ${fmtPct(idx - 100)} · 出金需求偏强`)
    : "暂无足够数据";

  const fx = L?.fx?.[state.fiat];
  const spot = L?.spot?.[state.asset];
  const cells = [
    { k: `USD/${state.fiat} 官方汇率`, v: fx ? fmtP(fx, 4) : "--", s: "open.er-api.com" },
    { k: `${state.asset} 现货指数`, v: spot ? "$" + fmtP(spot) : "--", s: "Binance/Bybit 现货" },
    { k: "全市场均价(中间价)", v: avgMid ? FIAT_SYMBOL[state.fiat] + fmtP(avgMid) : "--", s: `${mids.length} 家交易所` },
    { k: "公允参考价", v: fair ? FIAT_SYMBOL[state.fiat] + fmtP(fair) : "--", s: "汇率 × 现货" },
  ];
  const g = $("#banner-grid");
  g.innerHTML = "";
  for (const c of cells) {
    g.appendChild(el("div", "banner-cell",
      `<div class="k">${esc(c.k)}</div><div class="v">${c.v}</div><div class="s">${esc(c.s)}</div>`));
  }
}

/* ---------------- 交易所卡片 ---------------- */
function renderCards() {
  const ms = curMarkets();
  $("#cards-sub").textContent = `${state.asset}/${state.fiat} · 买一/卖一与 24h 走势`;
  const wrap = $("#cards");
  wrap.innerHTML = "";
  for (const [ex, meta] of Object.entries(EX_META)) {
    const m = ms[ex];
    const card = el("div", "card");
    card.style.setProperty("--ex-color", meta.color);
    const b = best(m, "buy"), s = best(m, "sell");
    const pb = premiumOf(b), psell = premiumOf(s);
    const sumBuy = (m?.buy || []).reduce((x, a) => x + (a.amount || 0), 0);
    const dotCls = m?.ok ? "ok" : "err";
    let body;
    if (m?.ok) {
      body = `
        <div class="card-prices">
          <div class="cp"><div class="k">买一价(我买)</div><div class="v buy">${fmtP(b)}</div><div class="s">溢价 ${fmtPct(pb)}</div></div>
          <div class="cp"><div class="k">卖一价(我卖)</div><div class="v sell">${fmtP(s)}</div><div class="s">溢价 ${fmtPct(psell)}</div></div>
        </div>
        <canvas class="sparkline" data-ex="${ex}" height="34"></canvas>
        <div class="card-foot">
          <span>价差 ${b && s ? fmtP(b - s) + " (" + fmtPct(((b - s) / s) * 100, false) + ")" : "--"}</span>
          <span>买盘挂单 ${fmtAmt(sumBuy)} ${esc(state.asset)}</span>
        </div>`;
    } else {
      body = `<div class="card-err">⚠ ${esc(shortErr(m?.error))}</div>
        <div class="card-foot"><span>该交易对暂无数据</span></div>`;
    }
    card.innerHTML = `
      <div class="card-head">
        <span class="card-name">${esc(meta.name)}</span>
        <span class="dot ${dotCls}"></span>
      </div>${body}`;
    wrap.appendChild(card);
  }
  renderSparklines();
}

function shortErr(err) {
  if (!err) return "无数据";
  if (/403|Access Denied/i.test(err)) return "交易所接口风控限制, 暂不可用";
  if (/451|restricted|unavailable.*legal/i.test(err)) return "接口对采集地区限制访问";
  if (/未开通此法币市场|无货币映射/.test(err)) return "该交易所未开通此交易对市场";
  if (/无广告数据/.test(err)) return "该交易所不支持此交易对(或暂无挂单)";
  return err.length > 90 ? err.slice(0, 90) + "…" : err;
}

function renderSparklines() {
  if (!state.history) return;
  const now = Math.floor(Date.now() / 1000);
  document.querySelectorAll(".sparkline").forEach((cv) => {
    const ex = cv.dataset.ex;
    const key = `${state.fiat}|${state.asset}|${ex}`;
    const pts = (state.history.series?.[key] || [])
      .filter((p) => now - p[0] <= 86400 && p[1] != null);
    const ctx = cv.getContext("2d");
    const W = (cv.width = cv.clientWidth || 280), H = cv.height;
    ctx.clearRect(0, 0, W, H);
    if (pts.length < 2) {
      ctx.fillStyle = "#5c677f"; ctx.font = "11px sans-serif";
      ctx.fillText("历史数据积累中…", 4, H / 2 + 4);
      return;
    }
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    let y0 = Math.min(...ys), y1 = Math.max(...ys);
    if (y1 - y0 < 1e-9) { y0 -= 1e-3; y1 += 1e-3; }
    ctx.beginPath();
    pts.forEach((p, i) => {
      const x = ((p[0] - x0) / (x1 - x0)) * (W - 2) + 1;
      const y = H - 3 - ((p[1] - y0) / (y1 - y0)) * (H - 6);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.strokeStyle = EX_META[ex]?.color || "#4f8cff";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  });
}

/* ---------------- 跨所对比表 ---------------- */
function renderCompare() {
  const ms = curMarkets();
  const tb = $("#compare-table tbody");
  tb.innerHTML = "";
  const rows = [];
  for (const [ex, meta] of Object.entries(EX_META)) {
    const m = ms[ex];
    const b = best(m, "buy"), s = best(m, "sell");
    rows.push({
      ex, meta, m, b, s,
      sumB: (m?.buy || []).reduce((x, a) => x + (a.amount || 0), 0),
      sumS: (m?.sell || []).reduce((x, a) => x + (a.amount || 0), 0),
    });
  }
  const okRows = rows.filter((r) => r.m?.ok);
  const minBuy = Math.min(...okRows.filter((r) => r.b).map((r) => r.b));
  const maxSell = Math.max(...okRows.filter((r) => r.s).map((r) => r.s));
  for (const r of rows) {
    const tr = el("tr");
    const sym = FIAT_SYMBOL[state.fiat] || "";
    const spread = r.b && r.s ? r.b - r.s : null;
    tr.innerHTML = `
      <td><span class="ex-chip" style="--ex-color:${r.meta.color}"><i></i>${esc(r.meta.name)}</span></td>
      <td class="${r.b === minBuy ? "best" : ""}">${r.b ? sym + fmtP(r.b) : "--"}</td>
      <td class="${r.s === maxSell ? "best" : ""}">${r.s ? sym + fmtP(r.s) : "--"}</td>
      <td>${spread != null ? fmtP(spread) : "--"}</td>
      <td class="${cls(premiumOf(r.b))}">${fmtPct(premiumOf(r.b))}</td>
      <td class="${cls(premiumOf(r.s))}">${fmtPct(premiumOf(r.s))}</td>
      <td>${fmtAmt(r.sumB)}</td>
      <td>${fmtAmt(r.sumS)}</td>
      <td>${r.m?.ok ? '<span class="dot ok"></span>' : `<span class="dot err" title="${esc(r.m?.error || "")}"></span>`}</td>`;
    tb.appendChild(tr);
  }
  function cls(v) { return v == null ? "" : v >= 0 ? "pos" : "neg"; }
}

/* ---------------- 套利监测 ---------------- */
function renderArb() {
  const ms = curMarkets();
  const tb = $("#arb-table tbody");
  tb.innerHTML = "";
  const ops = [];
  const exs = Object.keys(EX_META).filter((e) => ms[e]?.ok);
  for (const a of exs) {
    for (const b of exs) {
      if (a === b) continue;
      const buyP = best(ms[a], "buy");   // 在 a 买入
      const sellP = best(ms[b], "sell"); // 在 b 卖出
      if (!buyP || !sellP) continue;
      ops.push({ a, b, buyP, sellP, diff: sellP - buyP, pct: ((sellP - buyP) / buyP) * 100 });
    }
  }
  ops.sort((x, y) => y.pct - x.pct);
  state._maxArb = ops.length ? ops[0].pct : null;
  const top = ops.slice(0, 6);
  if (!top.length) {
    tb.appendChild(el("tr", "", '<td colspan="7" class="empty">暂无足够数据</td>'));
    return;
  }
  const sym = FIAT_SYMBOL[state.fiat] || "";
  top.forEach((o, i) => {
    const tr = el("tr");
    tr.innerHTML = `
      <td>${i + 1}</td>
      <td><span class="ex-chip" style="--ex-color:${EX_META[o.a].color}"><i></i>${esc(EX_META[o.a].name)}</span></td>
      <td>${sym}${fmtP(o.buyP)}</td>
      <td><span class="ex-chip" style="--ex-color:${EX_META[o.b].color}"><i></i>${esc(EX_META[o.b].name)}</span></td>
      <td>${sym}${fmtP(o.sellP)}</td>
      <td class="${o.diff >= 0 ? "best" : "worst"}">${fmtP(o.diff)}</td>
      <td class="${o.pct >= 0 ? "best" : "worst"}">${fmtPct(o.pct)}</td>`;
    tb.appendChild(tr);
  });
}

/* ---------------- ECharts 公共 ---------------- */
let depthChart = null, histChart = null;
function chartBase() {
  return {
    backgroundColor: "transparent",
    textStyle: { color: "#8a94ad" },
    tooltip: { trigger: "axis", backgroundColor: "#1b2540", borderColor: "#25304d",
               textStyle: { color: "#e8edf7", fontSize: 12 } },
    grid: { left: 70, right: 24, top: 42, bottom: 46 },
  };
}
function ensureCharts() {
  if (typeof echarts === "undefined") return false;
  if (!depthChart) depthChart = echarts.init($("#depth-chart"));
  if (!histChart) histChart = echarts.init($("#history-chart"));
  return true;
}
window.__echartsReady = () => { ensureCharts(); renderDepth(); renderHistoryChart(); };

/* ---------------- 深度图 ---------------- */
function renderDepth() {
  if (!ensureCharts() || !state.latest) return;
  const ms = curMarkets();
  const series = [];
  for (const [ex, meta] of Object.entries(EX_META)) {
    const m = ms[ex];
    if (!m?.ok) continue;
    for (const side of ["buy", "sell"]) {
      const ads = (m[side] || []).slice().sort((a, b) =>
        side === "buy" ? a.price - b.price : b.price - a.price);
      let cum = 0;
      const data = ads.map((a) => { cum += a.amount || 0; return [a.price, +cum.toFixed(2)]; });
      if (!data.length) continue;
      series.push({
        name: `${meta.name}·${side === "buy" ? "可买" : "可卖"}`,
        type: "line", step: "end", showSymbol: false,
        data: side === "buy" ? data : data.slice().reverse(),
        lineStyle: { color: meta.color, width: 1.8, type: side === "buy" ? "solid" : "dashed" },
        itemStyle: { color: meta.color },
      });
    }
  }
  depthChart.setOption({
    ...chartBase(),
    legend: { textStyle: { color: "#8a94ad", fontSize: 11 }, type: "scroll", top: 4 },
    xAxis: { type: "value", scale: true, name: `价格 (${state.fiat})`,
             axisLine: { lineStyle: { color: "#25304d" } },
             splitLine: { lineStyle: { color: "#1b2540" } } },
    yAxis: { type: "value", name: `累计挂单 (${state.asset})`,
             axisLine: { lineStyle: { color: "#25304d" } },
             splitLine: { lineStyle: { color: "#1b2540" } } },
    series,
  }, true);
}

/* ---------------- 历史走势 ---------------- */
function fxAt(ts) {
  // 取最接近 ts 的汇率点
  const arr = state.history?.series?.[`fx|${state.fiat}`] || [];
  if (!arr.length) return state.latest?.fx?.[state.fiat] || null;
  let bestP = arr[0];
  for (const p of arr) if (Math.abs(p[0] - ts) < Math.abs(bestP[0] - ts)) bestP = p;
  return bestP[1];
}
function renderHistoryChart() {
  if (!ensureCharts() || !state.history) return;
  const now = Math.floor(Date.now() / 1000);
  const series = [];
  const isPremium = state.histMode === "premium";
  const isSpread = state.histMode === "spread";
  for (const [ex, meta] of Object.entries(EX_META)) {
    const key = `${state.fiat}|${state.asset}|${ex}`;
    const pts = (state.history.series?.[key] || []).filter((p) => now - p[0] <= state.histRange);
    if (!pts.length) continue;
    const data = [];
    for (const [ts, b1, s1] of pts) {
      let v = null;
      if (isPremium) {
        const fx = fxAt(ts);
        const spot = state.asset === "USDT" ? 1 : state.latest?.spot?.[state.asset];
        if (fx && spot && b1) v = (b1 / (fx * spot) - 1) * 100;
      } else if (isSpread) {
        if (b1 != null && s1 != null) v = b1 - s1;
      } else {
        v = b1;
      }
      if (v != null) data.push([ts * 1000, +v.toFixed(4)]);
    }
    if (data.length) {
      series.push({
        name: meta.name, type: "line", showSymbol: false, smooth: true,
        data, lineStyle: { color: meta.color, width: 1.8 }, itemStyle: { color: meta.color },
      });
    }
  }
  const yName = isPremium ? "买一价溢价 %" : isSpread ? `买卖价差 (${state.fiat})` : `买一价 (${state.fiat})`;
  histChart.setOption({
    ...chartBase(),
    legend: { textStyle: { color: "#8a94ad", fontSize: 11 }, type: "scroll", top: 4 },
    xAxis: { type: "time", axisLine: { lineStyle: { color: "#25304d" } },
             splitLine: { show: false } },
    yAxis: { type: "value", scale: true, name: yName,
             axisLine: { lineStyle: { color: "#25304d" } },
             splitLine: { lineStyle: { color: "#1b2540" } } },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8,
                 borderColor: "#25304d", backgroundColor: "#111726" }],
    series: series.length ? series : [{ type: "line", data: [] }],
    graphic: series.length ? [] : [{
      type: "text", left: "center", top: "middle",
      style: { text: "历史数据积累中, 稍后回来查看", fill: "#5c677f", fontSize: 14 },
    }],
  }, true);
}

/* ---------------- 商家挂单 ---------------- */
function renderAds() {
  const ms = curMarkets();
  const tb = $("#ads-table tbody");
  tb.innerHTML = "";
  let rows = [];
  for (const [ex, meta] of Object.entries(EX_META)) {
    if (state.exFilter && state.exFilter !== ex) continue;
    const m = ms[ex];
    if (!m?.ok) continue;
    for (const a of m[state.side] || []) rows.push({ ex, meta, a });
  }
  if (state.payFilter) {
    const key = state.payFilter;
    rows = rows.filter((r) => (r.a.methods || []).some((m) => {
      if (key === "支付宝") return payClass(m) === "alipay";
      if (key === "微信") return payClass(m) === "wechat";
      if (key === "银行卡") return payClass(m) === "bank";
      return true;
    }));
  }
  if (state.amountFilter > 0) {
    rows = rows.filter((r) => (!r.a.min || r.a.min <= state.amountFilter) &&
                              (!r.a.max || r.a.max >= state.amountFilter));
  }
  rows.sort((x, y) => state.side === "buy" ? x.a.price - y.a.price : y.a.price - x.a.price);
  if (!rows.length) {
    tb.appendChild(el("tr", "", '<td colspan="9" class="empty">没有符合条件的挂单</td>'));
    return;
  }
  const sym = FIAT_SYMBOL[state.fiat] || "";
  for (const { ex, meta, a } of rows.slice(0, 60)) {
    const prem = premiumOf(a.price);
    const tr = el("tr");
    tr.innerHTML = `
      <td><span class="ex-chip" style="--ex-color:${meta.color}"><i></i>${esc(meta.name)}</span></td>
      <td class="l">${esc(a.merchant || "-")}</td>
      <td><strong>${sym}${fmtP(a.price)}</strong></td>
      <td class="${prem == null ? "" : prem >= 0 ? "pos" : "neg"}">${fmtPct(prem)}</td>
      <td>${fmtAmt(a.amount)} ${esc(state.asset)}</td>
      <td>${sym}${fmtAmt(a.min)} - ${sym}${fmtAmt(a.max)}</td>
      <td class="l">${payTags(a.methods) || '<span class="pay-tag">未知</span>'}</td>
      <td>${a.orders ? a.orders.toLocaleString() : "--"}</td>
      <td class="${a.rate == null ? "" : a.rate >= 97 ? "rate-hi" : "rate-lo"}">${a.rate != null ? a.rate + "%" : "--"}</td>`;
    tb.appendChild(tr);
  }
}

function buildAdsToolbar() {
  buildSeg($("#side-seg"),
    [{ value: "buy", label: "我要买入" }, { value: "sell", label: "我要卖出" }],
    state.side, (v) => { state.side = v; buildAdsToolbar(); renderAds(); });
  buildSeg($("#ex-filter"),
    [{ value: "", label: "全部交易所" },
     ...Object.entries(EX_META).map(([k, m]) => ({ value: k, label: m.name.split(" ")[0] }))],
    state.exFilter, (v) => { state.exFilter = v; buildAdsToolbar(); renderAds(); });
  buildSeg($("#pay-filter"),
    [{ value: "", label: "全部支付" }, { value: "支付宝", label: "支付宝" },
     { value: "微信", label: "微信" }, { value: "银行卡", label: "银行卡" }],
    state.payFilter, (v) => { state.payFilter = v; buildAdsToolbar(); renderAds(); });
}

/* ---------------- 数据源状态 ---------------- */
function renderSources() {
  const L = state.latest;
  const g = $("#src-grid");
  g.innerHTML = "";
  const ms = curMarkets();
  for (const [ex, meta] of Object.entries(EX_META)) {
    const m = ms[ex];
    const ok = m?.ok;
    g.appendChild(el("div", "src-cell", `
      <div class="name"><span class="dot ${ok ? "ok" : "err"}"></span>${esc(meta.name)}</div>
      <div class="msg">${ok
        ? `正常 · ${(m.buy || []).length} 买盘 / ${(m.sell || []).length} 卖盘广告`
        : esc(shortErr(m?.error))}</div>`));
  }
  if (L?.meta) {
    g.appendChild(el("div", "src-cell", `
      <div class="name"><span class="dot ${L.meta.okMarkets ? "ok" : "err"}"></span>采集器</div>
      <div class="msg">本轮 ${L.meta.okMarkets} 个市场成功 / ${L.meta.errMarkets} 个失败 · 用时 ${L.meta.fetchSeconds}s · 数据源: ${esc(state.activeSource)}</div>`));
  }
}

/* ---------------- 预警 ---------------- */
const ALERT_KEY = "otc-radar-alerts";
function getAlerts() {
  try { return JSON.parse(localStorage.getItem(ALERT_KEY)) || []; } catch { return []; }
}
function saveAlerts(a) { localStorage.setItem(ALERT_KEY, JSON.stringify(a)); }

function buildAlertUI() {
  const exSel = $("#alert-ex");
  exSel.innerHTML = '<option value="*">任一交易所</option>' +
    Object.entries(EX_META).map(([k, m]) => `<option value="${k}">${esc(m.name)}</option>`).join("");
  $("#alert-add").onclick = () => {
    const value = parseFloat($("#alert-value").value);
    if (isNaN(value)) { alert("请输入阈值"); return; }
    const list = getAlerts();
    list.push({
      id: Date.now(), metric: $("#alert-metric").value, ex: $("#alert-ex").value,
      op: $("#alert-op").value, value, fiat: state.fiat, asset: state.asset, lastFired: 0,
    });
    saveAlerts(list);
    renderAlertList();
  };
  $("#alert-notify").onclick = async () => {
    if (!("Notification" in window)) { alert("当前浏览器不支持通知"); return; }
    const p = await Notification.requestPermission();
    $("#alert-notify").textContent = p === "granted" ? "✓ 通知已开启" : "开启浏览器通知";
  };
  renderAlertList();
}
const METRIC_NAME = { index: "溢价指数", buy1: "买一价", sell1: "卖一价", arb: "最大跨所毛利率%" };
function renderAlertList() {
  const ul = $("#alert-list");
  ul.innerHTML = "";
  const list = getAlerts();
  if (!list.length) {
    ul.appendChild(el("li", "", '<span class="muted">暂无预警规则</span>'));
    return;
  }
  for (const a of list) {
    const li = el("li", a.lastFired && Date.now() - a.lastFired < 600000 ? "fired" : "");
    const exName = a.ex === "*" ? "任一交易所" : (EX_META[a.ex]?.name || a.ex);
    li.innerHTML = `
      <span>${esc(a.asset)}/${esc(a.fiat)} · ${esc(exName)} · ${esc(METRIC_NAME[a.metric] || a.metric)} ${a.op === ">" ? "高于" : "低于"} <strong>${a.value}</strong></span>
      <span class="spacer"></span>
      ${a.lastFired ? `<span class="muted">上次触发 ${new Date(a.lastFired).toLocaleTimeString("zh-CN")}</span>` : ""}
      <button class="btn danger" data-id="${a.id}">删除</button>`;
    li.querySelector("button").onclick = (e) => {
      saveAlerts(getAlerts().filter((x) => x.id !== +e.target.dataset.id));
      renderAlertList();
    };
    ul.appendChild(li);
  }
}
function metricValue(a) {
  const ms = state.latest?.markets?.[a.fiat]?.[a.asset] || {};
  const exs = a.ex === "*" ? Object.keys(EX_META) : [a.ex];
  if (a.metric === "index") {
    const fx = state.latest?.fx?.[a.fiat], spot = state.latest?.spot?.[a.asset];
    if (!fx || !spot) return null;
    const mids = exs.map((e) => {
      const b = best(ms[e], "buy"), s = best(ms[e], "sell");
      return b && s ? (b + s) / 2 : (b || s);
    }).filter(Boolean);
    return mids.length ? (mids.reduce((x, y) => x + y) / mids.length / (fx * spot)) * 100 : null;
  }
  if (a.metric === "arb") return state._maxArb;
  const vals = exs.map((e) => best(ms[e], a.metric === "buy1" ? "buy" : "sell")).filter(Boolean);
  if (!vals.length) return null;
  // "高于"取最大值比较, "低于"取最小值比较 — 任一交易所满足即触发
  return a.op === ">" ? Math.max(...vals) : Math.min(...vals);
}
function checkAlerts() {
  const list = getAlerts();
  let changed = false;
  for (const a of list) {
    const v = metricValue(a);
    if (v == null) continue;
    const hit = a.op === ">" ? v > a.value : v < a.value;
    if (hit && Date.now() - (a.lastFired || 0) > 600000) {  // 10 分钟冷却
      a.lastFired = Date.now();
      changed = true;
      const msg = `${a.asset}/${a.fiat} ${METRIC_NAME[a.metric]} 当前 ${v.toFixed(3)}, ${a.op === ">" ? "高于" : "低于"}阈值 ${a.value}`;
      if ("Notification" in window && Notification.permission === "granted") {
        new Notification("📡 OTC 雷达预警", { body: msg });
      }
      beep();
      console.warn("[alert]", msg);
    }
  }
  if (changed) { saveAlerts(list); renderAlertList(); }
}
function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.frequency.value = 880; g.gain.value = 0.08;
    o.start(); o.stop(ctx.currentTime + 0.35);
  } catch { /* 静默 */ }
}

/* ---------------- 状态栏 ---------------- */
function updateStatusBar() {
  const L = state.latest;
  const dot = $("#conn-dot");
  if (!L) {
    dot.className = "dot err";
    $("#update-time").textContent = "数据加载失败, 等待重试…";
    return;
  }
  const age = Math.floor(Date.now() / 1000) - L.updated;
  dot.className = "dot " + (age < 900 ? "ok" : age < 3600 ? "warn" : "err");
  const t = new Date(L.updated * 1000).toLocaleString("zh-CN", { hour12: false });
  $("#update-time").textContent = `数据更新于 ${t}` + (age >= 900 ? `(${Math.floor(age / 60)} 分钟前)` : "");
  $("#src-health").textContent = `${L.meta?.okMarkets ?? "-"}/${(L.meta?.okMarkets ?? 0) + (L.meta?.errMarkets ?? 0)} 市场正常`;
}

/* ---------------- 总渲染 ---------------- */
function renderAll() {
  if (!state.latest) return;
  buildTopControls();
  renderBanner();
  renderCards();
  renderCompare();
  renderArb();
  renderDepth();
  renderAds();
  renderSources();
}

/* ---------------- 初始化 ---------------- */
function initToolbars() {
  $("#hist-mode").querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      $("#hist-mode").querySelectorAll("button").forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      state.histMode = b.dataset.v;
      renderHistoryChart();
    };
  });
  $("#hist-range").querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      $("#hist-range").querySelectorAll("button").forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      state.histRange = +b.dataset.v;
      renderHistoryChart();
    };
  });
  $("#amount-filter").oninput = (e) => {
    state.amountFilter = parseFloat(e.target.value) || null;
    renderAds();
  };
  buildAdsToolbar();
  buildAlertUI();
}

function tick() {
  state.countdown -= 1;
  if (state.countdown <= 0) {
    state.countdown = REFRESH_SEC;
    loadLatest();
  }
  $("#countdown").textContent = state.countdown;
}

window.addEventListener("resize", () => {
  depthChart?.resize();
  histChart?.resize();
  renderSparklines();
});

initToolbars();
loadLatest();
loadHistory();
setInterval(tick, 1000);
setInterval(loadHistory, HISTORY_REFRESH_SEC * 1000);
