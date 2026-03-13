import sys
import json
import os
import requests
from pathlib import Path
from elasticsearch import Elasticsearch

# Windows 终端 utf-8 输出
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def load_config():
    config_path = Path(__file__).parent / "config.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def fetch_index_patterns(kibana_url, api_key):
    """从 Kibana 分页拉取全量索引模式列表，返回 [{"id": ..., "title": ...}]"""
    headers = {"kbn-xsrf": "true"}
    if api_key:
        headers["Authorization"] = f"ApiKey {api_key}"

    all_patterns = []
    page = 1
    per_page = 200
    while True:
        resp = requests.get(
            f"{kibana_url}/api/saved_objects/_find",
            params={"type": "index-pattern", "per_page": per_page, "page": page},
            headers=headers,
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        saved_objects = data.get("saved_objects", [])
        all_patterns.extend(
            {"id": obj["id"], "title": obj["attributes"]["title"]}
            for obj in saved_objects
        )
        if len(all_patterns) >= data.get("total", 0):
            break
        page += 1
    return all_patterns


def resolve_index_pattern(project, kibana_url, api_key, default):
    """
    用 project 关键词模糊匹配 Kibana 索引模式 title。
    返回 (matched_list, fallback)：
      - matched_list: 所有匹配的索引模式名列表（可能为空）
      - fallback: 无匹配时使用的默认索引
    """
    try:
        patterns = fetch_index_patterns(kibana_url, api_key)
        keyword = project.lower()
        matched = [p["title"] for p in patterns if keyword in p["title"].lower()]
        return matched, default
    except Exception:
        return [], default


def build_query(query_string, fields, start_time, end_time, max_hits):
    """
    对齐 Kibana 真实请求格式：
    - query_string / fields 的值均走 multi_match phrase 全字段匹配
    - 兼容不同索引的字段命名差异（如 traceId vs json.traceId）
    - _source: false，通过 fields 返回所有字段
    - 按 @timestamp 倒序
    """
    filter_clauses = []

    # query_string：跨所有字段的短语匹配
    if query_string:
        filter_clauses.append({
            "multi_match": {
                "type": "phrase",
                "query": query_string,
                "lenient": True
            }
        })

    # fields：每个值独立做 multi_match phrase，兼容任意字段命名前缀
    if fields:
        for value in fields.values():
            filter_clauses.append({
                "multi_match": {
                    "type": "phrase",
                    "query": value,
                    "lenient": True
                }
            })

    # 时间范围
    filter_clauses.append({
        "range": {
            "@timestamp": {
                "gte": start_time,
                "lte": end_time,
                "format": "strict_date_optional_time"
            }
        }
    })

    return {
        "track_total_hits": True,
        "version": True,
        "size": max_hits,
        "_source": False,
        "fields": [
            {"field": "*", "include_unmapped": "true"},
            {"field": "@timestamp", "format": "strict_date_optional_time"}
        ],
        "sort": [
            {"@timestamp": {"order": "desc", "unmapped_type": "boolean"}}
        ],
        "stored_fields": ["*"],
        "runtime_mappings": {},
        "script_fields": {},
        "query": {
            "bool": {
                "must": [],
                "filter": filter_clauses,
                "should": [],
                "must_not": []
            }
        }
    }


def search_via_kibana(kibana_url, api_key, index_pattern, search_query):
    """通过 Kibana console proxy 转发查询"""
    headers = {"kbn-xsrf": "true", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"ApiKey {api_key}"
    resp = requests.post(
        f"{kibana_url}/api/console/proxy",
        params={"path": f"{index_pattern}/_search", "method": "POST"},
        headers=headers,
        json=search_query,
        timeout=15
    )
    resp.raise_for_status()
    return resp.json()["hits"]["hits"]


def search_via_es(es_url, api_key, index_pattern, search_query):
    """直连 ES 查询"""
    es_kwargs = {"hosts": [es_url]}
    if api_key:
        es_kwargs["api_key"] = api_key
    es = Elasticsearch(**es_kwargs)
    res = es.search(index=index_pattern, body=search_query)
    return res["hits"]["hits"]


def run():
    es_url = os.getenv("ELASTICSEARCH_URL", "")
    kibana_url = os.getenv("KIBANA_URL", "")
    api_key = os.getenv("ELK_API_KEY", "")

    config = load_config()

    try:
        args = json.loads(sys.argv[1])
        query_string = args.get("query_string")
        fields = args.get("fields")
        start_time = args.get("start_time")
        end_time = args.get("end_time")
        project = args.get("project")
        default_index = config.get("default_index_pattern", "logs-*")
        max_hits = config.get("max_hits", 100)

        if not query_string and not fields:
            raise ValueError("query_string 和 fields 不能同时为空")

        # 索引解析：project 模糊匹配 > index_pattern 直接指定 > 默认
        if project and kibana_url:
            matched, fallback = resolve_index_pattern(project, kibana_url, api_key, default_index)
            if len(matched) > 1:
                # 多个匹配，返回列表让 LLM 询问用户确认
                print(json.dumps({
                    "success": False,
                    "need_clarification": True,
                    "message": f"关键词 '{project}' 匹配到 {len(matched)} 个索引模式，请告知用户选择具体项目后，通过 index_pattern 参数重新查询",
                    "matches": matched
                }, ensure_ascii=False))
                return
            index_pattern = matched[0] if matched else fallback
        else:
            index_pattern = args.get("index_pattern") or default_index

        search_query = build_query(query_string, fields, start_time, end_time, max_hits)

        # 查询：ES 直连 > Kibana 代理
        hits = None
        mode = ""
        if es_url:
            try:
                hits = search_via_es(es_url, api_key, index_pattern, search_query)
                mode = "ES直连"
            except Exception:
                pass

        if hits is None and kibana_url:
            hits = search_via_kibana(kibana_url, api_key, index_pattern, search_query)
            mode = "Kibana代理"

        if hits is None:
            raise RuntimeError("ELASTICSEARCH_URL 和 KIBANA_URL 均未配置或连接失败")

        print(json.dumps({
            "success": True,
            "message": f"成功查询到 {len(hits)} 条相关日志（索引: {index_pattern}，模式: {mode}）",
            "data": hits
        }, ensure_ascii=False))

    except Exception as e:
        print(json.dumps({
            "success": False,
            "message": f"查询失败: {str(e)}"
        }, ensure_ascii=False))


if __name__ == "__main__":
    run()
