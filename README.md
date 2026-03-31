# deg-openclaw-skills

这个仓库目前包含两个用于问题定位的 OpenClaw skill：

## `elk-skill`

面向 ELK / Kibana 日志查询。

- 适合查服务端日志、接口链路、trace/request 级别细节
- 支持按关键词、字段、项目、索引模式和时间范围查询
- 更适合回答“后端实际打印了什么日志”

入口文件：
- [elk_query.py](/Users/deg/Documents/my-work/deg-openclaw-skills/elk-skill/elk_query.py)
- [SKILL.md](/Users/deg/Documents/my-work/deg-openclaw-skills/elk-skill/SKILL.md)
- [README.md](/Users/deg/Documents/my-work/deg-openclaw-skills/elk-skill/README.md)

## `sentry-skill`

面向 Sentry 的前端问题定位和事件证据提取。

- 适合根据页面 URL、时间范围、问题描述查相关 issue / event
- 支持 event-first 查询，能提取 transaction、breadcrumbs、请求线索和用户操作轨迹
- 更适合回答“这个页面当时在前端发生了什么”

入口文件：
- [sentry_query.py](/Users/deg/Documents/my-work/deg-openclaw-skills/sentry-skill/sentry_query.py)
- [SKILL.md](/Users/deg/Documents/my-work/deg-openclaw-skills/sentry-skill/SKILL.md)
- [README.md](/Users/deg/Documents/my-work/deg-openclaw-skills/sentry-skill/README.md)

## 简单分工建议

- 先看页面报错、用户操作轨迹、前端请求链路：用 `sentry-skill`
- 需要继续看服务端日志、trace 明细、接口内部异常：用 `elk-skill`
- 两者可以串联使用：先用 Sentry 缩小范围，再用 ELK 深挖服务端细节
