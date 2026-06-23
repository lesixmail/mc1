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


# 探测目标定义: (分组, 名称, 方法, url, body, params, 用途)
def build_targets():
    adv_body = {"asset": "USDT", "fiat": "CNY", "page": 1, "rows": 5,
                "tradeType": "BUY", "payTypes": [], "publisherType": None,
                "merchantCheck": False}
    agent = "/bapi/c2c/v1/public/c2c/agent"
    return [
        # —— 行情 / 广告 (PUBLIC) ——
        ("行情", "广告搜索 adv/search(主)", "POST", P2P + "/bapi/c2c/v2/friendly/c2c/adv/search",
         adv_body, None, "按 资产/法币/方向/支付方式/金额 分页查询挂单广告。站点核心数据源,字段最全"),
        ("行情", "广告搜索 adv/search(public)", "POST", P2P + "/bapi/c2c/v2/public/c2c/adv/search",
         adv_body, None, "公开版广告搜索(免 referer 限制,字段略少)"),
        ("行情", "官方 Skill 广告 agent/ad-list", "GET", P2P + agent + "/ad-list",
         None, {"fiat": "CNY", "asset": "USDT", "tradeType": "BUY", "limit": 5}, "官方 Skills Hub 文档化的公开广告列表接口"),
        ("行情", "官方 Skill 报价 agent/quote-price", "GET", P2P + agent + "/quote-price",
         None, {"fiat": "CNY", "asset": "USDT", "tradeType": "BUY"}, "官方文档化的参考报价(单一价格)"),
        # —— 配置 / 元数据 (PUBLIC) ——
        ("配置", "筛选条件 filter-conditions", "POST", P2P + "/bapi/c2c/v2/public/c2c/adv/filter-conditions",
         {"fiat": "CNY"}, None, "给定法币下:可选资产、支付方式、交易区、周期、金额档等筛选项全集"),
        ("配置", "官方 Skill 支付方式 agent/trade-methods", "GET", P2P + agent + "/trade-methods",
         None, {"fiat": "CNY"}, "某法币支持的全部支付方式(官方文档化)"),
        ("配置", "支持法币 trade-rule/fiat-list", "POST", P2P + "/bapi/c2c/v1/friendly/c2c/trade-rule/fiat-list",
         {}, None, "C2C 支持的全部法币集合"),
        ("配置", "门户配置 portal/config", "POST", P2P + "/bapi/c2c/v2/friendly/c2c/portal/config",
         {"fiat": "CNY"}, None, "P2P 门户全局配置(功能开关/默认值)"),
        ("配置", "Skill 版本 agent/check-version", "GET", P2P + agent + "/check-version",
         None, None, "官方 Skill 版本校验(连通性探针)"),
        # —— 商家 / 用户 (多数需登录) ——
        ("商家", "商家广告 getUserAdsByUserNo", "POST", P2P + "/bapi/c2c/v2/friendly/c2c/adv/getUserAdsByUserNo",
         {"userNo": "", "page": 1, "rows": 5}, None, "指定商家的全部在售广告(需 userNo,可用 adv/search 反查)"),
        ("商家", "商家档案 user-profile", "GET", P2P + "/bapi/c2c/v2/friendly/c2c/user-center/user-profile",
         None, None, "广告主公开档案(注册时长/认证/成交/好评等,通常需登录)"),
        ("商家", "免责声明状态 is-agreement", "GET", P2P + "/bapi/c2c/v1/friendly/c2c/user/is-agreement-disclaimer-required",
         None, None, "前端 bundle 引用的用户态接口(探可达性/鉴权)"),
        # —— 现货参考价 / 签名 SAPI(对照) ——
        ("现货", "现货参考价 get-products", "GET", WWW + "/bapi/asset/v2/public/asset-service/product/get-products",
         None, None, "资产现货参考价(P2P 溢价计算用,www 站点,常受风控)"),
        ("签名", "SAPI 参考价 getReferencePrice", "POST", "https://api.binance.com/sapi/v1/c2c/ads/getReferencePrice",
         {}, None, "官方签名接口:P2P 现货参考价(需 API Key+签名,此处仅证其存在/鉴权)"),
        ("签名", "SAPI 广告搜索 ads/search", "POST", "https://api.binance.com/sapi/v1/c2c/ads/search",
         {}, None, "官方签名接口:广告搜索(需 API Key+签名)"),
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
        if res["http"] in (401, 403, 451) or "login" in blob or "signature" in blob \
           or "api-key" in blob or "apikey" in blob or "unauthor" in blob \
           or str(code) in ("100001003", "100002001", "-1022", "-2014", "-1002"):
            auth = True
        # SAPI 签名接口: 非 200 一律视为"需签名"(其本质就是要 API Key)
        if grp == "签名" and not biz_ok:
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
            # 从主广告接口抽取"币安字段全字典"(adv / advertiser / tradeMethods)
            if "adv/search(主)" in name and isinstance(data, list) and data:
                it = data[0]
                extracted["advFields"] = sorted((it.get("adv") or {}).keys())
                extracted["advertiserFields"] = sorted((it.get("advertiser") or {}).keys())
                tms = (it.get("adv") or {}).get("tradeMethods") or []
                if tms:
                    extracted["tradeMethodFields"] = sorted(tms[0].keys())
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


def _names(arr, *keys):
    out = []
    for x in arr or []:
        if isinstance(x, dict):
            for k in keys:
                if x.get(k):
                    out.append(x[k])
                    break
        elif x:
            out.append(x)
    return out


def _extract(name, data, store):
    """从可用接口里抽取展示用元数据(法币/资产/支付方式全集等)。"""
    try:
        if not data:
            return
        if "filter-conditions" in name and isinstance(data, dict):
            if data.get("areas"):
                store["areas"] = _names(data["areas"], "name", "area")[:80]
            pm = data.get("tradeMethods") or data.get("payTypes")
            if pm:
                store["payMethods"] = _names(pm, "tradeMethodName", "identifier", "payType")[:120]
            if data.get("assets"):
                store["assets"] = _names(data["assets"], "asset")[:80] or data["assets"][:80]
            if data.get("fiatList") or data.get("fiats"):
                store["fiats"] = _names(data.get("fiatList") or data.get("fiats"), "fiat", "currencyCode")[:120]
            if data.get("periods"):
                store["periods"] = data["periods"][:20]
        if "trade-methods" in name:
            arr = data if isinstance(data, list) else (data.get("list") if isinstance(data, dict) else None)
            if arr:
                store["payMethods"] = _names(arr, "tradeMethodName", "identifier", "payType", "name")[:120]
        if "fiat-list" in name:
            arr = data if isinstance(data, list) else (data.get("list") if isinstance(data, dict) else None)
            if arr:
                store["fiats"] = _names(arr, "fiat", "currencyCode", "currencyName")[:140]
        if "quote-price" in name and isinstance(data, (dict, list)):
            store["quotePrice"] = data
        if "ad-list" in name and isinstance(data, list) and data:
            store["agentAdSample"] = data[0]
        if "现货" in name and isinstance(data, list):
            store["spotSample"] = [{"s": p.get("s"), "c": p.get("c")} for p in data[:5]]
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
