# 📡 OTC 雷达 — 六大交易所场外市场全景监控

实时监控 **Binance / OKX / Bybit / Gate / Bitget / HTX** 六大交易所的 P2P/OTC 市场。

## 在线访问

- **即时公网链接(无需任何配置)**: https://raw.githack.com/lesixmail/mc1/gh-pages/index.html
- **正式链接(推荐)**: https://lesixmail.github.io/mc1/ — 首次需要仓库管理员一次性开通:
  打开 `Settings → Pages → Build and deployment → Source` 选择 **GitHub Actions** 即可,
  之后工作流会自动持续部署(每 30 分钟的 main 调度运行会执行 pages job)。
  也可选择 "Deploy from a branch" + `gh-pages` 分支,效果相同。
  (Pages 站点的"创建"动作 GitHub 限定管理员权限,Actions 的 GITHUB_TOKEN 无法代办,故需手动点一次。)

## 功能(对标 P2P.Army、AICoin/ChaiNext 场外指数、BestChange 等行业工具)

优先展示 **USDT / 人民币**。

- **人民银行中间价对照**: 抓取 CFETS 授权发布的美元兑人民币中间价,与场外 USDT/CNY 实时对比,计算相对人行/市场汇率的溢价
- **每日成交量估算**: 基于六所可见挂单深度 × 日换手率区间(4–20×)的粗略数量级估算(下限级,透明披露方法)
- **压价急售商家监测**: 自动识别报价明显低于本平台 Top2 的"着急出货/急购"商家(可能涉赃款冻卡风险),长期记录其价格、成交量、低于 Top2 的幅度;提供实时榜/长期高频榜/历史事件三视图
- **币安全字段商家大表**: 把币安 P2P API 提供的全部字段(类型/好评率/累计成交/VIP/等级/注册天数/活跃/保证金/付款时限/最小数量/买家限制/定价方式/广告号…)做成**可排序、可逐列筛选**的大表;其他平台对照同列排序
- **支付渠道盘口统计**: 微信 / 支付宝 / 银行卡 / QQ钱包 等渠道当日盘口价、平均价、最低价(买卖双向)
- **30 日走势**: 场外 USDT/CNY 价 vs 人民银行中间价双线对照 + 溢价曲线
- **跨所盘口对比 / 搬砖套利监测 / 合并深度图**
- **价格预警**: 相对人行/市场溢价、场外中间价、套利毛利率、实时压价商家数阈值,浏览器通知 + 提示音
- **商家数据库**: 全部商家持久化进 SQLite(`otc.db`),跨运行累积;数据源健康面板

## 数据库(常态化监测)

采集端把每轮快照写入 SQLite **系统记录库** `otc.db`(随 `otc-data` 分支携带,跨 CI 运行持续累积):

| 表 | 内容 | 保留 |
|---|---|---|
| `merchants` | 所有商家档案(首末见、出现/压价次数) | 长期 |
| `merchant_stats` | 商家在各交易对/方向的滚动价格、成交量、压价统计 | 长期 |
| `observations` | 原始盘口前 8 档 | 72 小时 |
| `suspicious_events` | 压价急售事件日志 | 60 天 |
| `channel_daily` | 每日各支付渠道盘口/均价/最低 | 120 天 |
| `rate_daily` | 每日 场外 vs 人行 vs 市场 汇率与溢价 | 120 天 |
| `volume_daily` | 每日成交量估算 | 120 天 |

前端不直接读 SQLite,而是消费采集端导出的紧凑 JSON 投影(`merchants/suspicious/channels/series.json`);完整数据库可在站点"数据库"区下载 `otc.db`。

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

## 已知限制(2026-06 实测)

- **Gate**: 全站(含 gate.com/gate.io 双域名)被 Akamai 风控拦截数据中心流量,接口返回 403,
  状态面板如实显示"风控限制"。如有自建代理可在 `fetch_otc.py` 的 `http_json` 中接入。
- **Bybit / Bitget 的 CNY**: 接口正常但在线广告数为 0(两所已不开 CNY P2P 市场),HKD 同理(Bitget)。
  CNY 实际有盘口的是 Binance / OKX / HTX 三所。
- **HTX**: 无 USDC 市场;法币 id 通过接口动态发现,发现不了的法币按"未开通"处理。
- 采集器带"公允价偏离 25% 熔断",自动丢弃疑似映射错误的脏数据。
- 套利监测为毛价差,未计入手续费、提币费与转账时间成本。

⚠️ 本项目仅作行情聚合展示,不构成投资建议;场外交易请自行评估对手方与合规风险。
