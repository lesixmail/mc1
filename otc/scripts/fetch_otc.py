#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OTC Market Monitor - 数据抓取器
抓取 Binance / OKX / Bybit / Gate / Bitget / HTX 六大交易所的 P2P/OTC 盘口,
以及美元法币汇率与现货指数价, 输出 latest.json 并维护 history.json 时间序列。

用法:
  python3 fetch_otc.py --out DIR [--probe]
"""
import argparse
import json
import os
import statistics
import sys
import time
import traceback
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod  # noqa: E402  (同目录持久化层)

UA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

ROWS = 15                      # 每侧抓取的广告条数
FIATS = ["CNY", "USD", "EUR", "HKD"]
ASSETS_BY_FIAT = {             # CNY 全资产, 其他法币只跟 USDT (控制请求量)
    "CNY": ["USDT", "BTC", "ETH", "USDC"],
    "USD": ["USDT"],
    "EUR": ["USDT"],
    "HKD": ["USDT"],
}
EXCHANGES = ["binance", "okx", "bybit", "gate", "bitget", "htx"]

HISTORY_MAX_AGE = 30 * 86400       # 历史保留 30 天
HISTORY_DENSE_AGE = 48 * 3600      # 48 小时内保留全部点, 更早的按小时降采样

PROBE = False


def log(msg):
    print("[fetch] %s" % msg, flush=True)


def http_json(method, url, *, params=None, json_body=None, data=None,
              headers=None, timeout=20, allow_proxy=True):
    """带公共代理回退的 HTTP JSON 请求 (应对交易所对 runner 地区的封锁)。"""
    hdrs = dict(UA_HEADERS)
    if headers:
        hdrs.update(headers)
    last_err = None
    try:
        r = requests.request(method, url, params=params, json=json_body,
                             data=data, headers=hdrs, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        last_err = "HTTP %s %s" % (r.status_code, r.text[:200])
        if PROBE:
            log("PROBE direct %s -> %s" % (url, last_err))
    except Exception as e:  # noqa: BLE001
        last_err = "%s: %s" % (type(e).__name__, e)
        if PROBE:
            log("PROBE direct %s -> %s" % (url, last_err))
    # 地区封锁/超时时尝试公共代理 (仅 GET 可代理)
    if allow_proxy and method.upper() == "GET":
        full = url
        if params:
            full = requests.Request("GET", url, params=params).prepare().url
        for proxy_tpl in (
            "https://api.allorigins.win/raw?url={u}",
            "https://api.codetabs.com/v1/proxy?quest={u}",
        ):
            try:
                purl = proxy_tpl.format(u=requests.utils.quote(full, safe=""))
                r = requests.get(purl, headers=hdrs, timeout=timeout + 10)
                if r.status_code == 200:
                    return r.json()
                if PROBE:
                    log("PROBE proxy %s -> HTTP %s" % (purl[:80], r.status_code))
            except Exception as e:  # noqa: BLE001
                if PROBE:
                    log("PROBE proxy fail %s" % e)
    raise RuntimeError(last_err or "request failed")


def fnum(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def norm_rate(v):
    """成单率归一化为百分数 0-100。"""
    x = fnum(v, -1)
    if x < 0:
        return None
    if x <= 1.0:
        x *= 100.0
    return round(min(x, 100.0), 1)


def make_ad(price, amount, lo, hi, methods, merchant, orders, rate, **extra):
    ad = {
        "price": round(fnum(price), 6),
        "amount": round(fnum(amount), 2),
        "min": round(fnum(lo), 2),
        "max": round(fnum(hi), 2),
        "methods": [str(m) for m in methods if m][:8],
        "merchant": str(merchant or "")[:28],
        "orders": int(fnum(orders)),
        "rate": norm_rate(rate),
    }
    # 富字段 (各所能给多少给多少; None 不写入, 前端大表按列展示)
    for k, v in extra.items():
        if v is None or v == "":
            continue
        ad[k] = v
    return ad


# ---------------------------------------------------------------- Binance ----
def fetch_binance(asset, fiat, user_side):
    # tradeType=BUY: 用户买入(商家卖出广告)
    body = {
        "asset": asset, "fiat": fiat, "merchantCheck": False,
        "page": 1, "rows": ROWS, "tradeType": "BUY" if user_side == "buy" else "SELL",
        "payTypes": [], "countries": [], "proMerchantAds": False,
        "shieldMerchantAds": False, "publisherType": None,
    }
    j = http_json("POST", "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search",
                  json_body=body)
    ads = []
    for it in (j.get("data") or []):
        adv = it.get("adv") or {}
        u = it.get("advertiser") or {}
        active = u.get("activeTimeInSecond")
        reg = u.get("registrationTime")  # 毫秒时间戳 (部分账号无)
        reg_days = None
        if reg:
            try:
                reg_days = int((time.time() - float(reg) / 1000) / 86400)
            except (TypeError, ValueError):
                reg_days = None
        ads.append(make_ad(
            adv.get("price"), adv.get("surplusAmount"),
            adv.get("minSingleTransAmount"), adv.get("maxSingleTransAmount"),
            [(m.get("tradeMethodName") or m.get("identifier") or "")
             for m in (adv.get("tradeMethods") or [])],
            u.get("nickName"), u.get("monthOrderCount"), u.get("monthFinishRate"),
            # —— 币安完整字段 ——
            positiveRate=norm_rate(u.get("positiveRate")),
            orderCount=_int_or_none(u.get("orderCount")),
            userType=("商家" if (u.get("userType") == "merchant" or u.get("proMerchant"))
                      else "用户"),
            proMerchant=bool(u.get("proMerchant")),
            vipLevel=_int_or_none(u.get("vipLevel")),
            userGrade=_int_or_none(u.get("userGrade")),
            userIdentity=u.get("userIdentity") or None,
            registerDays=reg_days,
            lastActiveMin=(round(fnum(active) / 60) if active not in (None, "") else None),
            margin=_num_or_none(u.get("margin")),
            marginUnit=u.get("marginUnit") or None,
            payTimeLimit=_int_or_none(adv.get("payTimeLimit")),
            minQuantity=_num_or_none(adv.get("minSingleTransQuantity")),
            tradableQuantity=_num_or_none(adv.get("tradableQuantity")),
            buyerRegDays=_int_or_none(adv.get("buyerRegDaysLimit")),
            buyerKyc=bool(adv.get("buyerKycLimit")),
            assetLocked=bool(adv.get("buyerBtcPositionLimit")),
            remark=(str(adv.get("remarks"))[:60] if adv.get("remarks") else None),
            advNo=adv.get("advNo") or None,
            priceType=("浮动" if adv.get("priceType") == 1 else "固定"),
        ))
    return ads


def _int_or_none(v):
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _num_or_none(v):
    try:
        return round(float(v), 4) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# -------------------------------------------------------------------- OKX ----
OKX_PAY = {"aliPay": "支付宝", "wxPay": "微信", "bank": "银行卡", "wechatPay": "微信"}


def fetch_okx(asset, fiat, user_side):
    # side=sell: 商家卖出 => 用户买入
    side = "sell" if user_side == "buy" else "buy"
    j = http_json("GET", "https://www.okx.com/v3/c2c/tradingOrders/books", params={
        "t": int(time.time() * 1000),
        "quoteCurrency": fiat.lower(), "baseCurrency": asset.lower(),
        "side": side, "paymentMethod": "all", "userType": "all",
        "showTrade": "false", "showFollow": "false",
        "showAlreadyTraded": "false", "isAbleFilter": "false",
    })
    data = (j.get("data") or {}).get(side) or []
    ads = []
    for it in data[:ROWS]:
        ads.append(make_ad(
            it.get("price"), it.get("availableAmount"),
            it.get("quoteMinAmountPerOrder"), it.get("quoteMaxAmountPerOrder"),
            [OKX_PAY.get(m, m) for m in (it.get("paymentMethods") or [])],
            it.get("nickName"), it.get("completedOrderQuantity"),
            it.get("completedRate")))
    return ads


# ------------------------------------------------------------------ Bybit ----
BYBIT_PAY_CACHE = {}


def bybit_payment_map():
    global BYBIT_PAY_CACHE
    if BYBIT_PAY_CACHE:
        return BYBIT_PAY_CACHE
    try:
        j = http_json("POST",
                      "https://api2.bybit.com/fiat/otc/configuration/queryAllPaymentList",
                      json_body={}, allow_proxy=False)
        items = (j.get("result") or {}).get("paymentConfigVo") or \
                (j.get("result") or {}).get("paymentList") or j.get("result") or []
        if isinstance(items, list):
            for p in items:
                pid = str(p.get("paymentType") or p.get("id") or "")
                name = p.get("paymentName") or p.get("name") or ""
                if pid and name:
                    BYBIT_PAY_CACHE[pid] = name
    except Exception as e:  # noqa: BLE001
        log("bybit payment map fail: %s" % e)
    return BYBIT_PAY_CACHE


def fetch_bybit(asset, fiat, user_side):
    # side=1: 商家卖出 => 用户买入 (若标注相反由 normalize 自动纠正)
    side = "1" if user_side == "buy" else "0"
    j = http_json("POST", "https://api2.bybit.com/fiat/otc/item/online", json_body={
        "userId": "", "tokenId": asset, "currencyId": fiat, "payment": [],
        "side": side, "size": str(ROWS), "page": "1", "amount": "",
        "authMaker": False, "canTrade": False,
    })
    res = j.get("result") or {}
    items = res.get("items") or []
    if not items:
        if str(j.get("ret_code")) == "0":
            raise RuntimeError("交易所未开通此法币市场(在线广告为 0)")
        raise RuntimeError("空列表: %s" % snip(j, 160))
    pmap = bybit_payment_map()
    ads = []
    for it in items[:ROWS]:
        ads.append(make_ad(
            it.get("price"), it.get("lastQuantity"),
            it.get("minAmount"), it.get("maxAmount"),
            [pmap.get(str(p), str(p)) for p in (it.get("payments") or [])],
            it.get("nickName"), it.get("recentOrderNum"),
            it.get("recentExecuteRate")))
    return ads


# ------------------------------------------------------------------- Gate ----
def snip(j, n=160):
    try:
        return json.dumps(j, ensure_ascii=False)[:n]
    except Exception:  # noqa: BLE001
        return str(j)[:n]


def gate_parse_rows(j):
    if not isinstance(j, dict):
        return None
    for k in ("push_order", "data", "datas", "list"):
        v = j.get(k)
        if isinstance(v, list) and v:
            return v
        if isinstance(v, dict):
            for kk in ("list", "rows", "push_order", "records"):
                if isinstance(v.get(kk), list) and v[kk]:
                    return v[kk]
    return None


def fetch_gate(asset, fiat, user_side):
    sym = "%s_%s" % (asset.upper(), fiat.upper())
    errors = []
    # 候选 1: 站内 json_svr push_order_list (gate.com 新域名优先, 旧域名兜底)
    push_type = "2" if user_side == "buy" else "1"  # 2=商家卖出列表
    for host in ("www.gate.com", "www.gate.io"):
        try:
            j = http_json("POST", "https://%s/json_svr/query_push/" % host,
                          data={"type": "push_order_list", "symbol": sym,
                                "big_trade": "0", "fiat_amount": "", "amount": "",
                                "pay_type": "", "is_blue": "0", "have_traded": "0",
                                "follow": "0", "per_page": str(ROWS),
                                "push_type": push_type, "page": "1"},
                          headers={"X-Requested-With": "XMLHttpRequest",
                                   "Referer": "https://%s/zh/c2c/%s" % (host, sym.lower().replace("_", "-"))},
                          allow_proxy=False)
            if PROBE:
                log("PROBE gate %s json_svr: %s" % (host, snip(j, 800)))
            rows = gate_parse_rows(j)
            if rows:
                ads = []
                for it in rows[:ROWS]:
                    ads.append(make_ad(
                        it.get("rate") or it.get("price"),
                        it.get("curr_amount") or it.get("amount"),
                        it.get("limit_min") or it.get("min_amount"),
                        it.get("limit_total") or it.get("limit_max") or it.get("max_amount"),
                        gate_paytypes(it),
                        it.get("username") or it.get("nickname") or it.get("user_name"),
                        it.get("deal_count") or it.get("month_finish_count"),
                        it.get("complete_rate") or it.get("deal_rate")))
                return ads
            errors.append("%s json_svr 空: %s" % (host, snip(j, 80)))
        except Exception as e:  # noqa: BLE001
            errors.append("%s json_svr: %s" % (host, str(e)[:90]))
    # 候选 2: apiw v2 GET (可走公共代理绕过风控)
    for host in ("www.gate.com", "www.gate.io"):
        try:
            side = "sell" if user_side == "buy" else "buy"
            j = http_json("GET", "https://%s/apiw/v2/c2c/advertisements" % host, params={
                "asset": asset.upper(), "fiat": fiat.upper(), "side": side,
                "page": 1, "limit": ROWS,
            }, allow_proxy=True)
            if PROBE:
                log("PROBE gate %s apiw: %s" % (host, snip(j, 800)))
            rows = gate_parse_rows(j) or []
            ads = []
            for it in rows[:ROWS]:
                ads.append(make_ad(
                    it.get("price"), it.get("available") or it.get("amount"),
                    it.get("min_limit") or it.get("minLimit"),
                    it.get("max_limit") or it.get("maxLimit"),
                    it.get("pay_types") or it.get("payTypes") or [],
                    (it.get("merchant") or {}).get("name") if isinstance(it.get("merchant"), dict)
                    else it.get("merchant_name") or it.get("nickname"),
                    it.get("orders") or it.get("order_count"),
                    it.get("rate") or it.get("finish_rate")))
            if ads:
                return ads
            errors.append("%s apiw 空: %s" % (host, snip(j, 80)))
        except Exception as e:  # noqa: BLE001
            errors.append("%s apiw: %s" % (host, str(e)[:90]))
    raise RuntimeError(" | ".join(errors))


GATE_PAY = {"1": "银行卡", "2": "支付宝", "3": "微信", "alipay": "支付宝",
            "wechat": "微信", "bank": "银行卡", "card": "银行卡"}


def gate_paytypes(it):
    raw = it.get("pay_type") or it.get("pay_types") or it.get("payments") or ""
    if isinstance(raw, list):
        vals = raw
    else:
        vals = str(raw).split(",")
    return [GATE_PAY.get(str(v).strip().lower(), str(v).strip()) for v in vals if str(v).strip()]


# ----------------------------------------------------------------- Bitget ----
def fetch_bitget(asset, fiat, user_side):
    # side=1: 用户买入(商家卖出) — 若相反由 normalize 纠正
    side = 1 if user_side == "buy" else 2
    errors = []
    for url, body in (
        ("https://www.bitget.com/v1/p2p/pub/adv/queryAdvList",
         {"side": side, "pageNo": 1, "pageSize": ROWS, "coinCode": asset,
          "fiatCode": fiat, "languageType": 6, "payMethodId": None, "amount": ""}),
        # 变体: 字符串 side + 常见可选过滤字段
        ("https://www.bitget.com/v1/p2p/pub/adv/queryAdvList",
         {"side": str(side), "pageNo": "1", "pageSize": str(ROWS),
          "coinCode": asset, "fiatCode": fiat, "languageType": 0,
          "orderBy": 1, "userType": 0, "amount": "", "payMethodIdList": []}),
        ("https://www.bitget.com/v1/p2p/pub/adv/list",
         {"side": side, "pageNo": 1, "pageSize": ROWS, "coinCode": asset,
          "fiatCode": fiat, "languageType": 6}),
    ):
        try:
            j = http_json("POST", url, json_body=body,
                          headers={"Referer": "https://www.bitget.com/zh-CN/p2p-trade",
                                   "language": "zh_CN"})
            if PROBE:
                log("PROBE bitget %s: %s" % (url, json.dumps(j, ensure_ascii=False)[:800]))
            data = j.get("data") or {}
            rows = (data.get("dataList") or data.get("list") or data.get("rows")
                    or (data if isinstance(data, list) else None) or [])
            if rows:
                ads = []
                for it in rows[:ROWS]:
                    user = it.get("userInfo") or it.get("merchantInfo") or {}
                    pays = (it.get("payMethodList") or it.get("paymethodInfo")
                            or it.get("adPayMethodList") or [])
                    methods = []
                    for p in pays:
                        if isinstance(p, dict):
                            methods.append(p.get("payMethodName") or p.get("paymentName")
                                           or p.get("name") or "")
                        else:
                            methods.append(str(p))
                    ads.append(make_ad(
                        it.get("price") or it.get("advPrice"),
                        it.get("surplusQuantity") or it.get("lastQuantity") or it.get("amount"),
                        it.get("minQuota") or it.get("minAmount") or it.get("minTradeAmount"),
                        it.get("maxQuota") or it.get("maxAmount") or it.get("maxTradeAmount"),
                        methods,
                        it.get("nickName") or user.get("nickName") or user.get("name"),
                        it.get("orderNum") or it.get("turnoverNum") or user.get("totalOrderNum"),
                        it.get("goodRate") or it.get("turnoverRate") or user.get("goodRate")))
                if ads:
                    return ads
            if str(j.get("code")) == "00000":
                raise RuntimeError("交易所未开通此法币市场(在线广告为 0)")
            errors.append("空响应: %s" % snip(j, 140))
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            errors.append("%s" % str(e)[:120])
    raise RuntimeError(" | ".join(errors))


# -------------------------------------------------------------------- HTX ----
HTX_HOSTS = ["https://otc-api.trygofast.com", "https://otc-api.huobi.pro"]
HTX_COIN = {"USDT": 2, "BTC": 1, "ETH": 3}  # HTX OTC 无 USDC 市场
HTX_CURRENCY_STATIC = {"CNY": 172, "USD": 2}  # 实测验证过的 id
HTX_PAY = {"1": "银行卡", "2": "支付宝", "3": "微信", "9": "银行卡"}
HTX_CURRENCY_CACHE = {}


def htx_currency_map():
    """动态拉取 HTX 法币 id 表, 失败则用静态映射。"""
    global HTX_CURRENCY_CACHE
    if HTX_CURRENCY_CACHE:
        return HTX_CURRENCY_CACHE
    for host in HTX_HOSTS:
        for path in ("/v1/data/currencies?language=zh-CN", "/v1/data/currencies",
                     "/v1/data/config/currencies"):
            try:
                j = http_json("GET", host + path, allow_proxy=False, timeout=12)
                rows = j.get("data") or []
                m = {}
                for it in rows:
                    if not isinstance(it, dict):
                        continue
                    code = (it.get("nameShort") or it.get("code") or
                            it.get("currencyCode") or "").upper()
                    cid = it.get("currencyId") or it.get("id")
                    if code and cid:
                        m[code] = int(cid)
                if m:
                    if PROBE:
                        log("PROBE htx currencies via %s%s: %s" % (host, path, snip(m, 300)))
                    HTX_CURRENCY_CACHE = m
                    return m
            except Exception as e:  # noqa: BLE001
                if PROBE:
                    log("PROBE htx currencies %s%s fail: %s" % (host, path, e))
    HTX_CURRENCY_CACHE = dict(HTX_CURRENCY_STATIC)
    return HTX_CURRENCY_CACHE


def fetch_htx(asset, fiat, user_side):
    coin = HTX_COIN.get(asset)
    curr = htx_currency_map().get(fiat) or HTX_CURRENCY_STATIC.get(fiat)
    if not coin or not curr:
        raise RuntimeError("HTX 不支持该交易对(无货币映射)")
    trade_type = "sell" if user_side == "buy" else "buy"  # sell=商家卖出
    errors = []
    for host in HTX_HOSTS:
        try:
            j = http_json("GET", host + "/v1/data/trade-market", params={
                "coinId": coin, "currency": curr, "tradeType": trade_type,
                "currPage": 1, "payMethod": 0, "acceptOrder": 0, "country": "",
                "blockType": "general", "online": 1, "range": 0, "amount": "",
                "onlyTradable": "false", "isFollowed": "false",
            })
            if PROBE:
                log("PROBE htx %s: %s" % (host, json.dumps(j, ensure_ascii=False)[:800]))
            rows = j.get("data") or []
            ads = []
            for it in rows[:ROWS]:
                pays = it.get("payMethods") or []
                methods = [HTX_PAY.get(str(p.get("payMethodId")), p.get("name") or str(p.get("payMethodId")))
                           if isinstance(p, dict) else HTX_PAY.get(str(p), str(p)) for p in pays]
                ads.append(make_ad(
                    it.get("price"), it.get("tradeCount"),
                    it.get("minTradeLimit"), it.get("maxTradeLimit"),
                    methods, it.get("userName") or it.get("nickName"),
                    it.get("tradeMonthTimes"), it.get("orderCompleteRate")))
            if ads:
                return ads
            errors.append("%s: empty" % host)
        except Exception as e:  # noqa: BLE001
            errors.append("%s: %s" % (host, e))
    raise RuntimeError(" | ".join(errors))


FETCHERS = {
    "binance": fetch_binance,
    "okx": fetch_okx,
    "bybit": fetch_bybit,
    "gate": fetch_gate,
    "bitget": fetch_bitget,
    "htx": fetch_htx,
}


# ------------------------------------------------------------ 汇率 / 现货 ----
def fetch_fx():
    for url in ("https://open.er-api.com/v6/latest/USD",
                "https://api.frankfurter.app/latest?from=USD"):
        try:
            j = http_json("GET", url, allow_proxy=False, timeout=15)
            rates = j.get("rates") or {}
            if rates:
                out = {f: fnum(rates.get(f)) for f in FIATS if rates.get(f)}
                out["USD"] = 1.0
                return out
        except Exception as e:  # noqa: BLE001
            log("fx %s fail: %s" % (url, e))
    return {"USD": 1.0}


def fetch_spot():
    spot = {"USDT": 1.0}
    for sym, key in (("BTCUSDT", "BTC"), ("ETHUSDT", "ETH"), ("USDCUSDT", "USDC")):
        for url in ("https://data-api.binance.vision/api/v3/ticker/price?symbol=" + sym,
                    "https://api.bybit.com/v5/market/tickers?category=spot&symbol=" + sym):
            try:
                j = http_json("GET", url, allow_proxy=False, timeout=15)
                if "price" in j:
                    spot[key] = fnum(j["price"])
                    break
                lst = ((j.get("result") or {}).get("list") or [])
                if lst:
                    spot[key] = fnum(lst[0].get("lastPrice"))
                    break
            except Exception as e:  # noqa: BLE001
                log("spot %s fail: %s" % (url, e))
    return spot


# -------------------------------------------------------------- normalize ----
def normalize_sides(buy, sell):
    """保证 buy=用户买入(低价优先), sell=用户卖出(高价优先); 若两侧明显倒挂则交换。"""
    buy = sorted([a for a in buy if a["price"] > 0], key=lambda a: a["price"])
    sell = sorted([a for a in sell if a["price"] > 0], key=lambda a: -a["price"])
    if buy and sell:
        mb = statistics.median(a["price"] for a in buy)
        ms = statistics.median(a["price"] for a in sell)
        if mb < ms * 0.99:  # 买价显著低于卖价 => 标签反了
            buy, sell = ([a for a in sorted(sell, key=lambda x: x["price"])],
                         [a for a in sorted(buy, key=lambda x: -x["price"])])
    return buy, sell


FAIR = {}  # (asset, fiat) -> 公允参考价, 主流程填充


def fetch_market(ex, asset, fiat):
    f = FETCHERS[ex]
    out = {"ok": False, "error": None, "buy": [], "sell": [], "ts": int(time.time())}
    try:
        buy = f(asset, fiat, "buy")
        time.sleep(0.4)
        sell = f(asset, fiat, "sell")
        buy, sell = normalize_sides(buy, sell)
        # 法币映射错误熔断: 买卖两侧同向偏离公允价 >25% 视为脏数据
        fair = FAIR.get((asset, fiat))
        if fair and buy and sell:
            db = buy[0]["price"] / fair - 1
            ds = sell[0]["price"] / fair - 1
            if abs(db) > 0.25 and abs(ds) > 0.25 and db * ds > 0:
                raise RuntimeError("疑似法币/字段映射错误: 价格 %s/%s 偏离公允价 %.4g 超 25%%"
                                   % (buy[0]["price"], sell[0]["price"], fair))
        out["buy"], out["sell"] = buy, sell
        out["ok"] = bool(buy or sell)
        if not out["ok"]:
            out["error"] = "无广告数据(可能不支持该交易对)"
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:300]
        if PROBE:
            traceback.print_exc()
    return out


# ---------------------------------------------------------------- history ----
def update_history(hist_path, latest):
    hist = {"series": {}}
    if os.path.exists(hist_path):
        try:
            with open(hist_path, "r", encoding="utf-8") as fh:
                hist = json.load(fh)
        except Exception:  # noqa: BLE001
            log("history corrupt, restart")
    series = hist.setdefault("series", {})
    ts = latest["updated"]
    now = ts

    def push(key, b1, s1):
        arr = series.setdefault(key, [])
        arr.append([ts, b1, s1])
        # 裁剪: 30 天上限; 48h 以前的降采样到小时
        kept, last_hour = [], None
        for p in arr:
            age = now - p[0]
            if age > HISTORY_MAX_AGE:
                continue
            if age > HISTORY_DENSE_AGE:
                hour = p[0] // 3600
                if hour == last_hour:
                    kept[-1] = p
                    continue
                last_hour = hour
            kept.append(p)
        series[key] = kept

    for fiat, assets in latest["markets"].items():
        for asset, exmap in assets.items():
            for ex, m in exmap.items():
                if not m["ok"]:
                    continue
                b1 = m["buy"][0]["price"] if m["buy"] else None
                s1 = m["sell"][0]["price"] if m["sell"] else None
                push("%s|%s|%s" % (fiat, asset, ex), b1, s1)
    # 汇率序列 (溢价历史计算用)
    for f, v in latest["fx"].items():
        push("fx|%s" % f, v, None)
    # 30 日对照序列: 场外 USDT/CNY 中间价 + 人民银行中间价
    rate = (latest.get("analytics") or {}).get("rate", {}).get("CNY") or {}
    if rate.get("otc_mid"):
        push("mid|CNY|USDT", rate["otc_mid"], None)
    if rate.get("pboc"):
        push("pboc|CNY", rate["pboc"], None)
    with open(hist_path, "w", encoding="utf-8") as fh:
        json.dump(hist, fh, ensure_ascii=False, separators=(",", ":"))
    return hist


# ------------------------------------------------- 人民银行中间价 (CFETS) ----
def fetch_pboc():
    """美元兑人民币中间价 (人民银行授权 CFETS 发布的 central parity)。"""
    parsers = [
        ("https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/fx/ccpr.json",
         _parse_cfets),
        ("https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew?startDate=&endDate=&currency=USD/CNY&pageNum=1&pageSize=1",
         _parse_cfets_his),
    ]
    for url, parser in parsers:
        try:
            j = http_json("GET", url, timeout=18, allow_proxy=True,
                          headers={"Referer": "https://www.chinamoney.com.cn/chinese/bkccpr/"})
            v = parser(j)
            if v and 5 < v < 9:   # 合理区间护栏
                log("pboc USD/CNY = %s" % v)
                return round(v, 4)
            if PROBE:
                log("PROBE pboc %s -> %s" % (url, snip(j, 300)))
        except Exception as e:  # noqa: BLE001
            log("pboc %s fail: %s" % (url[:60], e))
    log("pboc 中间价获取失败, 将仅展示市场汇率")
    return None


def _parse_cfets(j):
    recs = j.get("records") or (j.get("data") or {}).get("records") or []
    for r in recs:
        name = str(r.get("vrtEName") or r.get("foreignCName") or r.get("ccyPair") or "").upper()
        if "USD/CNY" in name or "美元" in str(r.get("foreignCName") or ""):
            return fnum(r.get("price") or r.get("ccprPrice") or r.get("ccpr"))
    return None


def _parse_cfets_his(j):
    recs = (j.get("data") or {}).get("records") or j.get("records") or []
    if recs and isinstance(recs, list):
        r = recs[0]
        return fnum(r.get("values", [None])[0] if r.get("values") else
                    r.get("price") or r.get("ccpr"))
    return None


# ----------------------------------------------------------- 分析/聚合层 ----
CHANNELS = [
    ("支付宝", ("支付宝", "alipay")),
    ("微信", ("微信", "wechat", "wxpay", "weixin")),
    ("QQ钱包", ("qq",)),
    ("银行卡", ("银行", "bank", "card", "transfer", "转账", "对公", "sepa", "swift")),
]
TURN_LOW, TURN_HIGH = 4, 20   # 可见盘口的日换手率估算区间


def classify_channels(methods):
    found = set()
    for m in methods or []:
        ml = str(m).lower()
        for name, keys in CHANNELS:
            if any(k.lower() in ml for k in keys):
                found.add(name)
    return found


def compute_analytics(markets, fx, spot, pboc):
    """从一轮快照派生: 汇率对照 / 成交量估算 / 渠道盘口 / 可疑压价。"""
    channel_rows = {}   # (asset,fiat,channel,side) -> {n,sum,best,min,max}
    channels_disp = {}  # "asset|fiat" -> {channel: {buy:{...}, sell:{...}}}
    suspicious = []

    for fiat, assets in markets.items():
        for asset, exmap in assets.items():
            for side in ("buy", "sell"):
                for ex, m in exmap.items():
                    if not m.get("ok"):
                        continue
                    ads = m.get(side) or []
                    # 渠道聚合
                    for ad in ads:
                        p = ad.get("price") or 0
                        if p <= 0:
                            continue
                        for ch in classify_channels(ad.get("methods")):
                            key = (asset, fiat, ch, side)
                            st = channel_rows.setdefault(
                                key, {"n": 0, "sum": 0.0, "best": p, "min": p, "max": p})
                            st["n"] += 1
                            st["sum"] += p
                            st["min"] = min(st["min"], p)
                            st["max"] = max(st["max"], p)
                            st["best"] = (min(st["best"], p) if side == "buy"
                                          else max(st["best"], p))
                    # 可疑压价 (当前轮, 供前端即时展示)
                    for i, info in _suspect(ads, side).items():
                        ad = ads[i]
                        suspicious.append({
                            "exchange": ex, "asset": asset, "fiat": fiat, "side": side,
                            "merchant": ad.get("merchant"), "price": ad.get("price"),
                            "top2_price": info[2], "below_abs": info[0],
                            "below_pct": info[1], "amount": ad.get("amount"),
                            "orders": ad.get("orders"), "rate": ad.get("rate"),
                        })
    for (asset, fiat, ch, side), st in channel_rows.items():
        d = channels_disp.setdefault("%s|%s" % (asset, fiat), {})
        d.setdefault(ch, {})[side] = {
            "best": round(st["best"], 4), "avg": round(st["sum"] / st["n"], 4),
            "min": round(st["min"], 4), "max": round(st["max"], 4), "n": st["n"],
        }
    suspicious.sort(key=lambda x: -(x["below_pct"] or 0))

    # 汇率对照 (USDT/CNY)
    rate = {}
    cny = markets.get("CNY", {}).get("USDT", {})
    mids = []
    for ex, m in cny.items():
        if not m.get("ok"):
            continue
        b = m["buy"][0]["price"] if m.get("buy") else None
        s = m["sell"][0]["price"] if m.get("sell") else None
        mid = (b + s) / 2 if b and s else (b or s)
        if mid:
            mids.append(mid)
    otc_mid = sum(mids) / len(mids) if mids else None
    market = fx.get("CNY")
    if otc_mid:
        rate["CNY"] = {
            "otc_mid": round(otc_mid, 4), "market": market, "pboc": pboc,
            "premium_market": round((otc_mid / market - 1) * 100, 3) if market else None,
            "premium_pboc": round((otc_mid / pboc - 1) * 100, 3) if pboc else None,
        }

    # 成交量估算 (USDT/CNY, 透明启发式)
    volume = {}
    standing = 0.0
    merchants_set = set()
    monthly = {}
    for ex, m in cny.items():
        if not m.get("ok"):
            continue
        for side in ("buy", "sell"):
            for ad in m.get(side) or []:
                standing += ad.get("amount") or 0
                mer = ad.get("merchant")
                if mer:
                    merchants_set.add((ex, mer))
                    monthly[(ex, mer)] = max(monthly.get((ex, mer), 0),
                                             ad.get("orders") or 0)
    if standing > 0 and otc_mid:
        volume["CNY"] = {
            "standing_liq": round(standing, 1),
            "est_low": round(standing * TURN_LOW),
            "est_high": round(standing * TURN_HIGH),
            "est_low_fiat": round(standing * TURN_LOW * otc_mid),
            "est_high_fiat": round(standing * TURN_HIGH * otc_mid),
            "merchants": len(merchants_set),
            "monthly_orders": sum(monthly.values()),
            "turn_low": TURN_LOW, "turn_high": TURN_HIGH,
        }

    return {"channel_rows": channel_rows, "channels": channels_disp,
            "suspicious": suspicious[:60], "rate": rate, "volume": volume}


def _suspect(ads, side, pct_thr=0.3, min_amount=50):
    """与 db._suspect_indices 同义: 明显低于 Top2 的急售/急购广告。"""
    valid = [(i, a) for i, a in enumerate(ads) if (a.get("price") or 0) > 0]
    if len(valid) < 3:
        return {}
    asc = side == "buy"
    ordered = sorted(valid, key=lambda x: x[1]["price"], reverse=not asc)
    ref = ordered[1][1]["price"]
    out = {}
    for idx, ad in ordered:
        p = ad["price"]
        below_abs = (ref - p) if asc else (p - ref)
        below_pct = below_abs / ref * 100 if ref else 0
        if below_pct >= pct_thr and (ad.get("amount") or 0) >= min_amount:
            out[idx] = (round(below_abs, 4), round(below_pct, 3), round(ref, 6))
    return out


# -------------------------------------------------------------------- main ----
def main():
    global PROBE
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()
    PROBE = args.probe
    os.makedirs(args.out, exist_ok=True)

    t0 = time.time()
    fx = fetch_fx()
    spot = fetch_spot()
    pboc = fetch_pboc()
    log("fx=%s spot=%s pboc=%s" % (fx, spot, pboc))
    for fiat in FIATS:
        for asset in ASSETS_BY_FIAT[fiat]:
            if fx.get(fiat) and spot.get(asset):
                FAIR[(asset, fiat)] = fx[fiat] * spot[asset]

    markets = {}
    ok_count = err_count = 0
    for fiat in FIATS:
        markets[fiat] = {}
        for asset in ASSETS_BY_FIAT[fiat]:
            markets[fiat][asset] = {}
            for ex in EXCHANGES:
                m = fetch_market(ex, asset, fiat)
                markets[fiat][asset][ex] = m
                if m["ok"]:
                    ok_count += 1
                    log("OK  %-7s %s/%s buy1=%s sell1=%s" % (
                        ex, asset, fiat,
                        m["buy"][0]["price"] if m["buy"] else "-",
                        m["sell"][0]["price"] if m["sell"] else "-"))
                else:
                    err_count += 1
                    log("ERR %-7s %s/%s %s" % (ex, asset, fiat, m["error"]))
                time.sleep(0.3)

    now = int(time.time())
    analytics = compute_analytics(markets, fx, spot, pboc)
    latest = {
        "updated": now,
        "updatedISO": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fx": fx, "spot": spot, "pboc": pboc, "markets": markets,
        "analytics": analytics,
        "meta": {"exchanges": EXCHANGES, "fiats": FIATS,
                 "assetsByFiat": ASSETS_BY_FIAT, "rows": ROWS,
                 "okMarkets": ok_count, "errMarkets": err_count,
                 "fetchSeconds": round(time.time() - t0, 1)},
    }

    # 持久化数据库 + 长期投影 (商家档案/可疑事件/渠道日/汇率日/成交量日)
    try:
        conn = dbmod.connect(os.path.join(args.out, "otc.db"))
        dbmod.init(conn)
        dbmod.record(conn, now, markets, analytics)
        dbmod.prune(conn, now)
        dbmod.write_projections(conn, args.out, now)
        latest["meta"]["db"] = dbmod.db_stats(conn)
        conn.close()
        log("db: %s" % latest["meta"]["db"])
    except Exception as e:  # noqa: BLE001
        log("db error: %s" % e)
        if PROBE:
            traceback.print_exc()

    with open(os.path.join(args.out, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(latest, fh, ensure_ascii=False, separators=(",", ":"))
    update_history(os.path.join(args.out, "history.json"), latest)
    log("done: ok=%d err=%d susp=%d in %.1fs" % (
        ok_count, err_count, len(analytics.get("suspicious", [])), time.time() - t0))
    # 至少要有一部分市场成功才算成功
    if ok_count == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
