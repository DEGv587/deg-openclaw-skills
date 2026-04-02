# deg-openclaw-skills

这个仓库目前包含四个用于问题定位和飞书同步的 OpenClaw skill：

## `elk-skill`

面向 ELK / Kibana 日志查询。

- 适合查服务端日志、接口链路、trace/request 级别细节
- 支持按关键词、字段、项目、索引模式和时间范围查询
- 更适合回答”后端实际打印了什么日志”

入口文件：
- [elk_query.py](/Users/deg/Documents/my-work/deg-openclaw-skills/elk-skill/elk_query.py)
- [SKILL.md](/Users/deg/Documents/my-work/deg-openclaw-skills/elk-skill/SKILL.md)
- [README.md](/Users/deg/Documents/my-work/deg-openclaw-skills/elk-skill/README.md)

## `sentry-skill`

面向 Sentry 的前端问题定位和事件证据提取。

- 适合根据页面 URL、时间范围、问题描述查相关 issue / event
- 支持 event-first 查询，能提取 transaction、breadcrumbs、请求线索和用户操作轨迹
- 更适合回答”这个页面当时在前端发生了什么”

入口文件：
- [sentry_query.py](/Users/deg/Documents/my-work/deg-openclaw-skills/sentry-skill/sentry_query.py)
- [SKILL.md](/Users/deg/Documents/my-work/deg-openclaw-skills/sentry-skill/SKILL.md)
- [README.md](/Users/deg/Documents/my-work/deg-openclaw-skills/sentry-skill/README.md)

## `bug-locator-skill`

串联 `sentry-skill` 和 `elk-skill` 的自动化 Bug 根因定位 Skill。

- 输入 Bug 报告（项目、时间、页面路由），自动完成 Sentry 取证 → ELK 日志溯源 → 代码定位 → git blame 责任人的完整链路
- 可选开启飞书同步：排查完成后自动回写飞书多维表格并@责任人
- 若传入 `record_id`（来自飞书表格），将自动更新对应记录状态和排查结论

入口文件：
- [SKILL.md](/Users/deg/Documents/my-work/deg-openclaw-skills/bug-locator-skill/SKILL.md)
- [README.md](/Users/deg/Documents/my-work/deg-openclaw-skills/bug-locator-skill/README.md)

## `feishu-bitable-sync`

飞书多维表格同步 Skill，为 `bug-locator-skill` 提供飞书读写能力。

- **轮询待排查**：定时读取表格中状态为”待排查”的记录，逐条触发 `bug-locator-skill` 排查
- **状态流转**：排查开始时将状态更新为”排查中”，完成后更新为”已排查待修复”
- **回写结论**：将责任人、Commit、根因摘要、修复建议写入对应记录
- **@责任人通知**：向飞书群发送排查完成消息，通过 member_map 将 git 作者映射为飞书用户并@
- **初始化向导**：`init_config` action 可自动拉取表格字段、引导完成字段映射和人员映射配置

### 典型流程

```
飞书表格新增 Bug 记录（状态：待排查）
         │
         ▼ 定时轮询（poll）或手动触发
feishu-bitable-sync list_pending
         │ 返回待排查记录列表
         ▼ 逐条处理
feishu-bitable-sync update_record（状态→排查中）
         │
         ▼
bug-locator-skill（传入 record_id + 表格字段作为参数）
         │ 排查完成
         ▼
feishu-bitable-sync update_record（写入责任人/commit/根因/修复建议，状态→已排查待修复）
         │
         ▼
feishu-bitable-sync send_message（飞书群通知 @责任人）
```

入口文件：
- [feishu_bitable.py](/Users/deg/Documents/my-work/deg-openclaw-skills/feishu-bitable-sync/feishu_bitable.py)
- [SKILL.md](/Users/deg/Documents/my-work/deg-openclaw-skills/feishu-bitable-sync/SKILL.md)

## 简单分工建议

- 先看页面报错、用户操作轨迹、前端请求链路：用 `sentry-skill`
- 需要继续看服务端日志、trace 明细、接口内部异常：用 `elk-skill`
- 两者可以串联使用：先用 Sentry 缩小范围，再用 ELK 深挖服务端细节
- 需要全自动端到端定位，不想手动串联：用 `bug-locator-skill`
- 需要从飞书表格批量触发排查并回写结果：配合 `feishu-bitable-sync` 使用
