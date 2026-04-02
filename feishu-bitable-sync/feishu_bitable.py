#!/usr/bin/env python3
"""
feishu_bitable.py — feishu-bitable-sync skill 核心脚本

Commands:
  test_connection
  list_tables
  list_fields
  list_field_options  --field <field_name>
  scan_git_authors
  resolve_user_by_email  --emails <email1,email2,...>
  list_pending
  update_record  --record_id <id>  --fields '<json>'
  send_message   --chat_id <id>  --message '<text>'  [--at_user_id <id>]
  poll
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print(json.dumps({"error": "requests not installed. Run: pip install requests"}))
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SKILL_DIR = Path(__file__).parent
CONFIG_PATH = SKILL_DIR / "config.json"

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return json.load(f)

def save_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# ---------------------------------------------------------------------------
# Feishu auth
# ---------------------------------------------------------------------------

_token_cache: dict = {}

def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    now = time.time()
    if _token_cache.get("token") and _token_cache.get("expire_at", 0) > now + 60:
        return _token_cache["token"]

    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data}")
    _token_cache["token"] = data["tenant_access_token"]
    _token_cache["expire_at"] = now + data.get("expire", 7200)
    return _token_cache["token"]

def feishu_get(path: str, cfg: dict, params: dict = None) -> dict:
    token = get_tenant_access_token(cfg["app_id"], cfg["app_secret"])
    resp = requests.get(
        f"https://open.feishu.cn/open-apis{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()

def feishu_post(path: str, cfg: dict, body: dict) -> dict:
    token = get_tenant_access_token(cfg["app_id"], cfg["app_secret"])
    resp = requests.post(
        f"https://open.feishu.cn/open-apis{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()

def feishu_put(path: str, cfg: dict, body: dict) -> dict:
    token = get_tenant_access_token(cfg["app_id"], cfg["app_secret"])
    resp = requests.put(
        f"https://open.feishu.cn/open-apis{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_app_token(url_or_token: str) -> str:
    """从飞书表格链接或直接 token 中解析 app_token。"""
    m = re.search(r"/base/([A-Za-z0-9]+)", url_or_token)
    if m:
        return m.group(1)
    # 去掉 query string 后直接返回
    return url_or_token.split("?")[0].strip("/").split("/")[-1]

def lookup_feishu_user(git_name: str, cfg: dict) -> dict | None:
    """通过 git 作者名（大小写不敏感）在 member_map 中查找飞书用户信息。"""
    git_name_lower = git_name.lower()
    for member in cfg.get("member_map", []):
        for gn in member.get("git_names", []):
            if gn.lower() == git_name_lower:
                return member
    return None

def resolve_status(value: str, cfg: dict) -> str:
    """将语义键（如 'located'）或实际文本原样返回（如已是中文则直接用）。"""
    status_values: dict = cfg.get("bitable", {}).get("status_values", {})
    if value in status_values:
        return status_values[value]
    return value

def resolve_field_name(semantic_key: str, cfg: dict) -> str | None:
    field_map: dict = cfg.get("bitable", {}).get("field_map", {})
    return field_map.get(semantic_key)

def build_feishu_fields(semantic_fields: dict, cfg: dict) -> dict:
    """将语义键值对转为飞书 API 所需的 {实际字段名: 值} 格式。"""
    field_map: dict = cfg.get("bitable", {}).get("field_map", {})
    result = {}
    for key, val in semantic_fields.items():
        if key == "status":
            actual = field_map.get("status")
            if actual:
                result[actual] = resolve_status(val, cfg)
        elif key == "assignee":
            # 人员字段：val 为 git 作者名，查 member_map 转为 [{"id": "ou_xxx"}]
            actual = field_map.get("assignee")
            if actual:
                member = lookup_feishu_user(val, cfg)
                if member:
                    result[actual] = [{"id": member["feishu_user_id"]}]
                # 找不到则跳过，不写入，避免写入错误格式报错
        else:
            actual = field_map.get(key)
            if actual:
                result[actual] = val
    return result

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_test_connection(cfg: dict):
    try:
        token = get_tenant_access_token(cfg["app_id"], cfg["app_secret"])
        bt = cfg["bitable"]
        data = feishu_get(
            f"/bitable/v1/apps/{bt['app_token']}/tables/{bt['table_id']}/fields",
            cfg,
            params={"page_size": 1},
        )
        if data.get("code") != 0:
            print(json.dumps({"success": False, "error": data}))
        else:
            print(json.dumps({"success": True, "token_prefix": token[:8] + "..."}))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))


def cmd_list_tables(cfg: dict):
    bt = cfg["bitable"]
    data = feishu_get(f"/bitable/v1/apps/{bt['app_token']}/tables", cfg)
    tables = [
        {"table_id": t["table_id"], "name": t.get("name", "")}
        for t in data.get("data", {}).get("items", [])
    ]
    print(json.dumps({"tables": tables}, ensure_ascii=False))


def cmd_list_fields(cfg: dict):
    bt = cfg["bitable"]
    items = []
    page_token = None
    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        data = feishu_get(
            f"/bitable/v1/apps/{bt['app_token']}/tables/{bt['table_id']}/fields",
            cfg,
            params=params,
        )
        for f in data.get("data", {}).get("items", []):
            items.append({
                "field_id": f["field_id"],
                "field_name": f["field_name"],
                "type": f.get("type"),
            })
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data["data"]["page_token"]
    print(json.dumps({"fields": items}, ensure_ascii=False))


def cmd_list_field_options(cfg: dict, field_name: str):
    bt = cfg["bitable"]
    data = feishu_get(
        f"/bitable/v1/apps/{bt['app_token']}/tables/{bt['table_id']}/fields",
        cfg,
        params={"page_size": 100},
    )
    for f in data.get("data", {}).get("items", []):
        if f["field_name"] == field_name:
            options = f.get("property", {}).get("options", [])
            print(json.dumps({"options": [o["name"] for o in options]}, ensure_ascii=False))
            return
    print(json.dumps({"error": f"字段 '{field_name}' 未找到"}))


def cmd_scan_git_authors(cfg: dict):
    """扫描 bug-locator-skill config.json 中所有仓库的最近提交者。"""
    bug_locator_config = SKILL_DIR.parent / "bug-locator-skill" / "config.json"
    if not bug_locator_config.exists():
        print(json.dumps({"error": "bug-locator-skill/config.json 不存在"}))
        return

    with open(bug_locator_config) as f:
        bl_cfg = json.load(f)

    authors: dict[str, str] = {}  # git_name -> email
    for repo in bl_cfg.get("repos", []):
        path = repo.get("path", "")
        if not Path(path).exists():
            continue
        try:
            out = subprocess.check_output(
                ["git", "log", "--format=%an\t%ae", "-n", "100"],
                cwd=path, stderr=subprocess.DEVNULL, text=True,
            )
            for line in out.strip().splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    name, email = parts
                    if name not in authors:
                        authors[name] = email
        except Exception:
            continue

    result = [{"git_name": n, "git_email": e} for n, e in authors.items()]
    print(json.dumps({"authors": result}, ensure_ascii=False))


def cmd_resolve_user_by_email(cfg: dict, emails: list[str]):
    data = feishu_post(
        "/contact/v3/users/batch_get_id",
        cfg,
        {"emails": emails, "user_id_type": "user_id"},
    )
    users = []
    for item in data.get("data", {}).get("user_list", []):
        users.append({
            "email": item.get("email", ""),
            "user_id": item.get("user_id", ""),
        })
    print(json.dumps({"users": users}, ensure_ascii=False))


def cmd_list_pending(cfg: dict):
    bt = cfg["bitable"]
    status_field = bt["field_map"].get("status", "状态")
    pending_value = bt["status_values"].get("pending", "待排查")
    field_map_inv = {v: k for k, v in bt["field_map"].items()}

    # 飞书 Bitable 过滤语法，单选字段用 AND(CurrentValue.[字段名]="值")
    filter_expr = f'AND(CurrentValue.[{status_field}]="{pending_value}")'

    items = []
    page_token = None
    while True:
        params = {
            "page_size": 100,
            "filter": filter_expr,
        }
        if page_token:
            params["page_token"] = page_token
        data = feishu_get(
            f"/bitable/v1/apps/{bt['app_token']}/tables/{bt['table_id']}/records",
            cfg,
            params=params,
        )
        for record in (data.get("data", {}).get("items") or []):
            row: dict[str, Any] = {"record_id": record["record_id"]}
            for actual_field, val in record.get("fields", {}).items():
                semantic = field_map_inv.get(actual_field)
                if semantic is None:
                    continue  # 跳过未映射字段
                # 人员字段：列表，取第一个成员的 name
                if isinstance(val, list) and val and isinstance(val[0], dict) and "name" in val[0]:
                    row[semantic] = val[0]["name"]
                # 单选/文本：直接取值
                elif isinstance(val, dict) and "text" in val:
                    row[semantic] = val["text"]
                else:
                    row[semantic] = val
            items.append(row)
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data["data"]["page_token"]

    print(json.dumps({"records": items}, ensure_ascii=False))


def cmd_update_record(cfg: dict, record_id: str, fields: dict):
    bt = cfg["bitable"]
    feishu_fields = build_feishu_fields(fields, cfg)
    if not feishu_fields:
        print(json.dumps({"error": "没有可映射的字段，请检查 field_map 配置"}))
        return

    data = feishu_put(
        f"/bitable/v1/apps/{bt['app_token']}/tables/{bt['table_id']}/records/{record_id}",
        cfg,
        {"fields": feishu_fields},
    )
    if data.get("code") == 0:
        print(json.dumps({"success": True, "record_id": record_id}))
    else:
        print(json.dumps({"success": False, "error": data}))


def cmd_send_message(cfg: dict, message: str, at_user_git_name: str = None):
    webhook_url = cfg.get("notify", {}).get("wecom_webhook_url", "")
    if not webhook_url:
        print(json.dumps({"error": "notify.wecom_webhook_url 未配置"}))
        return

    content = message.replace("\\n", "\n")
    member = None
    if at_user_git_name:
        member = lookup_feishu_user(at_user_git_name, cfg)

    # 第一条：markdown 格式，显示完整排查内容
    resp1 = requests.post(webhook_url, json={
        "msgtype": "markdown",
        "markdown": {"content": content},
    }, timeout=10)
    resp1.raise_for_status()

    # 第二条：text + mentioned_list，触发真正的@提醒
    at_name = member["feishu_name"] if member else ""
    wecom_userid = member.get("wecom_userid", "") if member else ""
    at_content = f"以上 Bug 请 @{at_name} 跟进修复" if at_name else "以上 Bug 请相关同学跟进修复"

    resp2 = requests.post(webhook_url, json={
        "msgtype": "text",
        "text": {
            "content": at_content,
            "mentioned_list": [wecom_userid] if wecom_userid else [],
        },
    }, timeout=10)
    resp2.raise_for_status()

    r1, r2 = resp1.json(), resp2.json()
    if r1.get("errcode") == 0 and r2.get("errcode") == 0:
        print(json.dumps({"success": True}))
    else:
        print(json.dumps({"success": False, "error": {"msg1": r1, "msg2": r2}}))


def cmd_poll(cfg: dict):
    """长驻轮询：每 N 分钟检查是否有待排查记录，有则输出到 stdout 供 openclaw 捕获。"""
    interval = cfg.get("bitable", {}).get("poll_interval_minutes", 10) * 60
    print(json.dumps({"status": "polling_started", "interval_seconds": interval}), flush=True)
    while True:
        try:
            bt = cfg["bitable"]
            status_field = bt["field_map"].get("status", "状态")
            pending_value = bt["status_values"].get("pending", "待排查")
            filter_expr = f'AND(CurrentValue.[{status_field}]="{pending_value}")'
            data = feishu_get(
                f"/bitable/v1/apps/{bt['app_token']}/tables/{bt['table_id']}/records",
                cfg,
                params={"page_size": 20, "filter": filter_expr},
            )
            records = data.get("data", {}).get("items") or []
            if records:
                print(json.dumps({
                    "event": "pending_found",
                    "count": len(records),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }), flush=True)
        except Exception as e:
            print(json.dumps({"event": "poll_error", "error": str(e)}), flush=True)
        time.sleep(interval)

# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("--field", default="")
    parser.add_argument("--emails", default="")
    parser.add_argument("--record_id", default="")
    parser.add_argument("--fields", default="{}")
    parser.add_argument("--message", default="")
    parser.add_argument("--at_user_git_name", default="")
    args = parser.parse_args()

    cfg = load_config()

    if args.command == "test_connection":
        cmd_test_connection(cfg)
    elif args.command == "list_tables":
        cmd_list_tables(cfg)
    elif args.command == "list_fields":
        cmd_list_fields(cfg)
    elif args.command == "list_field_options":
        cmd_list_field_options(cfg, args.field)
    elif args.command == "scan_git_authors":
        cmd_scan_git_authors(cfg)
    elif args.command == "resolve_user_by_email":
        emails = [e.strip() for e in args.emails.split(",") if e.strip()]
        cmd_resolve_user_by_email(cfg, emails)
    elif args.command == "list_pending":
        cmd_list_pending(cfg)
    elif args.command == "update_record":
        fields = json.loads(args.fields)
        cmd_update_record(cfg, args.record_id, fields)
    elif args.command == "send_message":
        cmd_send_message(cfg, args.message, args.at_user_git_name or None)
    elif args.command == "poll":
        cmd_poll(cfg)
    else:
        print(json.dumps({"error": f"未知命令: {args.command}"}))
        sys.exit(1)

if __name__ == "__main__":
    main()
