import { Select } from "./Select";
import { Pager, usePaged } from "./lists";
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
  const auditList = usePaged("/admin/audit", true, version),
    audit = auditList.resource;
  const r = useResource<any>("/admin/settings", version),
    toast = useToast();
  const [form, setForm] = useState<any>(null),
    [questions, setQuestions] = useState(""),
    [embedOrigins, setEmbedOrigins] = useState(""),
    [cannedText, setCannedText] = useState(""),
    [retention, setRetention] = useState<any>(null),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    if (r.data) {
      setForm(r.data);
      setQuestions(r.data.suggested_questions.join("\n"));
      setEmbedOrigins((r.data.allowed_embed_origins || []).join("\n"));
      setCannedText(
        (r.data.canned_replies || [])
          .map((item: any) => `${item.title}\t${item.content}`)
          .join("\n"),
      );
    }
  }, [r.data]);
  useEffect(() => {
    if (robot) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await api("/admin/privacy/retention");
        if (!cancelled) setRetention(data);
      } catch {
        if (!cancelled) setRetention(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [robot, version]);
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
            privacy_notice: form.privacy_notice,
            suggested_questions: questions
              .split("\n")
              .map((q) => q.trim())
              .filter(Boolean),
            allowed_embed_origins: embedOrigins
              .split("\n")
              .map((q) => q.trim())
              .filter(Boolean),
            reception_assign_mode: form.reception_assign_mode || "least_busy",
            reception_max_active: Number(form.reception_max_active) || 5,
            reception_wait_timeout_seconds:
              Number(form.reception_wait_timeout_seconds) || 300,
            canned_replies: cannedText
              .split("\n")
              .map((line) => line.trim())
              .filter(Boolean)
              .map((line) => {
                const [title, ...rest] = line.split("\t");
                return {
                  title: (title || "").trim(),
                  content: rest.join("\t").trim() || (title || "").trim(),
                };
              })
              .filter((item) => item.title && item.content),
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
      if (!robot && retention) {
        await api("/admin/privacy/retention", {
          method: "PATCH",
          body: JSON.stringify({
            retention_conversation_days:
              Number(retention.retention_conversation_days) || 0,
            retention_lead_days: Number(retention.retention_lead_days) || 0,
            retention_audit_days: Number(retention.retention_audit_days) || 0,
            retention_backup_days: Number(retention.retention_backup_days) || 0,
          }),
        });
      }
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
                模型隐私提示
                <textarea
                  rows={4}
                  value={form.privacy_notice || ""}
                  onChange={(e) =>
                    setForm({ ...form, privacy_notice: e.target.value })
                  }
                  maxLength={1000}
                />
                <small>
                  展示在访客聊天窗口。默认文案需机构按实际数据处理与 DeepSeek
                  服务条款确认后再对外使用。
                </small>
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
              <label>
                人工分配规则
                <Select
                  value={form.reception_assign_mode || "least_busy"}
                  onValueChange={(value) =>
                    setForm({
                      ...form,
                      reception_assign_mode: value,
                    })
                  }
                >
                  <option value="least_busy">最少负载优先（默认）</option>
                  <option value="manual">手动领取</option>
                </Select>
              </label>
              <label>
                每位客服同时接待上限
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={form.reception_max_active ?? 5}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      reception_max_active: Number(e.target.value),
                    })
                  }
                />
              </label>
              <label>
                排队超时提醒（秒）
                <input
                  type="number"
                  min={30}
                  max={86400}
                  value={form.reception_wait_timeout_seconds ?? 300}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      reception_wait_timeout_seconds: Number(e.target.value),
                    })
                  }
                />
                <small>超时会话会在接待摘要与列表中标出，便于发现积压。</small>
              </label>
              <label>
                常用回复
                <textarea
                  rows={6}
                  value={cannedText}
                  onChange={(e) => setCannedText(e.target.value)}
                  placeholder={"标题\t正文内容"}
                />
                <small>
                  每行一条，标题与正文用 Tab 分隔；最多 40
                  条。客服可在咨询页一键填入。
                </small>
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
                  更改企业名称不会自动重写知识库中的机构资料，请及时审核并更新相关信息。
                </p>
              </div>
              {retention && (
                <>
                  <h3 style={{ marginTop: 16 }}>数据留存（天）</h3>
                  <p className="muted" style={{ marginBottom: 8 }}>
                    0
                    表示不自动清理。备份介质上的副本需按备份留存天数另行销毁。详见
                    docs/备份恢复与密钥轮换.md。RPO/RTO
                    须机构确认，系统不预设正式承诺。
                  </p>
                  {(
                    [
                      ["retention_conversation_days", "咨询会话"],
                      ["retention_lead_days", "线索联系方式"],
                      ["retention_audit_days", "审计详情"],
                      ["retention_backup_days", "备份文件建议"],
                    ] as const
                  ).map(([key, label]) => (
                    <label key={key}>
                      {label}
                      <input
                        type="number"
                        min={0}
                        max={3650}
                        value={retention[key]}
                        onChange={(e) =>
                          setRetention({
                            ...retention,
                            [key]: e.target.value,
                          })
                        }
                      />
                    </label>
                  ))}
                </>
              )}
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
              API Key
              由管理员在服务端配置，不在此页面展示。需要调整时，请联系系统管理员。
            </p>
          </div>
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
                将以下代码加入企业网页，即可在页面右下角打开学习顾问。请确保服务地址使用
                HTTPS，并配置允许嵌入的域名。
              </p>
              <label>
                允许嵌入域名（每行一个 Origin）
                <textarea
                  rows={4}
                  value={embedOrigins}
                  onChange={(e) => setEmbedOrigins(e.target.value)}
                  placeholder={"https://www.example.com\nhttps://example.com"}
                />
                <small>
                  须含协议与主机名，如 https://www.example.com。本站
                  PUBLIC_ORIGIN
                  始终可用。未在名单中的网站创建会话会被拒绝并记入操作记录。
                </small>
              </label>
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
              <iframe title="智能客服预览" src="/chat?preview=1" />
            </section>
          </>
        ) : (
          <section className="panel audit-panel">
            <Pager list={auditList} />
            <div className="panel-heading">
              <h2>最近操作记录</h2>
              <Badge>{audit.data?.total || 0} 条</Badge>
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
                    {audit.data?.items.map((a) => (
                      <tr key={a.id}>
                        <td>{a.actor}</td>
                        <td>{a.action}</td>
                        <td className="muted audit-target">
                          {a.target || "—"}
                          {a.details && (
                            <details>
                              <summary>查看详情</summary>
                              <p>{a.details}</p>
                            </details>
                          )}
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
