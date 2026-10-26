# 知行客服 · 培训机构智能客服系统

单企业自用的本地开发版，包含管理后台、培训机构官网演示和可嵌入官网的客服组件。覆盖公安联考、国考、省考、选调生、事业单位、社会招警、辅警及社区工作者咨询。当前是可运行的首版，不代表原 10–12 周路线中的生产验收已经完成。

## 打开系统

- 管理后台：http://localhost:18080/
- 官网与悬浮客服：http://localhost:18080/demo
- 独立客服：http://localhost:18080/chat
- API 文档：http://localhost:18080/api/docs

管理员邮箱默认 `admin@zhixing.local`；密码在本地 `.env` 的 `ADMIN_PASSWORD`。该文件由初始化脚本生成随机值，权限为 600，已加入 `.gitignore`。不要把 `.env`、真实联系方式或 Key 发到公开仓库。

## 启动与停止

需要 Docker Desktop 与 Docker Compose。首次启动：

```bash
python3 scripts/bootstrap.py
python3 scripts/generate_knowledge.py
docker compose up -d --build
```

`bootstrap.py` 保留已有 `.env`。Compose 使用 `zhixing_support` 独立命名空间、数据库和持久卷，唯一暴露端口默认只绑定本机 `127.0.0.1:18080`。不使用或修改其他项目数据库。

```bash
docker compose ps
docker compose logs --tail=60 backend
docker compose stop
docker compose start
```

停止不会删除资料。不要使用 `docker compose down -v`，除非确实要删除此项目的持久化数据库和模型缓存。日志可能包含业务错误和文件名，分享前检查脱敏。

## DeepSeek

在 `.env` 中配置：

```dotenv
DEEPSEEK_API_KEY=在此填写自己的Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

随后运行 `docker compose up -d --force-recreate backend`。Key 只在后端环境变量中使用，后台只返回“是否配置”，不会显示值。模型和参数根据 2026-10-26 核验的 [DeepSeek 官方文档](https://api-docs.deepseek.com/) 实现；支持流式回答与用量记录，默认关闭思考模式以减少客服等待。

未配置 Key 时，界面和消息明确显示“资料摘录模式”；只返回实际找到的公开知识，不伪装为大模型回答。模型故障展示错误并保留检索来源，不隐藏失败。

## 知识与检索

`knowledge_base/` 包含 19 份演示 Markdown、`manifest.json` 及独立的初始验收样例 `evaluation.json`。18 份对外公开，1 份为内部权限测试资料。机构、价格、课程、员工、种子对话和联系方式都是合成设定，正式使用前必须替换审核。

首次启动幂等导入资料；之后修改文件不会覆盖后台现有版本。可以在后台创建、上传、编辑、发布或下架知识。上传默认为待审核，支持 UTF-8 TXT/Markdown、文本型 PDF、DOCX，单文件 5 MB，正文不超过 20 万字符。扫描 PDF 会提示首版尚未启用 OCR。

向量使用本地 FastEmbed 的 `BAAI/bge-small-zh-v1.5`，与中文分词/BM25 排名融合。模型首次下载保存在独立卷，过程在后台进行；不可用时明确显示关键词降级模式，不阻塞问答。当前首版最多扫描 5,000 个候选切片，超过此规模应升级为数据库侧召回，不能据此宣称已验证 10 万切片容量。

公众检索只读取 `published + public` 的企业资料，每次查询重新校验发布状态；下架立即失效。回答记录片段文本、文档及版本。当前文档编辑采用单版本正文＋消息引用快照，尚无完整历史版本回滚 UI。

## 建议体验顺序

1. 登录后台，查看“企业知识库”，选择一份演示课程并使用“检索测试”。
2. 打开官网演示，点击右下角“在线咨询”，询问“公安联考基础班多少钱”。
3. 展开答案来源，检查演示说明与资料版本；继续问“可以试听吗”。
4. 点击“请顾问联系我”，使用合成邮箱测试，主动确认本次联系用途。
5. 在后台“咨询线索”中查看标签证据，更新负责人、阶段和人工等级，添加跟进。
6. 在客服窗口申请转人工，在“咨询会话”接管并回复，查看访客窗口收到消息。
7. 将实际成交线索更新为“已成交”，“客户档案”会显示该记录。首版客户档案是已成交线索视图，尚未扩展企业组织/多联系人 CRM。

规则等级是辅助判断，不是成交概率；售后不参与销售等级评分。联系方式默认待核验，只有人工通过正式渠道核验后才能修改状态。当前没有发送短信、邮件或营销消息。

## 官网嵌入

本地网页可以引用：

```html
<script src="http://localhost:18080/widget.js" defer></script>
```

加载器使用独立 iframe、按点击加载、宿主页存储恢复凭据，并校验跨窗口消息的来源和发送窗口。访客令牌只能读取自己的会话，不依赖第三方 Cookie。公开入口仍需要限流；域名与 CORS 不能代替鉴权。

当前只部署在本机。正式企业官网不能引用开发者的 localhost，需要先部署到可访问的 HTTPS 服务、调整代理 CSP 与来源策略并完成安全验收。首版未实现强域名绑定管理，不能把组件的公开地址当作不可被他站使用的凭据。

## 测试

后端单元及接口测试在临时 SQLite 中运行，明确禁用模型 Key，不产生模型调用费用：

```bash
docker compose run --rm backend python -m pytest tests -q
```

真实 PostgreSQL/Redis/API 冒烟：

```bash
docker compose exec -T backend python - < scripts/smoke.py
```

该测试使用合成信息创建会话和线索，完成后标记演示，不进入默认业务统计。它验证未知问题处理，不额外调用付费模型。真实 DeepSeek 问答需要单独少量联调。

前端本地需要 Node 22；Docker 构建已内置，不要求更改系统 Node：

```bash
cd frontend
npm ci
npm run build
```

关闭“包含演示记录”可以排除合成数据。人工标记为测试的记录同样排除；验收和联调记录应标记为演示。有效咨询指已发送问题或主动提交联系请求的会话；仅打开面板不会增加咨询量。报表按 Asia/Shanghai 日期统计。当前统计查询有首版规模限制，不提供未经核验的解决率、通过率或成交因果结论。

## 数据、权限和恢复边界

- 后台使用 Argon2 密码哈希、随机服务器会话、12 小时过期、HttpOnly Cookie 及写入来源校验。
- 访客使用独立高熵令牌，持久化仅存 HMAC 摘要，7 天后不能恢复旧会话。
- 联系方式使用 Fernet 认证加密，密钥由 `SESSION_SECRET` 派生。**更换该密钥必须先规划解密重加密迁移和会话失效，不能直接删除或随机覆盖。** 备份必须安全保留该密钥。
- 访客明确拒绝继续联系时，记录停止联系状态；需要再次主动提交授权才能重新开启。文档下架、转内部或更新版本后，公众历史、重放和模型上下文不再返回旧引用正文，后台保留审计快照。
- 所有业务对象保留企业标识；当前只有一个机构管理员，尚未完成多员工细分 RBAC、多租户 RLS 或 SaaS 商业化隔离验收。
- 核心资料、会话、线索和跟进持久化 PostgreSQL，模型缓存在独立卷。数据库备份可使用 `docker compose exec -T db pg_dump -U zhixing zhixing > backup.sql`；备份含密文及业务内容，应安全存放。
- 此次未完成备份恢复演练、长期压测、生产 SLA、自动隐私请求处理、完整数据删除生命周期、审计不可篡改和扫描件沙箱解析。正式接触真实客户数据前需要继续落实。

## 项目结构

```text
backend/app/       配置、模型、认证、知识检索、DeepSeek、会话与后台接口
backend/tests/     规则、解析、安全、权限和链路测试
frontend/src/      React 工作台、聊天页、官网演示及样式
frontend/public/   网站悬浮组件加载器
knowledge_base/    演示资料、导入清单和初始验收问题
scripts/          初始化、资料生成及真实容器冒烟
docs/             完整方案、实施计划与进度
```

后续按原路线继续扩大真实知识评测集、完善权限和文档历史、增强人工坐席与隐私流程，再进行指定并发、性能、恢复与安全验收。不能以开发测试通过代替生产可用性结论。
