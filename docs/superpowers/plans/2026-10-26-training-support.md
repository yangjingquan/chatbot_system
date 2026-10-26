# 培训机构智能客服首版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在本地 Docker 运行可验证的培训知识管理、官网咨询、留资分级、人工接待及运营后台。

**Architecture:** FastAPI 模块化业务应用，PostgreSQL/pgvector 持久化，Redis 限流，FastEmbed 本地中文向量与关键词混合召回。React 前端由 Nginx 提供并反向代理 API；DeepSeek 仅服务端访问。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、PostgreSQL 17/pgvector、Redis 7、React 19、TypeScript、Vite、Nginx。

**Spec:** ../../智能客服系统_可行性与完整实现方案.md；本次范围修订以 PRODUCT.md 为准。

## Global Constraints
- 用户已确认单企业自用、预留扩展；已授权本地 Docker 安装与启动。不操作已有其他项目容器。
- 所有机构与经营资料明确标记演示；不编造政策日期、报名资格、编制或通过率承诺。
- DeepSeek Key 只放本地 .env，前端、Git、日志、接口不得回显。
- 未接入模型时返回有来源的资料摘录并明确模式；模型故障不伪装成功。
- 公众检索仅限已发布 public 文档；访客令牌仅授权自身会话；后台操作需要认证。
- 首版实现文本/PDF/DOCX 文件解析；扫描 PDF 提示需 OCR，不假装解析成功。

## Review Focus
- 未发布、内部或下架资料不得出现在公众答案及来源中（任务 2、4）。
- 他人会话、过期身份、伪造管理员与跨站请求不能获得后台或会话数据（任务 3、4）。
- 否定、售后和信息不足不误判高意向；留资需明确用途确认（任务 4）。
- 模型断流、无 Key、未知问题和重复提交具备可解释且可恢复行为（任务 4、6）。
- 演示数据可排除、附件错误可见、移动端不溢出（任务 5、6）。

## Task 1: 本地基础设施与测试环境
Files: compose.yaml、backend/Dockerfile、backend/requirements.txt、frontend/Dockerfile、scripts/bootstrap.py。
Interfaces: PostgreSQL database zhixing、Redis redis:6379；API /api/health；Web 127.0.0.1:18080。
- [ ] 创建独立项目 compose，所有公开端口只绑定 127.0.0.1，持久卷独立。
- [ ] 初始化随机管理员、数据库密码与会话密钥，不覆盖已有 .env。
- [ ] 建立测试环境并记录预期缺失功能的失败测试。
- [ ] 验证 compose 配置，启动依赖并确认健康。

## Task 2: 演示知识包与检索
Files: knowledge_base/*、scripts/generate_knowledge.py、backend/app/knowledge.py、backend/tests/test_knowledge.py。
Interfaces: retrieve(db, query, public_only=True) -> list[dict]；ingest_document(db, document) -> int。
- [ ] 写出并运行未实现检索、内部知识隔离、下架失效和扫描件拒绝的失败测试。
- [ ] 生成带来源、审核说明的 20 份左右 Markdown 与独立评测问题，所有经营资料标明演示。
- [ ] 实现语义切片、中文词法排名、可选本地向量、版本与发布过滤。
- [ ] 验证来源、目录与测试；不能加载向量时明确降级为关键词，不伪称语义检索。

## Task 3: 数据、登录与管理接口
Files: backend/app/{config,db,models,security,admin,seed}.py、backend/tests/test_admin.py。
Interfaces: cookie admin_session；GET /api/admin/overview；文档、会话、线索、设置与审计接口。
- [ ] 写出并运行未登录、坏密码、跨站修改和越权对象的失败测试。
- [ ] 建表、管理员密码哈希、随机会话、企业归属校验、Redis 限流。
- [ ] 知识上传/编辑/发布、企业/机器人配置、线索分配跟进、分析及导出。
- [ ] 验证完整管理 API 与演示数据过滤。

## Task 4: 官网问答、分级与人工接待
Files: backend/app/{chat,scoring,llm}.py、backend/tests/test_chat.py。
Interfaces: POST /api/widget/sessions；Bearer token；messages SSE: meta/delta/done/error；contact/handoff。
- [ ] 写出并运行他人会话、未知问题、否定信号、售后、同意与幂等测试的失败用例。
- [ ] 实现 DeepSeek 原生 HTTP 适配、限定来源提示、明确摘录模式、流式事件。
- [ ] 实现多轮检索、带证据规则标签、自愿留资、验证状态、人工排队/接管/回复。
- [ ] 通过 API 测试验证持久化及真实查询；无 Key 时不进行付费调用。

## Task 5: 管理后台与官网嵌入
Files: frontend/src/*、frontend/public/widget.js、frontend/nginx.conf。
Interfaces: 后台 /；官网演示 /demo；聊天 iframe /chat；组件 /widget.js。
- [ ] 实现登录、总览、知识、咨询、线索、分析、机器人与设置页面及真实 API 调用。
- [ ] 实现独立 iframe 聊天、引用、留资、人工状态、断线错误和持久会话。
- [ ] 实现网站演示与跨站加载器、严格 postMessage origin/source 校验。
- [ ] TypeScript 检查与生产构建通过；浏览器验证桌面及手机操作。

## Task 6: 整体验证、独立审阅与交付
Files: README.md、docs/开发进度.md、scripts/smoke.py、frontend/e2e/*。
- [ ] Docker 全栈运行，后端全套测试及真实 PostgreSQL 冒烟通过。
- [ ] 浏览器验证上传发布、带来源咨询、留资、后台跟进、人工回复；保存截图。
- [ ] 独立上下文审阅代码，重要问题以回归测试修复并运行套件。
- [ ] 记录当前实现、尚待 API Key 的真实联调，以及原 10–12 周路线中仍未验证的生产条件。
