---
name: elk-log-query
description: 用于在 ELK 系统中查询特定接口报错、时间段日志或关键词定位。
metadata:
  openclaw:
    requires:
      bins: ["python3"]
      env: ["KIBANA_URL"]
parameters:
  - name: query_string
    description: 日志搜索关键词或报错表象（如 "xxx接口报错"），用于全文模糊匹配 message 字段
  - name: fields
    description: 指定字段匹配，必须为 JSON 对象格式（如 {"trace_id": "abc123", "service": "order-api"}），各字段使用 match 查询
  - name: start_time
    description: 查询起始时间（ISO 8601 格式，如 2024-01-01T00:00:00Z）
  - name: end_time
    description: 查询结束时间（ISO 8601 格式，如 2024-01-01T01:00:00Z）
  - name: project
    description: 项目名称关键词，从用户描述中提取（如"singapore项目"→"singapore"），系统会自动从 Kibana 索引模式列表中模糊匹配，优先级高于 index_pattern
  - name: index_pattern
    description: ES 索引模式（可选，直接指定时使用，如 logs-order-*），优先级低于 project
---

### Use when
- 用户反馈特定接口在某个时间段出现报错。
- 需要通过 trace_id、request_id 等字段定位某次请求的完整链路日志。
- 需要通过关键词定位系统运行异常。
- 需要查询系统运行日志以定位 Bug。

### Instructions
1. 将用户描述的时间段（如"过去一小时"）转换为标准的 ISO 8601 时间戳。
2. 从用户描述中识别项目名称，提取关键词作为 project 参数（如"agentcy项目"→"agentcy"，"新加坡测试环境"→"测试-singapore"）；若无法识别则不传，使用默认索引。
3. 若用户提供了 traceId、spanId 等具体字段值，将其组织为 JSON 对象传入 fields 参数。
4. 若用户描述的是模糊关键词或报错现象，使用 query_string 参数。
5. fields 和 query_string 可同时使用，查询结果取交集。
6. 若脚本返回 need_clarification: true，将 matches 列表展示给用户，询问要查哪个项目，用户确认后以 index_pattern 参数重新调用。
7. 查询成功后将完整日志数据传递给后续报错分析 skill 处理。
