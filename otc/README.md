# 📡 OTC 雷达 — 六大交易所场外市场全景监控

实时监控 **Binance / OKX / Bybit / Gate / Bitget / HTX** 六大交易所的 P2P/OTC 市场。

**在线访问: https://lesixmail.github.io/mc1/**

## 功能(对标 P2P.Army、AICoin/ChaiNext 场外指数、BestChange 等行业工具)

- **场外折溢价指数**: 全市场中间价 ÷ (官方汇率 × 现货指数) × 100,>100 溢价(入金需求强)、<100 折价(出金需求强)
- **六所盘口对比**: 各所买一/卖一、买卖价差、对公允价的溢价、挂单深度合计、24h 迷你走势
- **跨所搬砖套利监测**: 自动计算"低价所买入 → 高价所卖出"的毛价差与毛利率排行
- **合并市场深度图**: 各所前 15 档挂单的累计深度曲线
- **历史走势**: 价格 / 溢价% / 买卖价差三种模式,24h / 7天 / 30天 区间(5 分钟粒度, 48h 后按小时降采样, 保留 30 天)
- **商家挂单簿**: 商家昵称、价格、可交易量、单笔限额、支付方式(支付宝/微信/银行卡标签)、成单数、成单率;支持按交易所、支付方式、金额筛选
- **价格预警**: 溢价指数 / 买一 / 卖一 / 套利毛利率阈值,浏览器通知 + 提示音
- **多法币多资产**: CNY(USDT/BTC/ETH/USDC) + USD/EUR/HKD(USDT)
- **数据源健康面板**: 每个交易所接口的实时可用状态与错误原因

## 架构

```
GitHub Actions (otc-monitor.yml, 自续期循环)
  └─ otc/scripts/fetch_otc.py   每 5 分钟抓取 6 所 P2P 接口 + 汇率 + 现货
       ├─ 推送 latest.json / history.json → otc-data 分支 (raw.githubusercontent.com 提供 CORS 数据)
       └─ 首轮发布 otc/site → gh-pages 分支 → GitHub Pages 站点
前端 (纯静态, ECharts) 每 60 秒拉取 otc-data 分支最新数据
```

- **运行模型**: 每次 Actions 运行内部循环 11 轮 × 5 分钟,结束时自动重新派发自己,实现 7×24 持续采集;
  `schedule` cron 为看门狗(合并到默认分支后生效),链条意外中断时自动拉起。
- **停止采集**: 把 `otc/AUTORUN` 内容改为 `off`(或在 Actions 页取消运行)。
- **容错**: 每所接口独立 try/catch、候选端点回退、公共代理回退(应对地区封锁)、买卖方向自动校正、历史数据跨运行持续累积。

## 手动操作

```bash
# 手动触发一次采集 (Actions 页面 Run workflow, 或:)
gh workflow run otc-monitor.yml --ref <branch> -f cycles=11

# 本地运行抓取器
pip install requests
python3 otc/scripts/fetch_otc.py --out /tmp/data --probe

# 本地预览前端
cd otc/site && python3 -m http.server 8000
```

## 数据格式

`latest.json`: `{updated, fx:{CNY:…}, spot:{BTC:…}, markets:{CNY:{USDT:{binance:{ok,buy:[{price,amount,min,max,methods,merchant,orders,rate}],sell:[…]}}}}}`

`history.json`: `{series:{"CNY|USDT|okx":[[ts,买一,卖一],…], "fx|CNY":[[ts,汇率,null],…]}}`

## 已知限制

- Binance P2P 已于 2021 年下架 CNY 交易对,故 CNY 视图下币安显示"不支持";USD/EUR 等正常。
- 部分交易所接口对数据中心 IP 有地区限制,采集器内置了候选域名与公共代理回退,个别交易所仍可能间歇不可用(状态面板会如实显示)。
- 套利监测为毛价差,未计入手续费、提币费与转账时间成本。

⚠️ 本项目仅作行情聚合展示,不构成投资建议;场外交易请自行评估对手方与合规风险。
