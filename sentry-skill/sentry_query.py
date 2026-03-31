import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import requests


if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def load_config():
    config_path = Path(__file__).parent / "config.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as file:
            return json.load(file)
    return {}


def clean_base_url(base_url):
    return (base_url or "https://sentry.io").rstrip("/")


def sentry_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def request_json(method, base_url, token, path, params=None, timeout=15):
    response = requests.request(
        method=method,
        url=f"{base_url}{path}",
        headers=sentry_headers(token),
        params=params,
        timeout=timeout,
    )
    response.raise_for_status()
    if not response.text.strip():
        return None, response.headers
    return response.json(), response.headers


def parse_next_cursor(link_header):
    if not link_header:
        return None

    for item in link_header.split(","):
        if 'rel="next"' not in item or 'results="true"' not in item:
            continue
        match = re.search(r'cursor="([^"]+)"', item)
        if match:
            return match.group(1)
    return None


def get_paginated(base_url, token, path, params=None, timeout=15, max_pages=10):
    merged_params = dict(params or {})
    results = []
    cursor = None

    for _ in range(max_pages):
        current_params = dict(merged_params)
        if cursor:
            current_params["cursor"] = cursor

        payload, headers = request_json(
            "GET",
            base_url,
            token,
            path,
            params=current_params,
            timeout=timeout,
        )

        if isinstance(payload, list):
            results.extend(payload)
        elif payload is not None:
            results.append(payload)

        cursor = parse_next_cursor(headers.get("Link"))
        if not cursor:
            break

    return results


def normalize_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def mask_text(value, keep_start=2, keep_end=2):
    text = normalize_text(value)
    if not text:
        return text
    if len(text) <= keep_start + keep_end:
        return "*" * len(text)
    return text[:keep_start] + "*" * (len(text) - keep_start - keep_end) + text[-keep_end:]


def mask_numeric_ids(text):
    return re.sub(r"\d{6,}", lambda match: mask_text(match.group(0), 3, 2), normalize_text(text))


def mask_url(value):
    text = normalize_text(value)
    if not text:
        return text
    masked = mask_numeric_ids(text)
    masked = re.sub(r"([?&][^=]+=)([^&]+)", lambda m: m.group(1) + mask_text(m.group(2), 2, 2), masked)
    return masked


def mask_user_display(value):
    text = normalize_text(value)
    if not text:
        return text
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", text):
        parts = text.split(".")
        return ".".join(parts[:2] + ["***", "***"])
    return mask_text(text, 2, 1)


def try_parse_json(value):
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return value


def collect_nested_candidates(payload, target_key):
    matches = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == target_key:
                matches.append(value)
            matches.extend(collect_nested_candidates(value, target_key))
    elif isinstance(payload, list):
        for item in payload:
            matches.extend(collect_nested_candidates(item, target_key))
    return matches


def score_text(text, route_variants):
    normalized = normalize_text(text).lower()
    if not normalized:
        return 0

    score = 0
    for index, variant in enumerate(route_variants):
        candidate = variant.lower()
        if candidate and candidate in normalized:
            score += max(5 - index, 1)
    return score


def build_route_variants(route):
    route = (route or "").strip()
    if not route:
        return []

    variants = []

    def add(value):
        if value and value not in variants:
            variants.append(value)

    add(route)
    parsed = urlparse(route)
    if parsed.path:
        add(parsed.path)
        add(parsed.path.rstrip("/"))
    stripped = route.split("?", 1)[0].split("#", 1)[0]
    add(stripped)
    add(stripped.rstrip("/"))

    segments = [segment for segment in stripped.split("/") if segment]
    if segments:
        add("/" + "/".join(segments[-2:]))
        add(segments[-1])

    return [item for item in variants if item]


def build_url_clues(url):
    parsed = urlparse((url or "").strip())
    if not parsed.scheme and not parsed.netloc and not parsed.path:
        return {
            "path": None,
            "page_search": None,
            "ids": [],
            "query_pairs": [],
            "variants": [],
        }

    path = parsed.path or None
    page_search = f"?{parsed.query}" if parsed.query else None
    ids = re.findall(r"\d{6,}", " ".join([parsed.path or "", parsed.query or ""]))
    query_pairs = [{"key": key, "value": value} for key, value in parse_qsl(parsed.query, keep_blank_values=True)]

    variants = []

    def add(value):
        if value and value not in variants:
            variants.append(value)

    if path:
        for item in build_route_variants(path):
            add(item)
    if page_search:
        add(page_search)
    for identifier in ids:
        add(identifier)
        add(f"url:*{identifier}*")
    if path:
        add(f'page_path:"{path}"')
    if page_search:
        add(f'page_search:"{page_search}"')

    return {
        "path": path,
        "page_search": page_search,
        "ids": ids,
        "query_pairs": query_pairs,
        "variants": variants,
    }


def extract_problem_terms(problem_description):
    text = (problem_description or "").strip()
    if not text:
        return []

    candidates = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_:/.-]{3,}", text)
    terms = []
    for item in candidates:
        lowered = item.lower()
        if lowered not in terms:
            terms.append(lowered)
    return terms[:10]


def summarize_project(project):
    return {
        "id": project.get("id"),
        "slug": project.get("slug"),
        "name": project.get("name"),
        "platform": project.get("platform"),
    }


def resolve_project(project_hint, projects, max_matches):
    if not project_hint:
        return {"resolved": None, "matches": []}

    keyword = project_hint.strip().lower()
    exact_matches = []
    scored = []
    for project in projects:
        slug = normalize_text(project.get("slug")).lower()
        name = normalize_text(project.get("name")).lower()

        if keyword == slug or keyword == name:
            score = 100
            exact_matches.append(project)
        elif keyword in slug:
            score = 80
        elif keyword in name:
            score = 60
        else:
            continue

        scored.append((score, project))

    scored.sort(key=lambda item: (-item[0], item[1].get("slug", "")))
    matches = [project for _, project in scored[:max_matches]]

    if len(exact_matches) == 1:
        resolved = exact_matches[0]
    else:
        resolved = matches[0] if len(matches) == 1 else None
    return {"resolved": resolved, "matches": matches}


def issue_summary(issue):
    metadata = issue.get("metadata") or {}
    return {
        "id": issue.get("id"),
        "short_id": issue.get("shortId"),
        "title": issue.get("title"),
        "culprit": issue.get("culprit"),
        "permalink": issue.get("permalink"),
        "status": issue.get("status"),
        "level": issue.get("level"),
        "count": issue.get("count"),
        "user_count": issue.get("userCount"),
        "first_seen": issue.get("firstSeen"),
        "last_seen": issue.get("lastSeen"),
        "project": issue.get("project"),
        "metadata": {
            "type": metadata.get("type"),
            "value": metadata.get("value"),
            "filename": metadata.get("filename"),
            "function": metadata.get("function"),
        },
    }


def is_api_issue(issue_or_event):
    title = normalize_text(issue_or_event.get("title")).upper()
    message = normalize_text(issue_or_event.get("message")).upper()
    exception = issue_or_event.get("exception") or {}
    exception_type = normalize_text(exception.get("type")).upper()
    exception_value = normalize_text(exception.get("value")).upper()
    tags = issue_or_event.get("tags") or {}

    return any(
        [
            "API_500_ERROR" in title,
            "API_" in title,
            "API_500_ERROR" in message,
            "API_" in exception_type,
            "API_" in exception_value,
            bool(tags.get("errorUrl")),
            bool(tags.get("errorType")),
        ]
    )


def score_api_diagnostic_value(issue_or_event):
    score = 0
    tags = issue_or_event.get("tags") or {}

    if is_api_issue(issue_or_event):
        score += 40
    if tags.get("errorUrl"):
        score += 20
    if tags.get("errorType"):
        score += 10

    for request_error in issue_or_event.get("error_data_requests") or []:
        score += 30
        score += 20 if request_error.get("is_business_error") else 0
        score += 15 if request_error.get("is_http_error") else 0
        score += 5 if request_error.get("request_body") else 0
        score += 5 if request_error.get("response_data") else 0

    for request_error in issue_or_event.get("related_request_errors") or []:
        status_code = request_error.get("status_code")
        is_http_failure = isinstance(status_code, int) and status_code >= 400
        score += 8 if is_http_failure else 0
        score += 3 if request_error.get("message") else 0

    return score


def collect_tag_map(event):
    tags = event.get("tags") or []
    tag_map = {}
    for item in tags:
        key = item.get("key")
        value = item.get("value")
        if key:
            tag_map[key] = value
    return tag_map


def collect_request_info(event):
    contexts = event.get("contexts") or {}
    request_context = contexts.get("request") or {}
    request_data = event.get("request") or {}

    method = request_context.get("method") or request_data.get("method")
    url = request_context.get("url") or request_data.get("url")
    query_string = request_context.get("query_string") or request_data.get("query_string")
    data = request_context.get("data") or request_data.get("data")
    headers = request_data.get("headers") or request_context.get("headers")

    return {
        "method": method,
        "url": url,
        "query_string": query_string,
        "data": data,
        "headers": headers,
    }


def collect_exception_info(event):
    entries = event.get("entries") or []
    for entry in entries:
        if entry.get("type") != "exception":
            continue
        values = ((entry.get("data") or {}).get("values")) or []
        if not values:
            continue

        exception = values[0]
        stacktrace = exception.get("stacktrace") or {}
        frames = stacktrace.get("frames") or []
        simplified_frames = [
            {
                "filename": frame.get("filename"),
                "function": frame.get("function"),
                "lineno": frame.get("lineno"),
                "module": frame.get("module"),
                "context_line": frame.get("context_line"),
            }
            for frame in frames[-8:]
        ]
        return {
            "type": exception.get("type"),
            "value": exception.get("value"),
            "mechanism": exception.get("mechanism"),
            "stacktrace": simplified_frames,
        }
    return {}


def extract_error_data_requests(event, route_variants):
    containers = []
    for key in ["extra", "contexts"]:
        payload = event.get(key)
        if payload:
            containers.append(payload)
    for entry in event.get("entries") or []:
        data = entry.get("data")
        if data:
            containers.append(data)

    error_data_items = []
    for container in containers:
        error_data_items.extend(collect_nested_candidates(container, "errorData"))

    extracted = []
    for item in error_data_items:
        if not isinstance(item, dict):
            continue

        config = item.get("config") or {}
        response_data = try_parse_json(item.get("data"))
        request_info = item.get("request") or {}
        sentry_xhr = request_info.get("__sentry_xhr_v3__") or {}

        request_body = try_parse_json(
            config.get("data") or sentry_xhr.get("body")
        )
        request_headers = sentry_xhr.get("request_headers") or config.get("headers")
        method = (
            sentry_xhr.get("method")
            or config.get("method")
            or request_info.get("method")
        )
        url = (
            sentry_xhr.get("url")
            or config.get("url")
            or request_info.get("url")
        )

        http_status = (
            sentry_xhr.get("status_code")
            or item.get("status")
            or item.get("statusCode")
        )
        try:
            http_status = int(http_status) if http_status is not None else None
        except (TypeError, ValueError):
            pass

        business_code = None
        business_message = None
        if isinstance(response_data, dict):
            business_code = response_data.get("code")
            business_message = response_data.get("message") or response_data.get("msg")

        route_score = score_text(
            " ".join(
                [
                    normalize_text(url),
                    normalize_text(request_body),
                    normalize_text(response_data),
                ]
            ),
            route_variants,
        )

        extracted.append(
            {
                "source": "errorData",
                "method": method.upper() if isinstance(method, str) else method,
                "url": url,
                "http_status": http_status,
                "business_code": business_code,
                "business_message": business_message,
                "request_body": request_body,
                "request_headers": request_headers,
                "response_data": response_data,
                "route_score": route_score,
                "is_http_error": bool(http_status and http_status >= 400),
                "is_business_error": bool(
                    business_code not in (None, 0, 200, "0", "200")
                ),
            }
        )

    extracted.sort(
        key=lambda item: (
            -(1 if item.get("is_business_error") else 0),
            -(1 if item.get("is_http_error") else 0),
            -(item.get("route_score") or 0),
        )
    )
    return extracted


def extract_related_request_errors(event, route_variants, breadcrumb_limit):
    breadcrumbs = []
    entries = event.get("entries") or []
    for entry in entries:
        if entry.get("type") != "breadcrumbs":
            continue
        breadcrumb_values = ((entry.get("data") or {}).get("values")) or []
        breadcrumbs.extend(breadcrumb_values)

    related = []
    for crumb in breadcrumbs:
        category = normalize_text(crumb.get("category")).lower()
        crumb_type = normalize_text(crumb.get("type")).lower()
        data = crumb.get("data") or {}
        text_blob = " ".join(
            [
                normalize_text(crumb.get("message")),
                normalize_text(category),
                normalize_text(crumb_type),
                normalize_text(data),
            ]
        )

        has_http_shape = any(token in text_blob.lower() for token in ["fetch", "xhr", "http", "api", "request"])
        route_score = score_text(text_blob, route_variants)
        status_code = data.get("status_code") or data.get("statusCode") or data.get("response_status")
        numeric_status = None
        if status_code is not None:
            try:
                numeric_status = int(status_code)
            except (TypeError, ValueError):
                numeric_status = None

        has_error_shape = any(
            value
            for value in [
                numeric_status and numeric_status >= 400,
                data.get("error"),
                data.get("message"),
                normalize_text(crumb.get("level")).lower() in {"error", "fatal"},
            ]
        )

        if not has_http_shape and route_score == 0 and not has_error_shape:
            continue

        related.append(
            {
                "timestamp": crumb.get("timestamp"),
                "category": crumb.get("category"),
                "type": crumb.get("type"),
                "level": crumb.get("level"),
                "method": data.get("method") or data.get("request_method"),
                "url": data.get("url") or data.get("request_url") or data.get("to"),
                "status_code": numeric_status if numeric_status is not None else status_code,
                "message": crumb.get("message") or data.get("message") or data.get("error"),
                "data": data,
                "route_score": route_score,
            }
        )

    related.sort(
        key=lambda item: (
            -(item.get("route_score") or 0),
            -(1 if item.get("status_code") else 0),
            item.get("timestamp") or "",
        )
    )
    return related[:breadcrumb_limit]


def dedupe_request_errors(items):
    deduped = []
    seen = set()
    for item in items:
        key = (
            item.get("method"),
            item.get("url"),
            item.get("status_code"),
            item.get("message"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def dedupe_error_data_requests(items):
    deduped = []
    seen = set()
    for item in items:
        key = (
            item.get("method"),
            item.get("url"),
            normalize_text(item.get("request_body")),
            item.get("business_code"),
            item.get("http_status"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def select_primary_request_signal(recommended_event):
    if not recommended_event:
        return {}

    error_data_requests = recommended_event.get("error_data_requests") or []
    if error_data_requests:
        return {
            "url": error_data_requests[0].get("url"),
            "method": error_data_requests[0].get("method"),
            "status_code": error_data_requests[0].get("http_status"),
            "business_code": error_data_requests[0].get("business_code"),
            "business_message": error_data_requests[0].get("business_message"),
        }

    tags = recommended_event.get("tags") or {}
    if tags.get("errorUrl") or tags.get("errorType"):
        return {
            "url": tags.get("errorUrl"),
            "method": None,
            "status_code": None,
            "business_code": tags.get("errorType"),
            "business_message": None,
        }

    related_request_errors = recommended_event.get("related_request_errors") or []
    filtered_related = [
        item for item in related_request_errors
        if "website/track" not in normalize_text(item.get("url"))
    ]
    primary = filtered_related[0] if filtered_related else (related_request_errors[0] if related_request_errors else {})
    return {
        "url": primary.get("url"),
        "method": primary.get("method"),
        "status_code": primary.get("status_code"),
        "business_code": None,
        "business_message": primary.get("message"),
    }


def summarize_event(event, route_variants, breadcrumb_limit):
    tags = collect_tag_map(event)
    request_info = collect_request_info(event)
    exception_info = collect_exception_info(event)
    error_data_requests = extract_error_data_requests(event, route_variants)
    related_request_errors = extract_related_request_errors(
        event,
        route_variants=route_variants,
        breadcrumb_limit=breadcrumb_limit,
    )

    return {
        "event_id": event.get("eventID") or event.get("id"),
        "group_id": event.get("groupID"),
        "title": event.get("title"),
        "message": event.get("message"),
        "date_created": event.get("dateCreated"),
        "platform": event.get("platform"),
        "location": event.get("location"),
        "transaction": event.get("transaction"),
        "tags": tags,
        "request": request_info,
        "exception": exception_info,
        "error_data_requests": error_data_requests,
        "related_request_errors": related_request_errors,
        "contexts": event.get("contexts") or {},
        "user": event.get("user") or {},
    }


def extract_user_action_trail(event, limit=30):
    actions = []

    for entry in event.get("entries") or []:
        if entry.get("type") != "breadcrumbs":
            continue
        for crumb in ((entry.get("data") or {}).get("values") or []):
            category = normalize_text(crumb.get("category")).lower()
            crumb_type = normalize_text(crumb.get("type")).lower()
            data = crumb.get("data") or {}
            if not any(token in f"{category} {crumb_type}" for token in ["ui", "click", "navigation", "xhr", "fetch", "http"]):
                continue
            actions.append(
                {
                    "timestamp": crumb.get("timestamp"),
                    "kind": "breadcrumb",
                    "category": crumb.get("category"),
                    "type": crumb.get("type"),
                    "message": crumb.get("message"),
                    "data": data,
                }
            )

    for span in (event.get("spans") or []):
        op = normalize_text(span.get("op")).lower()
        if not any(token in op for token in ["ui", "http", "navigation", "resource", "browser"]):
            continue
        actions.append(
            {
                "timestamp": span.get("startTimestamp") or span.get("timestamp"),
                "kind": "span",
                "category": span.get("op"),
                "type": "span",
                "message": span.get("description"),
                "data": {
                    "op": span.get("op"),
                    "description": span.get("description"),
                    "status": span.get("status"),
                    "span_id": span.get("spanID") or span.get("spanId"),
                },
            }
        )

    actions.sort(key=lambda item: item.get("timestamp") or "")
    return actions[:limit]


def build_operation_summary(event_detail):
    trail = extract_user_action_trail(event_detail)
    steps = []
    current_requests = []

    def flush_requests():
        nonlocal current_requests
        if current_requests:
            steps.append(
                {
                    "step": len(steps) + 1,
                    "action": "接口请求",
                    "requests": current_requests,
                }
            )
            current_requests = []

    for item in trail:
        category = normalize_text(item.get("category")).lower()
        data = item.get("data") or {}

        if category in {"xhr", "fetch"}:
            request_url = normalize_text(data.get("url"))
            if "/track" in request_url:
                continue
            current_requests.append(
                {
                    "method": data.get("method"),
                    "url": request_url,
                    "status_code": data.get("status_code"),
                }
            )
            continue

        flush_requests()

        if category == "navigation":
            steps.append(
                {
                    "step": len(steps) + 1,
                    "time": item.get("timestamp"),
                    "action": "页面跳转",
                    "navigation": {
                        "from": data.get("from"),
                        "to": data.get("to"),
                    },
                }
            )
        elif category == "ui.click":
            steps.append(
                {
                    "step": len(steps) + 1,
                    "time": item.get("timestamp"),
                    "action": "界面点击",
                    "ui_target": item.get("message"),
                }
            )

    flush_requests()
    return steps


def build_event_evidence_summary(event_detail, recommended_event):
    tags = {item.get("key"): item.get("value") for item in (event_detail.get("tags") or []) if item.get("key")}
    user = event_detail.get("user") or {}
    return {
        "evidence_type": "sentry_transaction_trace",
        "page_context": {
            "project": recommended_event.get("project.name") or recommended_event.get("project"),
            "user_display": recommended_event.get("user.display"),
            "tenant_id": (user.get("data") or {}).get("tenant_id"),
            "tenant_name": (user.get("data") or {}).get("tenant_name"),
            "user_id": (user.get("data") or {}).get("user_id"),
            "user_name": (user.get("data") or {}).get("user_name"),
            "release": tags.get("release"),
            "environment": tags.get("environment"),
            "entry_url": tags.get("url"),
            "page_path": tags.get("page_path"),
            "page_search": tags.get("page_search"),
            "event_id": event_detail.get("id"),
            "event_type": event_detail.get("type"),
            "timestamp": recommended_event.get("timestamp"),
        },
        "operation_summary": build_operation_summary(event_detail),
        "important_notes": [
            "当前证据来自 transaction/event 轨迹，不一定代表存在前端异常。",
            "接口状态码来自 breadcrumbs，若均为 200，仍可能存在业务层异常。",
            "该摘要保留原始证据字段，便于后续 skill 继续定位代码与接口链路。",
        ],
    }


def get_entry(event_detail, entry_type):
    for entry in event_detail.get("entries") or []:
        if entry.get("type") == entry_type:
            return entry.get("data") or {}
    return {}


def build_actor_fingerprint(event_row, event_detail):
    user = event_detail.get("user") or {}
    user_data = user.get("data") or {}
    contexts = event_detail.get("contexts") or {}
    browser = contexts.get("browser") or {}
    os_context = contexts.get("os") or {}
    return {
        "event_id": event_detail.get("id") or event_row.get("id"),
        "user_display": normalize_text(event_row.get("user.display")),
        "user_ip": normalize_text(user.get("ip_address")),
        "browser_name": normalize_text(browser.get("name")),
        "browser_version": normalize_text(browser.get("version")),
        "os_name": normalize_text(os_context.get("name")),
        "os_version": normalize_text(os_context.get("version")),
        "user_id": normalize_text(user_data.get("user_id")),
        "tenant_id": normalize_text(user_data.get("tenant_id")),
        "timestamp": normalize_text(event_row.get("timestamp")),
        "title": normalize_text(event_row.get("title")),
    }


def compare_actor_fingerprints(left, right):
    reasons = []
    score = 0

    if left.get("user_display") and left.get("user_display") == right.get("user_display"):
        score += 3
        reasons.append("same user.display")
    if left.get("user_ip") and left.get("user_ip") == right.get("user_ip"):
        score += 3
        reasons.append("same user.ip")
    if left.get("user_id") and left.get("user_id") == right.get("user_id"):
        score += 4
        reasons.append("same user_id")
    if left.get("tenant_id") and left.get("tenant_id") == right.get("tenant_id"):
        score += 1
        reasons.append("same tenant_id")
    if (
        left.get("browser_name")
        and left.get("browser_name") == right.get("browser_name")
        and left.get("browser_version")
        and left.get("browser_version") == right.get("browser_version")
    ):
        score += 1
        reasons.append("same browser")
    if (
        left.get("os_name")
        and left.get("os_name") == right.get("os_name")
        and left.get("os_version")
        and left.get("os_version") == right.get("os_version")
    ):
        score += 1
        reasons.append("same os")

    confidence = "low"
    if score >= 9:
        confidence = "high"
    elif score >= 5:
        confidence = "medium"

    return score, confidence, reasons


def build_same_actor_groups(actor_fingerprints):
    groups = []
    used = set()

    for index, actor in enumerate(actor_fingerprints):
        event_id = actor.get("event_id")
        if not event_id or event_id in used:
            continue

        group = {
            "group_key": event_id,
            "confidence": "low",
            "event_ids": [event_id],
            "reasons": [],
            "shared_actor": {
                "user_display": actor.get("user_display"),
                "user_ip": actor.get("user_ip"),
            },
        }
        used.add(event_id)

        for other_index in range(index + 1, len(actor_fingerprints)):
            other = actor_fingerprints[other_index]
            other_event_id = other.get("event_id")
            if not other_event_id or other_event_id in used:
                continue

            score, confidence, reasons = compare_actor_fingerprints(actor, other)
            if score >= 5:
                group["event_ids"].append(other_event_id)
                group["reasons"] = sorted(set(group["reasons"] + reasons))
                if confidence == "high" or (confidence == "medium" and group["confidence"] == "low"):
                    group["confidence"] = confidence
                used.add(other_event_id)

        if len(group["event_ids"]) > 1:
            groups.append(group)

    return groups


def score_issue(issue, route_variants):
    score = 0
    score += score_text(issue.get("title"), route_variants)
    score += score_text(issue.get("culprit"), route_variants)
    score += score_text(issue.get("permalink"), route_variants)
    score += score_text(issue.get("metadata") or {}, route_variants)
    score += score_api_diagnostic_value(issue)
    return score


def score_event(event, route_variants):
    score = 0
    score += score_text(event.get("title"), route_variants)
    score += score_text(event.get("message"), route_variants)
    score += score_text(event.get("transaction"), route_variants)
    score += score_text(event.get("tags"), route_variants)
    score += score_text(event.get("request"), route_variants)
    score += score_api_diagnostic_value(event)

    for request_error in event.get("error_data_requests") or []:
        score += (request_error.get("route_score") or 0)
        score += 8 if request_error.get("is_business_error") else 0
        score += 5 if request_error.get("is_http_error") else 0

    for request_error in event.get("related_request_errors") or []:
        score += (request_error.get("route_score") or 0) + (3 if request_error.get("status_code") else 0)

    return score


def build_issue_queries(route_variants, max_queries):
    queries = []
    for variant in route_variants:
        if variant and variant not in queries:
            queries.append(variant)
        if len(queries) >= max_queries:
            break
    if "" not in queries:
        queries.append("")
    return queries


def fetch_issue_candidates(base_url, token, organization, project_id, route_variants, environment, start_time, end_time, limit, timeout, max_route_queries, sentry_query=None):
    collected = []
    seen_issue_ids = set()
    query_attempts = []

    candidate_queries = [sentry_query] if sentry_query else build_issue_queries(route_variants, max_route_queries)

    for query in candidate_queries:
        try:
            issues = list_issues(
                base_url=base_url,
                token=token,
                organization=organization,
                project_id=project_id,
                route=query or None,
                environment=environment,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
                timeout=timeout,
            )
            query_attempts.append({"query": query, "status": "ok"})
        except requests.HTTPError as error:
            query_attempts.append({"query": query, "status": "error", "message": str(error)})
            continue
        for issue in issues:
            issue_id = issue.get("id")
            if issue_id in seen_issue_ids:
                continue
            seen_issue_ids.add(issue_id)
            collected.append(issue)
        if collected:
            break

    return collected, query_attempts


def list_projects(base_url, token, organization, timeout):
    return get_paginated(
        base_url=base_url,
        token=token,
        path=f"/api/0/organizations/{organization}/projects/",
        params={"per_page": 100},
        timeout=timeout,
    )


def list_discover_events(base_url, token, organization, project_id, query, environment, start_time, end_time, limit, timeout):
    params = {
        "per_page": limit,
        "query": query,
        "referrer": "api.discover.query-table",
        "sort": "-timestamp",
        "field": [
            "title",
            "event.type",
            "project",
            "user.display",
            "timestamp",
            "replayId",
        ],
    }
    if project_id:
        params["project"] = project_id
    if environment:
        params["environment"] = environment
    if start_time:
        params["start"] = start_time
    if end_time:
        params["end"] = end_time

    payload, _ = request_json(
        "GET",
        base_url,
        token,
        f"/api/0/organizations/{organization}/events/",
        params=params,
        timeout=timeout,
    )
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            return payload.get("data") or []
        if isinstance(payload.get("results"), list):
            return payload.get("results") or []
    return []


def list_issues(base_url, token, organization, project_id, route, environment, start_time, end_time, limit, timeout):
    params = {
        "limit": limit,
        "shortIdLookup": 1,
    }
    if project_id:
        params["project"] = project_id
    if route:
        params["query"] = route
    if environment:
        params["environment"] = environment
    if start_time:
        params["start"] = start_time
    if end_time:
        params["end"] = end_time

    payload, _ = request_json(
        "GET",
        base_url,
        token,
        f"/api/0/organizations/{organization}/issues/",
        params=params,
        timeout=timeout,
    )
    return payload or []


def list_issue_events(base_url, token, organization, issue_id, route, environment, start_time, end_time, limit, timeout):
    params = {
        "full": "true",
        "limit": limit,
    }
    if route:
        params["query"] = route
    if environment:
        params["environment"] = environment
    if start_time:
        params["start"] = start_time
    if end_time:
        params["end"] = end_time

    payload, _ = request_json(
        "GET",
        base_url,
        token,
        f"/api/0/organizations/{organization}/issues/{issue_id}/events/",
        params=params,
        timeout=timeout,
    )
    return payload or []


def fetch_event_evidence(base_url, token, organization, project_id, route_variants, sentry_query, environment, start_time, end_time, limit, timeout):
    event_query_attempts = []
    candidate_queries = [sentry_query] if sentry_query else [variant for variant in route_variants if variant]

    for query in candidate_queries:
        try:
            events = list_discover_events(
                base_url=base_url,
                token=token,
                organization=organization,
                project_id=project_id,
                query=query,
                environment=environment,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
                timeout=timeout,
            )
            event_query_attempts.append({"query": query, "status": "ok", "count": len(events)})
        except requests.HTTPError as error:
            event_query_attempts.append({"query": query, "status": "error", "message": str(error)})
            continue

        if events:
            return events, event_query_attempts

    return [], event_query_attempts


def get_issue_event(base_url, token, organization, issue_id, event_id, timeout):
    payload, _ = request_json(
        "GET",
        base_url,
        token,
        f"/api/0/organizations/{organization}/issues/{issue_id}/events/{event_id}/",
        timeout=timeout,
    )
    return payload or {}


def get_project_event_detail(base_url, token, organization, project_slug, event_id, timeout):
    payload, _ = request_json(
        "GET",
        base_url,
        token,
        f"/api/0/projects/{organization}/{project_slug}/events/{event_id}/",
        timeout=timeout,
    )
    return payload or {}


def pick_recommended_event(base_url, token, organization, issue_id, route, environment, start_time, end_time, route_variants, max_events_per_issue, breadcrumb_limit, timeout, sentry_query=None):
    events = []
    candidate_queries = [sentry_query] if sentry_query else build_issue_queries(route_variants, len(route_variants) or 1)
    for query in candidate_queries:
        try:
            events = list_issue_events(
                base_url=base_url,
                token=token,
                organization=organization,
                issue_id=issue_id,
                route=query or None,
                environment=environment,
                start_time=start_time,
                end_time=end_time,
                limit=max_events_per_issue,
                timeout=timeout,
            )
        except requests.HTTPError:
            continue
        if events:
            break

    if not events:
        for fallback_event_id in ["latest", "recommended"]:
            try:
                event = get_issue_event(
                    base_url=base_url,
                    token=token,
                    organization=organization,
                    issue_id=issue_id,
                    event_id=fallback_event_id,
                    timeout=timeout,
                )
                if event:
                    events = [event]
                    break
            except requests.HTTPError:
                continue

    scored_events = []
    for raw_event in events:
        summarized = summarize_event(raw_event, route_variants, breadcrumb_limit)
        scored_events.append(
            {
                "score": score_event(summarized, route_variants),
                "event": summarized,
            }
        )

    if not scored_events:
        return None

    scored_events.sort(key=lambda item: (-item["score"], item["event"].get("date_created") or ""))
    return scored_events[0]["event"]


def build_analysis_hints(recommended_event):
    if not recommended_event:
        return {}

    exception = recommended_event.get("exception") or {}
    request_info = recommended_event.get("request") or {}
    primary_signal = select_primary_request_signal(recommended_event)

    return {
        "error_type": exception.get("type"),
        "error_value": exception.get("value"),
        "transaction": recommended_event.get("transaction"),
        "page_url": request_info.get("url"),
        "related_api_url": primary_signal.get("url"),
        "related_api_method": primary_signal.get("method"),
        "related_api_status_code": primary_signal.get("status_code"),
        "related_api_business_code": primary_signal.get("business_code"),
        "related_api_business_message": primary_signal.get("business_message"),
    }


def run():
    config = load_config()
    timeout = config.get("timeout_seconds", 15)
    base_url = clean_base_url(os.getenv("SENTRY_BASE_URL"))
    token = os.getenv("SENTRY_AUTH_TOKEN", "")
    default_org = os.getenv("SENTRY_ORG", "")

    try:
        args = json.loads(sys.argv[1])
        organization = args.get("organization") or default_org
        project_hint = args.get("project")
        url = args.get("url")
        route = args.get("route")
        sentry_query = args.get("sentry_query")
        problem_description = args.get("problem_description")
        start_time = args.get("start_time")
        end_time = args.get("end_time")
        environment = args.get("environment")
        limit = int(args.get("limit") or config.get("default_limit", 3))

        if not token:
            raise ValueError("缺少环境变量 SENTRY_AUTH_TOKEN")
        if not organization:
            raise ValueError("缺少 organization，请传 organization 参数或配置环境变量 SENTRY_ORG")
        url_clues = build_url_clues(url)
        derived_route = route or url_clues.get("path")
        problem_terms = extract_problem_terms(problem_description)

        if not derived_route and not sentry_query and not url:
            raise ValueError("url、route 和 sentry_query 不能同时为空")

        route_variants = build_route_variants(derived_route) if derived_route else []
        for clue in url_clues.get("variants", []):
            if clue not in route_variants:
                route_variants.append(clue)
        for term in problem_terms:
            if term not in route_variants:
                route_variants.append(term)
        projects = list_projects(base_url, token, organization, timeout)
        resolution = resolve_project(
            project_hint=project_hint,
            projects=projects,
            max_matches=config.get("max_project_matches", 5),
        )

        if project_hint and not resolution["resolved"] and len(resolution["matches"]) > 1:
            print(
                json.dumps(
                    {
                        "success": False,
                        "need_clarification": True,
                        "message": f"关键词 '{project_hint}' 匹配到多个项目，请确认后重试",
                        "matches": [summarize_project(project) for project in resolution["matches"]],
                    },
                    ensure_ascii=False,
                )
            )
            return

        resolved_project = resolution["resolved"]
        project_id = resolved_project.get("id") if resolved_project else None

        if url or sentry_query:
            events, event_query_attempts = fetch_event_evidence(
                base_url=base_url,
                token=token,
                organization=organization,
                project_id=project_id,
                route_variants=route_variants,
                sentry_query=sentry_query,
                environment=environment,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
                timeout=timeout,
            )
            event_detail = {}
            user_action_trail = []
            event_evidence_summary = {}
            same_actor_groups = []
            recommended_event = events[0] if events else None
            event_details_by_id = {}
            if events and resolved_project:
                for event in events:
                    event_id = event.get("id")
                    if not event_id:
                        continue
                    try:
                        detail = get_project_event_detail(
                            base_url=base_url,
                            token=token,
                            organization=organization,
                            project_slug=resolved_project.get("slug"),
                            event_id=event_id,
                            timeout=timeout,
                        )
                    except requests.HTTPError:
                        detail = {}
                    if detail:
                        event_details_by_id[event_id] = detail

                actor_fingerprints = [
                    build_actor_fingerprint(event, event_details_by_id[event.get("id")] )
                    for event in events
                    if event.get("id") in event_details_by_id
                ]
                same_actor_groups = build_same_actor_groups(actor_fingerprints)

            if recommended_event and recommended_event.get("id") and resolved_project:
                try:
                    event_detail = event_details_by_id.get(recommended_event.get("id")) or get_project_event_detail(
                        base_url=base_url,
                        token=token,
                        organization=organization,
                        project_slug=resolved_project.get("slug"),
                        event_id=recommended_event.get("id"),
                        timeout=timeout,
                    )
                    if event_detail:
                        user_action_trail = extract_user_action_trail(event_detail)
                        event_evidence_summary = build_event_evidence_summary(event_detail, recommended_event)
                except requests.HTTPError:
                    event_detail = {}
                    user_action_trail = []
                    event_evidence_summary = {}
            print(
                json.dumps(
                    {
                        "success": True,
                        "message": f"找到 {len(events)} 条相关事件",
                        "query_context": {
                            "organization": organization,
                            "project_input": project_hint,
                            "resolved_project": summarize_project(resolved_project) if resolved_project else None,
                            "url": url,
                            "route": derived_route,
                            "sentry_query": sentry_query,
                            "route_variants": route_variants,
                            "url_clues": url_clues,
                            "problem_description": problem_description,
                            "problem_terms": problem_terms,
                            "event_query_attempts": event_query_attempts,
                            "start_time": start_time,
                            "end_time": end_time,
                            "environment": environment,
                        },
                        "matched_events": events,
                        "candidates": [],
                        "recommended_event": recommended_event,
                        "event_detail": event_detail,
                        "user_action_trail": user_action_trail,
                        "event_evidence_summary": event_evidence_summary,
                        "same_actor_groups": same_actor_groups,
                        "error_data_requests": [],
                        "related_request_errors": [],
                        "analysis_hints": {},
                    },
                    ensure_ascii=False,
                )
            )
            return

        issues, issue_query_attempts = fetch_issue_candidates(
            base_url=base_url,
            token=token,
            organization=organization,
            project_id=project_id,
            route_variants=route_variants,
            environment=environment,
            start_time=start_time,
            end_time=end_time,
            limit=config.get("max_issue_candidates", 10),
            timeout=timeout,
            max_route_queries=config.get("max_route_queries", 4),
            sentry_query=sentry_query,
        )

        scored_candidates = []
        for issue in issues:
            summarized_issue = issue_summary(issue)
            base_score = score_issue(issue, route_variants)
            recommended_event = pick_recommended_event(
                base_url=base_url,
                token=token,
                organization=organization,
                issue_id=issue.get("id"),
                route=derived_route,
                environment=environment,
                start_time=start_time,
                end_time=end_time,
                route_variants=route_variants,
                max_events_per_issue=config.get("max_events_per_issue", 5),
                breadcrumb_limit=config.get("request_error_breadcrumb_limit", 10),
                timeout=timeout,
                sentry_query=sentry_query,
            )
            total_score = base_score + (score_event(recommended_event, route_variants) if recommended_event else 0)
            scored_candidates.append(
                {
                    "score": total_score,
                    "api_diagnostic_score": score_api_diagnostic_value(recommended_event or summarized_issue),
                    "issue": summarized_issue,
                    "recommended_event": recommended_event,
                }
            )

        scored_candidates.sort(
            key=lambda item: (
                -item.get("api_diagnostic_score", 0),
                -item["score"],
                item["issue"].get("last_seen") or "",
            )
        )

        selected_candidates = scored_candidates[:limit]
        top_candidate = selected_candidates[0] if selected_candidates else None
        related_request_errors = []
        error_data_requests = []
        if top_candidate and top_candidate.get("recommended_event"):
            error_data_requests = dedupe_error_data_requests(
                top_candidate["recommended_event"].get("error_data_requests") or []
            )
            related_request_errors = dedupe_request_errors(
                top_candidate["recommended_event"].get("related_request_errors") or []
            )
            top_candidate["recommended_event"]["error_data_requests"] = error_data_requests
            top_candidate["recommended_event"]["related_request_errors"] = related_request_errors

        print(
            json.dumps(
                {
                    "success": True,
                    "message": f"找到 {len(selected_candidates)} 个候选 issue",
                    "query_context": {
                        "organization": organization,
                        "project_input": project_hint,
                        "resolved_project": summarize_project(resolved_project) if resolved_project else None,
                        "url": url,
                        "route": derived_route,
                        "sentry_query": sentry_query,
                        "route_variants": route_variants,
                        "url_clues": url_clues,
                        "problem_description": problem_description,
                        "problem_terms": problem_terms,
                        "issue_query_attempts": issue_query_attempts,
                        "start_time": start_time,
                        "end_time": end_time,
                        "environment": environment,
                    },
                    "candidates": [
                        {
                            "score": item["score"],
                            "api_diagnostic_score": item.get("api_diagnostic_score", 0),
                            "issue": item["issue"],
                            "recommended_event": item["recommended_event"],
                        }
                        for item in selected_candidates
                    ],
                    "recommended_event": top_candidate.get("recommended_event") if top_candidate else None,
                    "error_data_requests": error_data_requests,
                    "related_request_errors": related_request_errors,
                    "analysis_hints": build_analysis_hints(
                        top_candidate.get("recommended_event") if top_candidate else None
                    ),
                },
                ensure_ascii=False,
            )
        )

    except Exception as error:
        print(
            json.dumps(
                {
                    "success": False,
                    "message": f"查询失败: {str(error)}",
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    run()
