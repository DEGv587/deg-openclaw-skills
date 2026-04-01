# bug-locator-skill

串联 `sentry-skill` 和 `elk-skill` 的自动化 Bug 根因定位 Skill。输入 Bug 报告（项目、时间、页面路由），自动完成 Sentry 取证 → ELK 日志溯源 → 代码定位 → git blame 责任人的完整链路。

## 工作流程

```
输入 Bug 报告
  → Step 1: sentry-skill 定位页面异常和接口报错
      ├─ 0条结果 → 扩时间窗 / 读前端代码提取上报关键词 → 重试（最多3次）
      ├─ 结果过多 → 按 route + problem_description 二次评分，保留最多3条
      └─ 接口URL不完整 → 读前端代码找完整地址
      ↓
  ┌─────────────────────────────────────────────┐
  │ 前端报错（JS异常）                           │ → 查本地前端代码 → git blame
  ├─────────────────────────────────────────────┤
  │ 后端接口报错（4xx/5xx/业务错误码）           │ → elk-skill 查日志（见下）→ 查本地后端代码 → git blame
  ├─────────────────────────────────────────────┤
  │ 无明显报错                                   │ → 结合 breadcrumbs + 代码逻辑推测
  └─────────────────────────────────────────────┘
  → 输出：根因 + 文件:行号 + 查询过程 + commit + PR链接 + 责任人
```

**ELK 查询细节（后端报错时）：**

```
初始条件优先级：trace_id → request_id → 接口URL → 路径关键词
  ├─ 注意：前后端 trace_id 可能不互通，查不到时直接切换下一个条件
  ├─ 0条结果 → 切换条件 / 读后端 controller+service 提取日志关键词 / 扩时间窗（最多3次）
  ├─ 噪音过多 → 加 Sentry 已知字段精确过滤 / 读代码提取ERROR关键词 / 缩时间窗到±5min（最多3次）
  └─ 质量合格
      ↓
  trace_id 链路扩展（若当前非 trace_id 查询 且 日志含 trace_id）
    → 用提取的 trace_id 重查完整链路，取更有价值的结果
    → 此步骤不占重试配额
      ↓
  定位报错文件和行号 → git blame
```

## 依赖

- `sentry-skill`：需已配置并可用
- `elk-skill`：需已配置并可用
- `git`：本地已安装
- `python3`：本地已安装

## 配置

### 1. 创建仓库映射配置

复制 `config.json.example` 为 `config.json`，按实际情况填写：

```bash
cp config.json.example config.json
```

```json
{
  "repos": [
    {
      "name": "your-frontend-project",
      "type": "frontend",
      "path": "/absolute/path/to/frontend-repo",
      "sentry_project": "sentry-project-slug",
      "git_remote": "https://gitlab.example.com/org/frontend-repo",
      "environments": {
        "production": "main",
        "staging": "develop"
      }
    },
    {
      "name": "your-backend-project",
      "type": "backend",
      "path": "/absolute/path/to/backend-repo",
      "sentry_project": "sentry-backend-project-slug",
      "git_remote": "https://gitlab.example.com/org/backend-repo",
      "elk_projects": {
        "production": "prod-service-name",
        "staging": "staging-service-name"
      },
      "environments": {
        "production": "main",
        "staging": "develop"
      }
    }
  ]
}
```

**字段说明：**

| 字段 | 说明 |
|---|---|
| `name` | 项目标识，用于模糊匹配用户输入的 project |
| `type` | `frontend` 或 `backend` |
| `path` | 本地仓库绝对路径 |
| `sentry_project` | Sentry 中的 project slug，用于匹配 Sentry 返回结果 |
| `git_remote` | Git 仓库 HTTP 地址（不含 `.git`），用于拼接 commit 链接 |
| `elk_projects` | ELK 项目名映射，key 为环境名，value 为 elk-skill 的 project 参数；同一项目在不同环境可能对应不同 ELK 项目名 |
| `environments` | 环境名到 git 分支的映射，git blame 时切换到对应分支 |

### 2. 配置 openclaw.json（三合一）

复制 `openclaw.json.example` 为项目根目录的 `openclaw.json`（或合并到已有配置），填写 Sentry 和 ELK 的连接信息：

```json
{
  "skills": {
    "load": {
      "extraDirs": [
        "/absolute/path/to/deg-openclaw-skills/bug-locator-skill",
        "/absolute/path/to/deg-openclaw-skills/sentry-skill",
        "/absolute/path/to/deg-openclaw-skills/elk-skill"
      ],
      "watch": true
    },
    "entries": {
      "sentry-issue-investigation": {
        "env": {
          "SENTRY_BASE_URL": "https://sentry.io",
          "SENTRY_ORG": "your-organization-slug",
          "SENTRY_AUTH_TOKEN": "your-sentry-token-here"
        }
      },
      "elk-log-query": {
        "env": {
          "KIBANA_URL": "http://your-kibana-host",
          "ELASTICSEARCH_URL": "http://your-elasticsearch-host:9200",
          "ELK_API_KEY": "your-elk-api-key-here"
        }
      }
    }
  }
}
```

## 使用示例

```
用户：agentcy 项目线上环境，用户反馈昨天下午3点到4点订单确认页面报错，页面路由是 /order/confirm
```

```
用户：web-console 项目，2026-04-01T10:00:00Z 到 2026-04-01T11:00:00Z，
      staging 环境，URL: https://staging.example.com/dashboard?themeId=123，
      用户说点击提交按钮没有反应
```

## 输出示例

```
## Bug 定位报告

### 问题概述
订单确认页面调用 /api/order/submit 接口时，后端 OrderService 未处理空 userId 导致 NullPointerException

### 错误类型
后端接口报错

### 查询过程
Sentry 第1次查询命中，返回2条候选。ELK 初始用接口URL查询返回37条噪音日志，
读取 OrderController + OrderService 提取错误关键词后第2次查询收敛至4条，
从日志中提取 trace_id 扩展为完整链路共11条日志，定位到根因。

### 关键证据
Sentry 证据：
- Issue: TypeError: Cannot read properties of null (reading 'id')
- 相关接口: POST /api/order/submit → 500

ELK 证据：
- 报错位置: OrderService.java:142
- 异常: NullPointerException at userId validation
- 使用的查询条件: 关键词 "submitOrder userId"（第2次查询）
- 完整链路: 是，trace_id = abc-123-xyz

### 根因代码
文件: /path/to/backend/src/OrderService.java
行号: 142
...

### 责任人
作者: Zhang San <zhangsan@example.com>
提交时间: 2026-03-28 14:32
Commit: [a1b2c3d](https://gitlab.example.com/org/repo/-/commit/a1b2c3d) — feat: add order submit logic

### 修复建议
1. 在 OrderService.java:140 处增加 userId 的非空校验
2. ...
```
