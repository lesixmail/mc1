#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OTC Monitor 持久化数据库层 (SQLite, 标准库)。

作为"系统记录库": 持久保存所有商家、可疑压价事件、每日渠道盘口、汇率与成交量估算,
跨 CI 运行连续累积 (otc.db 随 otc-data 分支携带)。原始盘口只滚动保留近 72 小时,
长期数据全部以聚合形式保存, 控制体积。

前端不直接读 SQLite, 而是消费本层导出的紧凑 JSON 投影:
  merchants.json   商家长期档案 (含压价累计)
  suspicious.json  可疑事件日志 + 商家滚动统计
  channels.json    当日各支付渠道盘口/均价/最低价
  series.json      30 日: 场外 USDT/CNY 价 vs 人民银行中间价 + 成交量估算区间
"""
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

OBS_KEEP = 72 * 3600          # 原始盘口观测保留 72 小时
EVENT_KEEP = 60 * 86400       # 可疑事件保留 60 天
DAILY_KEEP = 120 * 86400      # 每日聚合保留 120 天


def _day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%Y-%m-%d")


def connect(path):
    # 单文件 journal (DELETE): 跨 CI 运行只需携带 otc.db 一个文件即可完整恢复
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS merchants (
      exchange TEXT, merchant TEXT,
      first_seen INTEGER, last_seen INTEGER,
      obs_count INTEGER DEFAULT 0, susp_count INTEGER DEFAULT 0,
      PRIMARY KEY (exchange, merchant)
    );
    -- 商家在某 (资产,法币,买卖向) 上的长期滚动统计
    CREATE TABLE IF NOT EXISTS merchant_stats (
      exchange TEXT, asset TEXT, fiat TEXT, side TEXT, merchant TEXT,
      samples INTEGER DEFAULT 0,
      sum_price REAL DEFAULT 0, min_price REAL, max_price REAL,
      last_price REAL, last_amount REAL, last_ts INTEGER,
      sum_below REAL DEFAULT 0,        -- 累计低于 Top2 的绝对值(仅压价样本)
      susp_samples INTEGER DEFAULT 0,  -- 被判定压价的样本数
      max_below_pct REAL DEFAULT 0,    -- 历史最大压价幅度 %
      last_orders INTEGER,             -- 最近月成交单
      last_rate REAL,                  -- 最近成单率
      PRIMARY KEY (exchange, asset, fiat, side, merchant)
    );
    -- 原始盘口观测 (滚动)
    CREATE TABLE IF NOT EXISTS observations (
      ts INTEGER, exchange TEXT, asset TEXT, fiat TEXT, side TEXT,
      merchant TEXT, price REAL, amount REAL, rank INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_obs_ts ON observations(ts);
    -- 可疑压价事件 (着急出货)
    CREATE TABLE IF NOT EXISTS suspicious_events (
      ts INTEGER, exchange TEXT, asset TEXT, fiat TEXT, side TEXT,
      merchant TEXT, price REAL, top2_price REAL,
      below_abs REAL, below_pct REAL, amount REAL,
      orders INTEGER, rate REAL
    );
    CREATE INDEX IF NOT EXISTS idx_susp_ts ON suspicious_events(ts);
    -- 每日支付渠道盘口统计
    CREATE TABLE IF NOT EXISTS channel_daily (
      day TEXT, asset TEXT, fiat TEXT, channel TEXT, side TEXT,
      samples INTEGER DEFAULT 0, sum_price REAL DEFAULT 0,
      best_price REAL, min_price REAL, max_price REAL,
      PRIMARY KEY (day, asset, fiat, channel, side)
    );
    -- 每日汇率对照 (场外 USDT vs 人民银行中间价)
    CREATE TABLE IF NOT EXISTS rate_daily (
      day TEXT, asset TEXT, fiat TEXT,
      otc_mid REAL, pboc REAL, market REAL,
      premium_pboc REAL, premium_market REAL, samples INTEGER DEFAULT 0,
      PRIMARY KEY (day, asset, fiat)
    );
    -- 每日成交量估算
    CREATE TABLE IF NOT EXISTS volume_daily (
      day TEXT, asset TEXT, fiat TEXT,
      standing_liq REAL, est_low REAL, est_high REAL,
      est_low_fiat REAL, est_high_fiat REAL,
      merchants INTEGER, monthly_orders INTEGER, samples INTEGER DEFAULT 0,
      PRIMARY KEY (day, asset, fiat)
    );
    """)
    conn.commit()


def _merchant_touch(conn, ex, merchant, ts, suspicious):
    conn.execute("""
      INSERT INTO merchants(exchange,merchant,first_seen,last_seen,obs_count,susp_count)
      VALUES (?,?,?,?,1,?)
      ON CONFLICT(exchange,merchant) DO UPDATE SET
        last_seen=excluded.last_seen,
        obs_count=obs_count+1,
        susp_count=susp_count+excluded.susp_count
    """, (ex, merchant, ts, ts, 1 if suspicious else 0))


def _stats_touch(conn, ex, asset, fiat, side, ad, ts, below_abs, below_pct, suspicious):
    price = ad["price"]
    conn.execute("""
      INSERT INTO merchant_stats(exchange,asset,fiat,side,merchant,samples,sum_price,
        min_price,max_price,last_price,last_amount,last_ts,sum_below,susp_samples,
        max_below_pct,last_orders,last_rate)
      VALUES (?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(exchange,asset,fiat,side,merchant) DO UPDATE SET
        samples=samples+1,
        sum_price=sum_price+excluded.sum_price,
        min_price=MIN(min_price,excluded.min_price),
        max_price=MAX(max_price,excluded.max_price),
        last_price=excluded.last_price,
        last_amount=excluded.last_amount,
        last_ts=excluded.last_ts,
        sum_below=sum_below+excluded.sum_below,
        susp_samples=susp_samples+excluded.susp_samples,
        max_below_pct=MAX(max_below_pct,excluded.max_below_pct),
        last_orders=excluded.last_orders,
        last_rate=excluded.last_rate
    """, (ex, asset, fiat, side, ad["merchant"], price, price, price, price,
          ad.get("amount"), ts, below_abs if suspicious else 0,
          1 if suspicious else 0, below_pct if suspicious else 0,
          ad.get("orders"), ad.get("rate")))


def record(conn, ts, markets, analytics):
    """把一轮快照写入数据库: 商家档案/观测/可疑事件/渠道日聚合/汇率日/成交量日。"""
    day = _day(ts)
    # 商家 + 观测 + 压价统计
    for fiat, assets in markets.items():
        for asset, exmap in assets.items():
            for ex, m in exmap.items():
                if not m.get("ok"):
                    continue
                for side in ("buy", "sell"):
                    ads = m.get(side) or []
                    susp_set = _suspect_indices(ads, side)
                    for i, ad in enumerate(ads):
                        mer = ad.get("merchant") or ""
                        if not mer:
                            continue
                        info = susp_set.get(i)
                        suspicious = info is not None
                        _merchant_touch(conn, ex, mer, ts, suspicious)
                        _stats_touch(conn, ex, asset, fiat, side, ad, ts,
                                     info[0] if info else 0,
                                     info[1] if info else 0, suspicious)
                        if i < 8:  # 仅存前 8 档原始观测, 控制体积
                            conn.execute(
                                "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)",
                                (ts, ex, asset, fiat, side, mer, ad["price"],
                                 ad.get("amount"), i))
                        if suspicious:
                            conn.execute(
                                "INSERT INTO suspicious_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (ts, ex, asset, fiat, side, mer, ad["price"],
                                 info[2], info[0], info[1], ad.get("amount"),
                                 ad.get("orders"), ad.get("rate")))
    # 渠道日聚合
    for (asset, fiat, channel, side), st in (analytics.get("channel_rows") or {}).items():
        best = st["best"]
        conn.execute("""
          INSERT INTO channel_daily(day,asset,fiat,channel,side,samples,sum_price,best_price,min_price,max_price)
          VALUES (?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(day,asset,fiat,channel,side) DO UPDATE SET
            samples=samples+excluded.samples,
            sum_price=sum_price+excluded.sum_price,
            best_price=CASE WHEN ?='buy' THEN MIN(best_price,excluded.best_price)
                            ELSE MAX(best_price,excluded.best_price) END,
            min_price=MIN(min_price,excluded.min_price),
            max_price=MAX(max_price,excluded.max_price)
        """, (day, asset, fiat, channel, side, st["n"], st["sum"], best,
              st["min"], st["max"], side))
    # 汇率日 / 成交量日
    rc = (analytics.get("rate") or {}).get("CNY")
    if rc and rc.get("otc_mid"):
        conn.execute("""
          INSERT INTO rate_daily(day,asset,fiat,otc_mid,pboc,market,premium_pboc,premium_market,samples)
          VALUES (?,?,?,?,?,?,?,?,1)
          ON CONFLICT(day,asset,fiat) DO UPDATE SET
            otc_mid=(otc_mid*samples+excluded.otc_mid)/(samples+1),
            pboc=COALESCE(excluded.pboc,pboc),
            market=COALESCE(excluded.market,market),
            premium_pboc=excluded.premium_pboc,
            premium_market=excluded.premium_market,
            samples=samples+1
        """, (day, "USDT", "CNY", rc["otc_mid"], rc.get("pboc"), rc.get("market"),
              rc.get("premium_pboc"), rc.get("premium_market")))
    vol = (analytics.get("volume") or {}).get("CNY")
    if vol and vol.get("standing_liq"):
        conn.execute("""
          INSERT INTO volume_daily(day,asset,fiat,standing_liq,est_low,est_high,est_low_fiat,est_high_fiat,merchants,monthly_orders,samples)
          VALUES (?,?,?,?,?,?,?,?,?,?,1)
          ON CONFLICT(day,asset,fiat) DO UPDATE SET
            standing_liq=(standing_liq*samples+excluded.standing_liq)/(samples+1),
            est_low=(est_low*samples+excluded.est_low)/(samples+1),
            est_high=(est_high*samples+excluded.est_high)/(samples+1),
            est_low_fiat=(est_low_fiat*samples+excluded.est_low_fiat)/(samples+1),
            est_high_fiat=(est_high_fiat*samples+excluded.est_high_fiat)/(samples+1),
            merchants=excluded.merchants,
            monthly_orders=excluded.monthly_orders,
            samples=samples+1
        """, (day, "USDT", "CNY", vol["standing_liq"], vol["est_low"], vol["est_high"],
              vol["est_low_fiat"], vol["est_high_fiat"], vol.get("merchants", 0),
              vol.get("monthly_orders", 0)))
    conn.commit()


def _suspect_indices(ads, side, pct_thr=0.3, min_amount=50):
    """返回 {index: (below_abs, below_pct, top2_price)}: 明显压价/着急出货的广告。

    buy 侧(商家卖出 USDT): 价格按升序, 越低越"急售"; 与第 2 低价(Top2)比, 低于阈值即标记。
    sell 侧(商家买入 USDT): 价格按降序, 越高越"急购"; 与第 2 高价比, 高于阈值即标记。
    """
    valid = [(i, a) for i, a in enumerate(ads) if a.get("price", 0) > 0]
    if len(valid) < 3:
        return {}
    asc = side == "buy"
    ordered = sorted(valid, key=lambda x: x[1]["price"], reverse=not asc)
    # Top2 参考价 = 排第 2 好的价 (剔除自身最极端那条的影响)
    ref = ordered[1][1]["price"]
    out = {}
    for idx, ad in ordered:
        p = ad["price"]
        if asc:
            below_abs = ref - p
        else:
            below_abs = p - ref
        below_pct = below_abs / ref * 100 if ref else 0
        if below_pct >= pct_thr and (ad.get("amount") or 0) >= min_amount:
            out[idx] = (round(below_abs, 4), round(below_pct, 3), round(ref, 6))
    return out


def prune(conn, now):
    conn.execute("DELETE FROM observations WHERE ts < ?", (now - OBS_KEEP,))
    conn.execute("DELETE FROM suspicious_events WHERE ts < ?", (now - EVENT_KEEP,))
    cutoff_day = _day(now - DAILY_KEEP)
    for t in ("channel_daily", "rate_daily", "volume_daily"):
        conn.execute("DELETE FROM %s WHERE day < ?" % t, (cutoff_day,))
    conn.commit()


def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def write_projections(conn, out_dir, now):
    today = _day(now)
    # 1) 可疑事件: 最近 7 天事件 + 商家滚动统计 (按累计压价排序)
    events = _rows(conn, """
      SELECT ts,exchange,asset,fiat,side,merchant,price,top2_price,below_abs,below_pct,amount,orders,rate
      FROM suspicious_events WHERE ts >= ? ORDER BY ts DESC LIMIT 400
    """, (now - 7 * 86400,))
    flagged = _rows(conn, """
      SELECT exchange,asset,fiat,side,merchant,samples,susp_samples,
             sum_price/NULLIF(samples,0) AS avg_price, min_price, last_price,
             last_amount, last_orders, last_rate, last_ts,
             sum_below/NULLIF(susp_samples,0) AS avg_below, max_below_pct
      FROM merchant_stats
      WHERE susp_samples > 0
      ORDER BY susp_samples DESC, max_below_pct DESC LIMIT 200
    """)
    _dump(out_dir, "suspicious.json", {
        "updated": now, "events": events, "flagged": flagged,
        "params": {"pct_threshold": 0.3, "window_days": 7},
    })
    # 2) 商家长期档案 (注册量 / 活跃 / 压价次数)
    merchants = _rows(conn, """
      SELECT exchange,merchant,first_seen,last_seen,obs_count,susp_count
      FROM merchants ORDER BY obs_count DESC LIMIT 500
    """)
    totals = _rows(conn, "SELECT exchange, COUNT(*) n FROM merchants GROUP BY exchange")
    _dump(out_dir, "merchants.json", {
        "updated": now, "merchants": merchants,
        "totals": {r["exchange"]: r["n"] for r in totals},
        "grand_total": sum(r["n"] for r in totals),
    })
    # 3) 当日渠道盘口表 (USDT/CNY 优先, 同时给出全部)
    chan = _rows(conn, """
      SELECT asset,fiat,channel,side,samples,sum_price/NULLIF(samples,0) AS avg_price,
             best_price, min_price, max_price
      FROM channel_daily WHERE day = ?
    """, (today,))
    _dump(out_dir, "channels.json", {"updated": now, "day": today, "rows": chan})
    # 4) 30 日序列: 场外 vs 人行中间价 + 成交量估算
    rate_series = _rows(conn, """
      SELECT day, otc_mid, pboc, market, premium_pboc, premium_market
      FROM rate_daily WHERE asset='USDT' AND fiat='CNY' ORDER BY day
    """)
    vol_series = _rows(conn, """
      SELECT day, standing_liq, est_low, est_high, est_low_fiat, est_high_fiat,
             merchants, monthly_orders
      FROM volume_daily WHERE asset='USDT' AND fiat='CNY' ORDER BY day
    """)
    _dump(out_dir, "series.json", {
        "updated": now, "rate": rate_series, "volume": vol_series,
    })


def _dump(out_dir, name, obj):
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))


def db_stats(conn):
    try:
        m = conn.execute("SELECT COUNT(*) FROM merchants").fetchone()[0]
        o = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        s = conn.execute("SELECT COUNT(*) FROM suspicious_events").fetchone()[0]
        return {"merchants": m, "observations": o, "suspicious": s}
    except Exception:  # noqa: BLE001
        return {}
