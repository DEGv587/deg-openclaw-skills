---
name: feishu-bitable-sync
description: 飞书多维表格同步助手。支持初始化字段映射和人员配置、轮询待排查记录、新建记录、更新记录字段（状态/责任人/根因等）、发送排查完成通知并@责任人。
metadata:
  openclaw:
    requires:
      bins: ["python3"]
      env: ["FEISHU_APP_ID", "FEISHU_APP_SECRET"]
parameters:
  - name: action
    description: "操作类型：init_config / list_pending / create_record / update_record / send_message / poll"
  - name: record_id
    description: 要更新的记录 ID（update_record 时必填）
  - name: fields
    description: 要写入的字段键值对，JSON 对象格式，key 为 field_map 中的语义键（如 {"assignee":"张三","status":"已排查待修复"}）
  - name: message
    description: 消息正文（send_message 时必填），支持企微 markdown 语法
  - name: at_user_git_name
    description: 要@的用户 git 名称，脚本会从 member_map 中查找飞书姓名并在消息末尾添加@（send_message 时可选）
---

### Use when
- bug-locator-skill 排查完毕，需要将结果写回飞书表格并通知责任人。
- 定时轮询飞书表格，拉取所有"待排查"记录供 bug-locator-skill 逐条处理。
- 首次接入飞书表格，需要完成字段映射和人员映射配置（init_config）。

---

### Step 0：读取配置

读取当前 skill 目录下的 `config.json`。

**若 config.json 不存在**，说明尚未完成初始化，立即执行 `init_config` 流程（见下方），完成后继续原始 action。

**若 config.json 存在**，校验以下必填项是否齐全：
- `app_id`、`app_secret`
- `bitable.app_token`、`bitable.table_id`
- `bitable.field_map.status`
- `bitable.status_values.pending`（待排查对应值）

任意缺失则告知用户需要补充哪个字段，停止执行。

---

### Action: init_config — 初始化配置

**仅在首次使用或用户主动要求重新配置时执行。**

#### 阶段一：基础连接配置

询问用户：
1. 飞书应用的 App ID（`cli_` 开头）
2. 飞书应用的 App Secret
3. 多维表格链接（如 `https://weiling-tech.feishu.cn/base/XHgKbAA3ZaOvqqs1Rpsc1PSBnvd`），从中自动解析 `app_token`
4. 目标 Sheet 的 Table ID（若链接中未包含，调用 `python3 feishu_bitable.py list_tables` 列出所有 sheet 供选择）
5. 接收通知的飞书群 chat_id（可暂时留空）

完成后运行 `python3 feishu_bitable.py test_connection` 验证连通性，失败则提示检查 App ID / Secret / 权限。

#### 阶段二：字段映射配置

调用 `python3 feishu_bitable.py list_fields` 获取该表所有字段，输出字段列表，逐一询问用户每个字段对应的语义角色：

```
从表格中读取到以下字段，请告诉我每个字段的用途（直接回车跳过）：
  1. 问题描述  → ?
  2. 状态      → ?
  3. 负责人    → ?
  ...
```

可选的语义角色（对应 field_map 的 key）：
- `bug_description`：问题描述
- `route`：页面路由或 URL
- `project`：项目名称
- `environment`：环境
- `incident_time`：问题发生时间
- `status`：状态（必选）
- `assignee`：责任人（git blame 结果）
- `commit`：Commit 信息
- `root_cause`：根因摘要
- `fix_suggestion`：修复建议
- `located_at`：排查完成时间

询问完成后，针对 `status` 字段，调用 `python3 feishu_bitable.py list_field_options --field {status_field_name}` 获取所有单选选项，让用户指定每个选项对应的语义：

```
状态字段有以下选项，请告诉我每个选项的语义：
  - 待排查       → pending（触发排查的初始状态）？
  - 已排查待修复 → located（排查完成后写入）？
  - 修复中       → fixing？
  - 已解决       → resolved？
  - 待复现       → reproducing？
  - 延期处理     → deferred？
  - 不处理       → wont_fix？
```

#### 阶段三：人员映射配置

调用 `python3 feishu_bitable.py scan_git_authors` 扫描 bug-locator-skill 的 `config.json` 中所有仓库（取各仓库最近 100 条 commit 的提交者），去重后输出 git 提交者列表。

逐一询问用户每位提交者在飞书中的 user_id（`ou_` 开头）：

```
从 git 仓库中扫描到以下提交者，请提供他们的飞书 user_id（用于@通知，不需要可留空）：
  - zhangsan (zhangsan@company.com) → 飞书 user_id: ?
  - lisi (lisi@company.com)         → 飞书 user_id: ?
```

> 提示：飞书 user_id 可在飞书管理后台「成员管理」中查看，或通过飞书 API `GET /contact/v3/users/batch_get_id` 按邮箱批量查询。

若用户提供了邮箱与飞书账号一致的信息，可调用 `python3 feishu_bitable.py resolve_user_by_email --emails {email_list}` 自动尝试查询（需要通讯录权限）。

#### 阶段四：写入配置文件

将以上信息组织为 `config.json` 写入当前目录，格式参考 `config.json.example`。

---

### Action: list_pending — 拉取待排查记录

调用 `python3 feishu_bitable.py list_pending`。

脚本按 `status_values.pending` 过滤表格记录，返回结构化列表：

```json
[
  {
    "record_id": "recxxxxxxxx",
    "bug_description": "...",
    "route": "/order/confirm",
    "project": "xxx",
    "environment": "production",
    "incident_time": "2026-04-01T10:00:00+08:00"
  }
]
```

- 若返回空列表，告知用户当前无待排查记录。
- 将列表传递给 bug-locator-skill 逐条处理。

---

### Action: create_record — 新建记录

调用 `python3 feishu_bitable.py create_record --fields '{fields_json}'`。

`fields` 使用语义键，脚本内部通过 `field_map` 转为实际字段名。支持的语义键：

| 语义键 | 飞书字段 | 类型 | 值说明 |
|---|---|---|---|
| `bug_description` | 问题描述 | 文本 | 字符串 |
| `route` | 路由(url) | 文本 | 页面路由或完整 URL |
| `incident_time` | 问题发生时间 | 日期时间 | ISO 8601 字符串或毫秒时间戳 |
| `status` | 状态 | 单选 | 语义键（如 `"pending"`）或实际文本（如 `"待排查"`） |
| `priority` | 优先级 | 单选 | `"P0"` / `"P1"` / `"P2"` |
| `source` | 来源 | 单选 | `"测试"` / `"外部"` |
| `platform` | 端类型 | 单选 | `"Web"` / `"iOS"` / `"Android"` / `"macOS"` / `"Windows"` |
| `reporter` | 提出人 | 人员 | 飞书 user_id（`ou_` 开头） |

典型写入场景（@openclaw 上报 bug 后直接创建记录）：

```json
{
  "bug_description": "kos混剪失败，页面报错",
  "route": "/matrix/kos",
  "incident_time": "2026-04-03T14:00:00+08:00",
  "status": "pending",
  "priority": "P1",
  "source": "外部",
  "platform": "Web"
}
```

脚本返回 `{"success": true, "record_id": "recxxxxxxxx"}` 或包含错误信息的对象。返回的 `record_id` 可直接传给 `bug-locator-skill` 触发排查。

---

### Action: update_record — 更新记录

调用 `python3 feishu_bitable.py update_record --record_id {record_id} --fields '{fields_json}'`。

`fields` 参数使用语义键，脚本内部通过 `field_map` 转换为表格实际字段名。

常用写入场景：
- 排查开始：`{"status": "排查中"}`（使用 status_values 中的实际文本值，或语义键如 `"in_progress"`，脚本自动转换）
- 排查完成：`{"status": "已排查待修复", "assignee": "张三", "commit": "[abc1234](https://...)", "root_cause": "...", "fix_suggestion": "...", "located_at": "2026-04-01T11:30:00+08:00"}`

脚本返回 `{"success": true}` 或包含错误信息的对象。

---

### Action: send_message — 发送通知消息

调用 `python3 feishu_bitable.py send_message --message '{text}' [--at_user_git_name {git_name}]`。

发送两条消息：
1. **markdown**：完整排查摘要，格式化展示
2. **text + mentioned_list**：触发真正的@提醒

企微消息格式（markdown）：

```
**[Bug 已初步定位]** {问题描述截断 30 字}

**问题描述：** {bug_description}
**错误类型：** {前端报错 / 后端接口报错 / 推测}

**根因：**
{root_cause，超过 500 字截断，末尾加"（详见飞书表格）"}

**责任人：** {assignee}
**Commit：** [{short_hash}]({commit_url}) — {commit message}

**修复建议：**
{fix_suggestion 第一条}
```

飞书表格 `状态补充说明` 写入完整内容（根因 + 代码位置 + 修复建议全部），不做截断。

`at_user_git_name` 通过 `member_map` 查找 `wecom_userid`，找不到则不@。

---

### Action: poll — 定时轮询

调用 `python3 feishu_bitable.py poll`。

脚本按 `bitable.poll_interval_minutes` 设定的间隔（默认 10 分钟），持续检查是否有新的"待排查"记录。

检测到新记录时，输出结构化列表供 openclaw 触发 bug-locator-skill。

> 注意：poll 是长驻进程，openclaw 应在后台运行此 action，并监听其输出来触发排查。

---

### 全局注意事项

- `app_token` 从飞书表格链接中自动解析（`/base/` 后的字符串，去掉 `?` 之后的部分）
- 所有飞书 API 调用使用 `tenant_access_token`，脚本自动处理 token 刷新（有效期 2 小时）
- `update_record` 的 `fields` 中 status 值支持两种写法：语义键（如 `"located"`）或实际文本（如 `"已排查待修复"`），脚本优先按语义键转换
- 人员映射找不到时不阻塞流程，仅跳过@操作并在日志中警告
- 飞书 API 限频：多条记录更新时每次调用间隔 100ms
