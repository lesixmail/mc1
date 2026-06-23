#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
币安 OTC(P2P / C2C)接口全景探测器。

挨个请求币安 P2P 的公开 web 接口(bapi/c2c/...),记录:
  - 是否可达(HTTP / 业务 code)
  - 是否需要登录鉴权
  - 返回的关键字段 / 数据量
输出 binance_api.json,供前端"币安接口全景"板块展示;
并顺带导出实测能拿到的元数据(支持的法币 / 资产 / 支付方式全集、价格统计等)。

用法: python3 probe_binance.py --out DIR
单跑约 15~20 个请求,几秒完成。
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone

import requests

P2P = "https://p2p.binance.com"
WWW = "https://www.binance.com"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "content-type": "application/json", "clienttype": "web",
    "Origin": P2P, "Referer": P2P + "/zh-CN/trade/all-payments/USDT",
}


def call(method, url, body=None, params=None, timeout=18):
    t0 = time.time()
    try:
        r = requests.request(method, url, json=body, params=params,
                             headers=HEADERS, timeout=timeout)
        ms = round((time.time() - t0) * 1000)
        try:
            j = r.json()
        except ValueError:
            return {"http": r.status_code, "ms": ms, "ok": False,
                    "note": "非 JSON 响应", "raw": r.text[:160], "json": None}
        return {"http": r.status_code, "ms": ms, "json": j,
                "ok": r.status_code == 200, "note": ""}
    except Exception as e:  # noqa: BLE001
        return {"http": 0, "ms": round((time.time() - t0) * 1000), "ok": False,
                "json": None, "note": "%s: %s" % (type(e).__name__, str(e)[:120])}


def keys_of(j, limit=40):
    """提取响应里有代表性的字段名(便于前端展示"该接口能给什么")。"""
    out = []

    def walk(o, prefix, depth):
        if depth > 3 or len(out) > limit:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                p = (prefix + "." + k) if prefix else k
                if isinstance(v, (dict, list)):
                    walk(v, p, depth + 1)
                else:
                    if p not in out:
                        out.append(p)
        elif isinstance(o, list) and o:
            walk(o[0], prefix + "[]", depth + 1)
    walk(j, "", 0)
    return out[:limit]


def biz_code(j):
    if not isinstance(j, dict):
        return None, None
    return j.get("code"), j.get("message") or j.get("msg")


# 探测目标定义: (分组, 名称, 方法, url, body, params, 用途, 备注解析器)
def build_targets():
    adv_body = {"asset": "USDT", "fiat": "CNY", "page": 1, "rows": 5,
                "tradeType": "BUY", "payTypes": [], "publisherType": None,
                "merchantCheck": False}
    return [
        ("行情", "广告搜索(主)", "POST", P2P + "/bapi/c2c/v2/friendly/c2c/adv/search",
         adv_body, None, "按 资产/法币/方向/支付方式 分页查询挂单广告(站点核心数据源)"),
        ("行情", "广告搜索(public)", "POST", P2P + "/bapi/c2c/v2/public/c2c/adv/search",
         adv_body, None, "公开版广告搜索(部分区域可用)"),
        ("行情", "价格统计 price-stats", "POST", P2P + "/bapi/c2c/v1/friendly/c2c/order-match/price-stats",
         {"asset": "USDT", "fiat": "CNY", "rows": 20}, None, "买/卖方向的均价、最优价等统计"),
        ("行情", "报价 quotedPrice", "POST", P2P + "/bapi/c2c/v2/friendly/c2c/adv/quoted-price",
         {"assets": ["USDT"], "fiat": "CNY", "rows": 1, "fromUserRole": "USER"}, None,
         "按法币给出资产的参考报价"),
        ("配置", "门户配置 portal/config", "GET", P2P + "/bapi/c2c/v2/friendly/c2c/portal/config",
         None, None, "C2C 门户全局配置"),
        ("配置", "筛选条件 filter-conditions", "POST", P2P + "/bapi/c2c/v2/friendly/c2c/adv/filter-conditions",
         {"fiat": "CNY"}, None, "给定法币下:可选资产、支付方式、交易区、周期等全集"),
        ("配置", "地区列表 areas", "GET", P2P + "/bapi/c2c/v2/friendly/c2c/areas",
         None, None, "支持的国家/地区列表"),
        ("配置", "支付方式 all-payments", "POST", P2P + "/bapi/c2c/v2/friendly/c2c/pay-method/list",
         {"fiat": "CNY"}, None, "法币对应的全部支付方式"),
        ("配置", "支持法币列表", "GET", P2P + "/bapi/c2c/v2/friendly/c2c/trade-rule/fiat-list",
         None, None, "C2C 支持的法币集合"),
        ("配置", "交易规则 trade-rule", "POST", P2P + "/bapi/c2c/v2/friendly/c2c/trade-rule/queryByTradeType",
         {"tradeType": "BUY"}, None, "下单交易规则/限制"),
        ("商家", "商家档案 user-profile", "GET", P2P + "/bapi/c2c/v2/friendly/c2c/user-center/user-profile",
         None, None, "广告主/商家公开档案(需 userNo 参数,先探可达性)"),
        ("商家", "商家广告 getUserAdsByUserNo", "POST", P2P + "/bapi/c2c/v2/friendly/c2c/adv/getUserAdsByUserNo",
         {"userNo": "", "page": 1, "rows": 5}, None, "指定商家的全部在售广告"),
        ("商家", "商家统计 user-statistics", "POST", P2P + "/bapi/c2c/v2/friendly/c2c/user-center/user/user-statistics",
         {"userNo": ""}, None, "商家成交量/成单率/好评率等统计"),
        ("现货", "P2P 现货参考价", "POST", WWW + "/bapi/asset/v2/public/asset-service/product/get-products",
         {"includeEtf": False}, None, "资产现货参考价(P2P 溢价计算用)"),
        ("行情", "按法币聚合 search-fiat", "POST", P2P + "/bapi/c2c/v2/friendly/c2c/adv/search-fiat",
         {"fiat": "CNY", "rows": 5}, None, "按法币维度聚合的广告(变体)"),
    ]


def run(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    endpoints = []
    extracted = {}   # 实测拿到的有用元数据

    for grp, name, method, url, body, params, purpose in build_targets():
        res = call(method, url, body, params)
        code, msg = biz_code(res.get("json"))
        # 业务层判定: code=="000000" 视为成功; 非 200 或 code 异常视为受限
        biz_ok = (res["ok"] and (code in (None, "000000", "0", 0)))
        # 鉴权判定
        auth = False
        blob = json.dumps(res.get("json") or {}, ensure_ascii=False).lower()
        if res["http"] in (401, 403) or "login" in blob or "auth" in blob or \
           code in ("100001003", "100002001") or "unauthor" in blob:
            auth = True
        path = url.replace(P2P, "").replace(WWW, "")
        rec = {
            "group": grp, "name": name, "method": method, "path": path,
            "purpose": purpose, "http": res["http"], "ms": res["ms"],
            "bizCode": str(code) if code is not None else None,
            "message": (msg or "")[:80],
            "status": ("ok" if biz_ok else ("auth" if auth else "fail")),
            "note": res["note"], "fields": [],
        }
        if biz_ok and res.get("json") is not None:
            data = res["json"].get("data") if isinstance(res["json"], dict) else res["json"]
            rec["fields"] = keys_of({"data": data} if data is not None else res["json"])
            rec["dataLen"] = (len(data) if isinstance(data, (list, dict)) else None)
            _extract(name, data, extracted)
        endpoints.append(rec)
        print("[probe] %-8s %-22s %s http=%s code=%s -> %s (%sms)" % (
            grp, name, path[:48], res["http"], code, rec["status"], res["ms"]), flush=True)
        time.sleep(0.4)

    ok_n = sum(1 for e in endpoints if e["status"] == "ok")
    out = {
        "updated": int(time.time()),
        "updatedISO": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {"total": len(endpoints), "ok": ok_n,
                    "auth": sum(1 for e in endpoints if e["status"] == "auth"),
                    "fail": sum(1 for e in endpoints if e["status"] == "fail")},
        "endpoints": endpoints,
        "extracted": extracted,
    }
    with open(os.path.join(out_dir, "binance_api.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print("[probe] done: %d/%d 接口可用; 元数据键: %s" % (
        ok_n, len(endpoints), list(extracted.keys())))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".")
    args = ap.parse_args()
    run(args.out)


def _extract(name, data, store):
    """从可用接口里抽取展示用元数据(法币/资产/支付方式全集等)。"""
    try:
        if not data:
            return
        if "筛选条件" in name and isinstance(data, dict):
            if data.get("areas"):
                store["areas"] = [a.get("name") or a.get("area") for a in data["areas"]][:60]
            if data.get("tradeMethods") or data.get("payTypes"):
                pm = data.get("tradeMethods") or data.get("payTypes")
                store["payMethods"] = [p.get("tradeMethodName") or p.get("identifier") or p.get("payType")
                                       for p in pm][:80]
            if data.get("assets"):
                store["assets"] = [a.get("asset") or a for a in data["assets"]][:60]
            if data.get("periods"):
                store["periods"] = data["periods"][:20]
        if "支付方式" in name and isinstance(data, list):
            store["payMethods"] = [p.get("tradeMethodName") or p.get("identifier") for p in data][:80]
        if "法币" in name:
            arr = data if isinstance(data, list) else data.get("list") if isinstance(data, dict) else None
            if arr:
                store["fiats"] = [f.get("fiat") or f.get("currencyCode") or f for f in arr][:80]
        if "价格统计" in name and isinstance(data, (dict, list)):
            store["priceStats"] = data
        if "现货" in name and isinstance(data, list):
            store["spotSample"] = [{"s": p.get("s"), "c": p.get("c")} for p in data[:5]]
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
