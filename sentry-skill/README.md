# sentry-skill

用于根据项目、时间范围和页面路由在 Sentry 中定位相关 issue/event，并额外提取可能失败的接口请求信息。

## 环境准备

```bash
cd /Users/deg/Documents/my-work/deg-openclaw-skills/sentry-skill
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SENTRY_BASE_URL="https://sentry.io"
export SENTRY_ORG="your-organization-slug"
export SENTRY_AUTH_TOKEN="your-token-here"
```

## 本地调用示例

```bash
python3 sentry_query.py '{
  "project": "web-console",
  "url": "https://app.example.com/order/confirm?id=123",
  "route": "/order/confirm",
  "problem_description": "页面打开后白屏，点击提交没有反应",
  "start_time": "2026-03-31T00:00:00Z",
  "end_time": "2026-03-31T02:00:00Z",
  "environment": "production",
  "limit": 3
}'
```

如果你已经知道 Sentry 原生搜索条件，也可以直接透传：

```bash
python3 sentry_query.py '{
  "project": "agentcy-web",
  "sentry_query": "page_search:\"?themeId=2034940229874614272\" OR url:*2034939715480977408*",
  "start_time": "2026-03-29T00:00:00Z",
  "end_time": "2026-03-31T23:59:59Z",
  "limit": 5
}'
```

如果项目名不确定，可以先传模糊值：

```bash
python3 sentry_query.py '{
  "project": "console",
  "route": "/order/confirm",
  "start_time": "2026-03-31T00:00:00Z",
  "end_time": "2026-03-31T02:00:00Z"
}'
```

如果返回 `need_clarification: true`，从 `matches` 里选一个更具体的项目 slug 再重试。

## 输出关注点

- `query_context`: 本次查询实际使用的 URL、route、问题描述、拆解出的检索线索和查询尝试。
- `candidates`: 候选 issue 列表，按相关性排序。
- `recommended_event`: 最推荐阅读的 event 摘要。
- `analysis_hints`: 给大模型直接消费的高价值摘要字段。
- `related_request_errors`: 从 event breadcrumbs/request 中提取出的接口报错线索。
- `error_data_requests`: 若事件里有自定义 `errorData`，会提取接口参数、业务码和响应信息。

## 联调优化建议

- 如果 route 不稳定，优先传页面 path，不要带域名和无关 query 参数。
- 如果你已经能在 Sentry 界面里用原生查询查到结果，优先把那段查询直接作为 `sentry_query` 传入。
- 只要传了 `url` 或 `sentry_query`，脚本会优先走 event-first 查询，不再回退到 issue-first。
- 若线上和测试环境都会上报，尽量总是传 `environment`。
- 如果页面路由和接口路由差异很大，后续可以追加 `api_hint` 参数做更准的接口筛选。
- 如果你们的 Sentry breadcrumb 里字段名不统一，可以根据实际数据补充 `extract_related_request_errors` 里的取值字段。
