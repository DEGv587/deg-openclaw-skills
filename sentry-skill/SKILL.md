---
name: sentry-issue-investigation
description: 用于根据项目、时间范围和页面路由在 Sentry 中定位相关 issue/event，并提取页面异常和关联接口报错上下文。
metadata:
  openclaw:
    requires:
      bins: ["python3"]
      env: ["SENTRY_AUTH_TOKEN", "SENTRY_ORG"]
parameters:
  - name: project
    description: 项目名称或 slug，可模糊传入（如 "web-console" 或 "console"）
  - name: url
    description: 完整页面 URL，可选；脚本会自动拆出 path、query 和关键 ID 作为检索线索
  - name: route
    description: 页面路由或页面 URL 片段（如 "/order/confirm"）
  - name: sentry_query
    description: 可选，直接传入 Sentry 原生搜索语法，如 page_search:"?themeId=xxx" 或 url:*123*
  - name: problem_description
    description: 用户描述的问题现象，可选；用于补充检索线索与结果披露上下文
  - name: start_time
    description: 查询起始时间（ISO 8601 格式，如 2026-03-31T00:00:00Z）
  - name: end_time
    description: 查询结束时间（ISO 8601 格式，如 2026-03-31T02:00:00Z）
  - name: environment
    description: 环境名称，可选（如 production、staging）
  - name: limit
    description: 返回的候选 issue 数量，默认 3
  - name: organization
    description: Sentry organization slug，可选；不传时使用环境变量 SENTRY_ORG
---

### Use when
- 用户反馈某个页面在某个时间段出现报错，需要在 Sentry 中快速定位。
- 已知项目、大概时间和页面路由，需要查找最相关的 issue/event。
- 已知页面 URL、时间和问题描述，需要尽量披露相关 Sentry 证据给后续 skill。
- 需要确认页面报错是否由某个接口请求失败引起。

### Instructions
1. 将用户描述的时间范围转换为标准 ISO 8601 时间戳；如果只有大概时间，请合理扩成一个较小窗口。
2. 从用户描述中提取项目名称或 slug，作为 `project` 参数；若无法确定，可先不传。
3. 如果用户提供了完整 URL，优先传 `url`；脚本会自动拆出 path、query 参数和关键 ID。
4. 如果你已经有明确的 Sentry 搜索条件，优先传 `sentry_query`；否则将页面路由、URL 路径或页面关键路径作为 `route` 参数传入。
5. 可将用户原始问题描述作为 `problem_description` 传入，用于补充检索线索和结果披露上下文。
6. 若用户明确提到环境，如线上、预发、测试，请传入 `environment`。
7. 若脚本返回 `need_clarification: true`，先把 `matches` 展示给用户，让用户确认具体项目后重试。
8. 脚本返回后，优先阅读 `recommended_event`、`candidates`、`analysis_hints` 和 `related_request_errors`：
   - `recommended_event` 用于理解页面异常本身。
   - `candidates` 用于向后续 skill 披露相关 issue/event 证据。
   - `analysis_hints` 用于快速确认最可能的报错接口。
   - `related_request_errors` 用于补充接口请求上下文。
9. 如果存在接口相关线索，优先向用户说明：
   - 哪个接口可能报错
   - 请求方法、URL、状态码或业务错误码
   - 相关异常或响应摘要
10. 只要传了 `url` 或 `sentry_query`，优先走 event-first 查询，不再回退到 issue-first。
11. 如果没有明确命中错误，也要把已尝试的查询线索、已返回的事件证据和查询上下文返回给后续 skill，不要自行做最终根因判断。
