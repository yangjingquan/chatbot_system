import { useEffect, useState } from "react";
import {
  Save,
  Copy,
  ExternalLink,
  ShieldCheck,
  KeyRound,
  Code2,
  Bot,
  BookOpen,
  CheckCircle2,
} from "lucide-react";
import { api, date, useResource } from "./api";
import { Badge, ErrorBox, Loading, PageTitle, useToast } from "./ui";

export function Configuration({
  robot = false,
  version,
  onChange,
}: {
  robot?: boolean;
  version: number;
  onChange: () => void;
}) {
  const r = useResource<any>("/admin/settings", version),
    audit = useResource<any[]>("/admin/audit", version),
    toast = useToast();
  const [form, setForm] = useState<any>(null),
    [questions, setQuestions] = useState(""),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    if (r.data) {
      setForm(r.data);
      setQuestions(r.data.suggested_questions.join("\n"));
    }
  }, [r.data]);
  if (r.loading) return <Loading />;
  if (r.error) return <ErrorBox text={r.error} retry={r.reload} />;
  if (!form) return <Loading />;
  const embed = `<script src="${location.origin}/widget.js" defer></script>`;
  async function save() {
    setBusy(true);
    try {
      const payload = robot
        ? {
            bot_name: form.bot_name,
            welcome: form.welcome,
            suggested_questions: questions
              .split("\n")
              .map((q) => q.trim())
              .filter(Boolean),
          }
        : {
            company_name: form.company_name,
            description: form.description,
            service_hours: form.service_hours,
          };
      await api("/admin/settings", {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      onChange();
      toast("配置已保存，新打开的客服窗口会使用新配置");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(embed);
      toast("接入代码已复制");
    } catch {
      toast("复制失败，请手动选择接入代码", true);
    }
  }
  return (
    <>
      <PageTitle
        title={robot ? "机器人配置" : "企业设置"}
        description={
          robot
            ? "让学习顾问按照企业知识与服务边界回答。"
            : "维护对外信息，检查运行状态与操作记录。"
        }
        actions={
          <button className="button" disabled={busy} onClick={save}>
            <Save size={16} />
            {busy ? "保存中…" : "保存设置"}
          </button>
        }
      />
      <div className="configuration-grid">
        <section className="panel configuration-form">
          <div className="panel-heading">
            <h2>{robot ? "接待与欢迎设置" : "企业基本资料"}</h2>
          </div>
          {robot ? (
            <>
              <label>
                机器人名称
                <input
                  value={form.bot_name}
                  onChange={(e) =>
                    setForm({ ...form, bot_name: e.target.value })
                  }
                  maxLength={80}
                />
              </label>
              <label>
                欢迎语
                <textarea
                  rows={5}
                  value={form.welcome}
                  onChange={(e) =>
                    setForm({ ...form, welcome: e.target.value })
                  }
                  maxLength={1000}
                />
              </label>
              <label>
                推荐问题
                <textarea
                  rows={6}
                  value={questions}
                  onChange={(e) => setQuestions(e.target.value)}
                />
                <small>每行一个，最多 6 个，每个最多 150 字。</small>
              </label>
              <div className="inline-note">
                <ShieldCheck size={18} />
                <p>
                  回答依据、未知拒答、禁止保过及公开权限由服务端控制，不允许通过欢迎语解除。
                </p>
              </div>
            </>
          ) : (
            <>
              <label>
                机构名称
                <input
                  value={form.company_name}
                  maxLength={80}
                  onChange={(e) =>
                    setForm({ ...form, company_name: e.target.value })
                  }
                />
              </label>
              <label>
                机构介绍
                <textarea
                  rows={4}
                  value={form.description}
                  onChange={(e) =>
                    setForm({ ...form, description: e.target.value })
                  }
                  maxLength={500}
                />
              </label>
              <label>
                人工服务时间
                <input
                  value={form.service_hours}
                  maxLength={100}
                  onChange={(e) =>
                    setForm({ ...form, service_hours: e.target.value })
                  }
                />
              </label>
              <div className="inline-note">
                <BookOpen size={18} />
                <p>
                  更改企业名称不会自动重写知识库中的机构资料。正式使用前，请逐份替换并审核演示内容。
                </p>
              </div>
            </>
          )}
        </section>
        <section className="panel model-panel">
          <div className="panel-heading">
            <h2>模型与检索</h2>
            <Badge tone={form.model_configured ? "green" : "amber"}>
              {form.model_configured ? "Key 已配置" : "等待配置 Key"}
            </Badge>
          </div>
          <div className="model-brand">
            <div className="round-icon blue">
              <Bot size={27} />
            </div>
            <div>
              <strong>DeepSeek</strong>
              <p>{form.model}</p>
            </div>
          </div>
          <dl className="settings-dl">
            <dt>回答模式</dt>
            <dd>
              {form.model_configured
                ? "DeepSeek · 依据知识库生成"
                : "资料摘录 · 未调用大模型"}
            </dd>
            <dt>知识检索</dt>
            <dd>
              {form.retrieval.mode === "hybrid"
                ? "中文向量＋关键词"
                : "中文关键词（向量未就绪）"}
            </dd>
            <dt>向量状态</dt>
            <dd>
              {{
                ready: "已就绪",
                loading: "后台加载中",
                disabled: "未开启",
                unavailable: "下载或加载失败，已降级",
              }[form.retrieval.embedding_status as string] ||
                form.retrieval.embedding_status}
            </dd>
          </dl>
          <div className="key-notice">
            <KeyRound size={18} />
            <p>
              API Key 仅保存在服务器环境变量中，不在此页面展示或存储。修改本地
              .env 后，重新创建后端容器使配置生效。
            </p>
          </div>
          <code className="command">
            docker compose up -d --force-recreate backend
          </code>
        </section>
        {robot ? (
          <>
            <section className="panel embed-panel">
              <div className="panel-heading">
                <h2>
                  <Code2 size={18} /> 官网嵌入
                </h2>
                <button className="button secondary small" onClick={copy}>
                  <Copy size={14} />
                  复制代码
                </button>
              </div>
              <p>
                将以下代码加入企业网页，在页面右下角打开学习顾问。当前服务仅绑定本机，适用于本地预览；正式官网需先部署
                HTTPS 并配置允许嵌入的来源。
              </p>
              <pre className="code-block">{embed}</pre>
              <div className="actions">
                <a
                  className="button secondary"
                  href="/demo"
                  target="_blank"
                  rel="noreferrer"
                >
                  <ExternalLink size={15} />
                  查看实际嵌入
                </a>
                <a
                  className="text-link"
                  href="/chat"
                  target="_blank"
                  rel="noreferrer"
                >
                  独立咨询窗口
                </a>
              </div>
            </section>
            <section className="panel preview-panel">
              <div className="panel-heading">
                <h2>客服预览</h2>
                <Badge tone="blue">真实接口</Badge>
              </div>
              <iframe title="智能客服预览" src="/chat" />
            </section>
          </>
        ) : (
          <section className="panel audit-panel">
            <div className="panel-heading">
              <h2>最近操作记录</h2>
              <Badge>{audit.data?.length || 0} 条</Badge>
            </div>
            {audit.error ? (
              <ErrorBox text={audit.error} />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>操作者</th>
                      <th>操作</th>
                      <th>对象</th>
                      <th>时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {audit.data?.slice(0, 15).map((a) => (
                      <tr key={a.id}>
                        <td>{a.actor}</td>
                        <td>{a.action}</td>
                        <td className="muted audit-target">
                          {a.target || "—"}
                        </td>
                        <td className="muted nowrap">
                          {date(a.created_at, true)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        )}
      </div>
    </>
  );
}
