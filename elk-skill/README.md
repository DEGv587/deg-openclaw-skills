# elk-skill

用于在 ELK / Kibana 中按关键词、字段和时间范围检索日志，帮助定位接口报错、请求链路和运行异常。

## 环境准备

```bash
cd /Users/deg/Documents/my-work/deg-openclaw-skills/elk-skill
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export KIBANA_URL="https://your-kibana.example.com"
```

## 本地调用示例

按关键词查日志：

```bash
python3 elk_query.py '{
  "project": "agentcy",
  "query_string": "generate_slice 报错",
  "start_time": "2026-03-31T00:00:00Z",
  "end_time": "2026-03-31T02:00:00Z"
}'
```

按字段查请求链路：

```bash
python3 elk_query.py '{
  "project": "agentcy",
  "fields": {
    "trace_id": "abc123",
    "service": "agentcy-api"
  },
  "start_time": "2026-03-31T00:00:00Z",
  "end_time": "2026-03-31T02:00:00Z"
}'
```

直接指定索引模式：

```bash
python3 elk_query.py '{
  "index_pattern": "logs-agentcy-*",
  "query_string": "timeout",
  "start_time": "2026-03-31T00:00:00Z",
  "end_time": "2026-03-31T02:00:00Z"
}'
```

## 输出关注点

- `matches`: 项目模糊匹配结果，项目不明确时用于二次确认。
- `logs`: 命中的原始日志结果，适合交给后续分析 skill。
- `query_context`: 本次查询实际使用的项目、索引模式、字段和时间范围。

## 适用场景

- 已知接口、trace_id、request_id，想查完整后端日志链路。
- 已知报错现象，想在 ELK 中搜索相关日志。
- 想补充 Sentry 没有记录到的服务端细节。
