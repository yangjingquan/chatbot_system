import { useEffect, useRef, useState } from "react";
import {
  Search,
  Send,
  Headphones,
  Bot,
  MessageSquare,
  UserRound,
  ArrowRight,
  CircleCheck,
} from "lucide-react";
import { api, date, statusLabel, useResource } from "./api";
import { useAccess } from "./auth";
import { useSearchParams } from "react-router-dom";
import { Filters, Pager, usePaged } from "./lists";
import {
  Badge,
  Empty,
  ErrorBox,
  Level,
  Loading,
  PageTitle,
  useToast,
} from "./ui";

export function MessageBubble({ message }: { message: any }) {
  const user = message.role === "user";
  return (
    <div className={"message " + (user ? "from-user" : "from-assistant")}>
      <span className="message-avatar">
        {user ? (
          <UserRound size={16} />
        ) : message.role === "agent" ? (
          <Headphones size={16} />
        ) : (
          <Bot size={16} />
        )}
      </span>
      <div className="message-body">
        <div className="message-meta">
          <strong>
            {user ? "访客" : message.role === "agent" ? "人工顾问" : "学习顾问"}
          </strong>
          <small>
            {message.created_at ? date(message.created_at, true) : ""}
          </small>
          {message.mode === "extractive" && (
            <Badge tone="muted">资料摘录</Badge>
          )}
          {message.mode === "deepseek" && <Badge tone="blue">DeepSeek</Badge>}
        </div>
        <div className="bubble">{message.content || "正在准备回答…"}</div>
        {message.sources?.length > 0 && (
          <details className="source-details">
            <summary>查看 {message.sources.length} 个知识来源</summary>
            {message.sources.map((s: any, i: number) => (
              <div key={s.chunk_id || i}>
                <strong>
                  [{i + 1}] {s.title}
                </strong>
                <small>
                  版本 {s.version}
                  {s.exam_year ? ` · ${s.exam_year}年度` : ""}
                  {s.region ? ` · ${s.region}` : ""}
                  {s.is_demo ? " · 演示资料" : ""}
                </small>
                <p>{s.text}</p>
              </div>
            ))}
          </details>
        )}
        {message.state === "failed" && (
          <small className="red-text">回答未完成，请重试或转人工。</small>
        )}
      </div>
    </div>
  );
}

export function Conversations({
  includeDemo,
  version,
}: {
  includeDemo: boolean;
  version: number;
}) {
  const { user, can } = useAccess();
  const pages = usePaged("/admin/conversations", includeDemo, version),
    list = pages.resource;
  const owners = useResource<any[]>("/admin/filter-options", version);
  const [params] = useSearchParams();
  const [selected, setSelected] = useState(params.get("conversation_id") || ""),
    [detail, setDetail] = useState<any>(null),
    [reply, setReply] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const toast = useToast(),
    bottom = useRef<HTMLDivElement>(null);
  const rows = list.data?.items || [];
  const canHandle =
    can("conversations.handle") &&
    (!detail?.assigned_to_id ||
      detail.assigned_to_id === user?.id ||
      user?.role === "admin");
  useEffect(() => {
    if (!selected && rows.length) setSelected(rows[0].id);
  }, [list.data, selected]);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    const load = () =>
      api("/admin/conversations/" + selected)
        .then((v) => {
          if (active) {
            setDetail(v);
            setError("");
          }
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    setDetail(null);
    load();
    const timer = setInterval(load, 3500);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [selected, version]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [detail?.messages?.length]);
  async function action(path: string, body?: any) {
    setBusy(true);
    try {
      await api(`/admin/conversations/${selected}/${path}`, {
        method: "POST",
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      setDetail(await api("/admin/conversations/" + selected));
      list.reload();
      toast("接待状态已更新");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function send() {
    if (!reply.trim()) return;
    setBusy(true);
    try {
      await api(`/admin/conversations/${selected}/reply`, {
        method: "POST",
        body: JSON.stringify({ content: reply }),
      });
      setReply("");
      setDetail(await api("/admin/conversations/" + selected));
      list.reload();
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        title="咨询会话"
        description="查看完整咨询与回答依据，必要时接管人工服务。"
      />
      <section className="panel">
        <Filters
          list={pages}
          owners={owners.data || []}
          placeholder="搜索访客、问题或来源页面"
        />
      </section>
      <div className="conversation-workspace">
        <aside className="conversation-list">
          <div className="conversation-filters">
            <select
              aria-label="接待状态"
              value={pages.filters.status || ""}
              onChange={(e) => pages.change("status", e.target.value)}
            >
              <option value="">全部接待状态</option>
              {["bot", "waiting", "human", "closed"].map((v) => (
                <option value={v} key={v}>
                  {statusLabel[v]}
                </option>
              ))}
            </select>
          </div>
          {list.loading ? (
            <Loading />
          ) : list.error ? (
            <ErrorBox text={list.error} retry={list.reload} />
          ) : rows.length ? (
            rows.map((v) => (
              <button
                key={v.id}
                className={
                  "conversation-item " + (selected === v.id ? "active" : "")
                }
                onClick={() => setSelected(v.id)}
              >
                <div className="row-between">
                  <strong>{v.visitor_name}</strong>
                  <small>{date(v.updated_at)}</small>
                </div>
                <p>{v.last_message}</p>
                <div className="row-between">
                  <span>{v.source_page}</span>
                  <Badge
                    tone={
                      v.status === "waiting"
                        ? "amber"
                        : v.status === "human"
                          ? "green"
                          : "muted"
                    }
                  >
                    {statusLabel[v.status]}
                  </Badge>
                </div>
                {v.is_demo && (
                  <small className="sample-label">合成演示会话</small>
                )}
              </button>
            ))
          ) : (
            <Empty
              title="暂无匹配会话"
              description="可从官网演示发起新咨询。"
            />
          )}
          <Pager list={pages} />
        </aside>
        <section className="conversation-main">
          {error ? (
            <ErrorBox text={error} />
          ) : !selected ? (
            <Empty
              title="选择一条咨询"
              description="对话、引用和接待操作将在这里展示。"
            />
          ) : !detail ? (
            <Loading />
          ) : (
            <>
              <div className="conversation-header">
                <div>
                  <h2>
                    {detail.visitor_name}{" "}
                    {detail.is_demo && <Badge tone="muted">演示</Badge>}
                  </h2>
                  <p>
                    {detail.source_page} · {statusLabel[detail.status]}
                  </p>
                </div>
                <div className="actions">
                  {detail.status !== "human" && canHandle && (
                    <button
                      className="button small"
                      disabled={busy}
                      onClick={() => action("takeover")}
                    >
                      <Headphones size={15} />
                      人工接管
                    </button>
                  )}
                  {detail.status === "human" && canHandle && (
                    <button
                      className="button secondary small"
                      disabled={busy}
                      onClick={() => action("status", { status: "bot" })}
                    >
                      <Bot size={15} />
                      转回机器人
                    </button>
                  )}
                  {detail.status !== "closed" && canHandle && (
                    <button
                      className="icon-button"
                      aria-label="结束咨询"
                      disabled={busy}
                      onClick={() => action("status", { status: "closed" })}
                    >
                      <CircleCheck size={20} />
                    </button>
                  )}
                </div>
              </div>
              <div className="conversation-messages">
                {detail.messages.map((m: any) => (
                  <MessageBubble message={m} key={m.id} />
                ))}
                <div ref={bottom} />
              </div>
              <div className="reply-area">
                <label className="sr-only" htmlFor="agent-reply">
                  人工回复
                </label>
                <textarea
                  id="agent-reply"
                  value={reply}
                  disabled={detail.status !== "human" || busy || !canHandle}
                  onChange={(e) => setReply(e.target.value)}
                  placeholder={
                    !canHandle
                      ? "此会话正由其他员工接待"
                      : detail.status === "human"
                        ? "输入人工回复，Enter 发送，Shift+Enter 换行"
                        : "接管咨询后可以发送人工回复"
                  }
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send();
                    }
                  }}
                />
                <div className="row-between">
                  <small>
                    {detail.status === "human"
                      ? "机器人已暂停自动回复"
                      : "机器人接待与人工接待分别管理"}
                  </small>
                  <button
                    className="button small"
                    disabled={
                      detail.status !== "human" ||
                      !reply.trim() ||
                      busy ||
                      !canHandle
                    }
                    onClick={send}
                  >
                    <Send size={15} />
                    发送回复
                  </button>
                </div>
              </div>
            </>
          )}
        </section>
        <aside className="conversation-profile">
          {detail ? (
            <>
              <h3>需求摘要</h3>
              <Level value={detail.analysis?.level} />
              <dl>
                <dt>咨询目的</dt>
                <dd>{detail.analysis?.purpose || "待判定"}</dd>
                <dt>购买意愿</dt>
                <dd>
                  {(
                    {
                      interested: "有明确咨询信号",
                      exploring: "了解中",
                      declined: "暂不考虑",
                      unknown: "尚未明确",
                    } as Record<string, string>
                  )[detail.analysis?.purchase_state] || "尚未明确"}
                </dd>
                <dt>联系状态</dt>
                <dd>
                  {detail.lead && !detail.lead.contact_allowed
                    ? "已停止联系"
                    : detail.lead
                      ? "已留资 · " +
                        (detail.lead.contact_verified ? "人工核验" : "待核验")
                      : "未留资"}
                </dd>
                <dt>线索负责人</dt>
                <dd>{detail.lead?.owner || "未分配"}</dd>
              </dl>
              {detail.analysis?.needs?.length > 0 && (
                <>
                  <h3>各项需求</h3>
                  <div className="need-list">
                    {detail.analysis.needs.map((n: any, i: number) => (
                      <div key={i}>
                        <strong>{n.exam_type}</strong>
                        <small>
                          {n.purpose} ·{" "}
                          {n.state === "declined"
                            ? "已取消"
                            : n.state === "switched"
                              ? "已转考"
                              : n.level || "待处理"}
                        </small>
                      </div>
                    ))}
                  </div>
                </>
              )}
              <h3>关注标签</h3>
              <div className="tag-wrap">
                {detail.analysis?.tags?.length ? (
                  detail.analysis.tags.map((t: string) => (
                    <Badge key={t}>{t}</Badge>
                  ))
                ) : (
                  <span className="muted">暂无足够信息</span>
                )}
              </div>
              <h3>判断依据</h3>
              {detail.analysis?.evidence?.length ? (
                detail.analysis.evidence.map((e: any, i: number) => (
                  <blockquote className="evidence" key={i}>
                    <strong>{e.label}</strong>
                    <p>“{e.quote}”</p>
                  </blockquote>
                ))
              ) : (
                <p className="footnote">
                  信息不足时保持待判定，不猜测学员背景。
                </p>
              )}
              {detail.lead && (
                <a className="text-link" href="/leads">
                  查看线索跟进 <ArrowRight size={14} />
                </a>
              )}
            </>
          ) : (
            <div className="profile-placeholder">
              <MessageSquare size={30} />
              <p>了解需求，有据跟进</p>
            </div>
          )}
        </aside>
      </div>
    </>
  );
}
