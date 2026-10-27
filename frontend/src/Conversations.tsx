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
import { connectReceptionSocket, pollMs } from "./receptionSocket";


function formatWait(seconds?: number | null) {
  if (seconds == null) return "";
  if (seconds < 60) return `${seconds} 秒`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `${m} 分 ${s} 秒` : `${m} 分钟`;
}

export function MessageBubble({
  message,
  onFeedback,
}: {
  message: any;
  onFeedback?: (id: string, rating: string) => void;
}) {
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
        {onFeedback &&
          message.role === "assistant" &&
          message.state === "completed" && (
            <div className="actions feedback-actions" aria-label="回答评价">
              <button
                className="button secondary small"
                aria-pressed={message.feedback === "up"}
                onClick={() => onFeedback(message.id, "up")}
              >
                有帮助
              </button>
              <button
                className="button secondary small"
                aria-pressed={message.feedback === "down"}
                onClick={() => onFeedback(message.id, "down")}
              >
                需改进
              </button>
              {message.feedback && <small role="status">已收到评价</small>}
            </div>
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
  const receptionStaff = useResource<any[]>("/admin/reception-staff", version);
  const canned = useResource<any[]>("/admin/canned-replies", version);
  const summary = useResource<any>("/admin/reception-summary", version);
  const [transferTo, setTransferTo] = useState("");
  const [transferNote, setTransferNote] = useState("");
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
  const [wsLive, setWsLive] = useState(false);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  useEffect(() => {
    const stop = connectReceptionSocket({
      onStatus: setWsLive,
      onEvent: (ev) => {
        list.reload();
        receptionStaff.reload();
        summary.reload();
        const sid = selectedRef.current;
        if (sid && ev.conversation_id && ev.conversation_id === sid) {
          api("/admin/conversations/" + sid)
            .then(setDetail)
            .catch(() => {});
        }
      },
    });
    return () => stop();
  }, []);
  useEffect(() => {
    const timer = setInterval(() => {
      list.reload();
      receptionStaff.reload();
      summary.reload();
    }, pollMs(wsLive, 30000, 5000));
    return () => clearInterval(timer);
  }, [wsLive]);
  useEffect(() => {
    const q = params.get("conversation_id");
    if (q) setSelected(q);
    const st = params.get("status");
    if (st && pages.filters.status !== st) pages.change("status", st);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);
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
            const last = v.messages?.at(-1);
            if (
              last &&
              v.unread_count &&
              document.visibilityState === "visible"
            )
              api(`/admin/conversations/${selected}/read`, {
                method: "POST",
                body: JSON.stringify({ message_id: last.id }),
              })
                .then(() => list.reload())
                .catch(() => {});
          }
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    setDetail(null);
    load();
    const timer = setInterval(load, pollMs(wsLive, 15000, 2500));
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [selected, version, wsLive]);
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
    const clientId =
      typeof crypto !== "undefined" && crypto.randomUUID
        ? "agent-" + crypto.randomUUID().replace(/-/g, "")
        : "agent-" + Date.now() + Math.random().toString(16).slice(2);
    try {
      await api(`/admin/conversations/${selected}/reply`, {
        method: "POST",
        body: JSON.stringify({ content: reply, client_id: clientId }),
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
          <div className="reception-pulse">
            <small>
              排队 {summary.data?.waiting ?? "—"}
              {(summary.data?.timed_out || 0) > 0
                ? ` · 超时 ${summary.data.timed_out}`
                : ""}
              {summary.data?.oldest_wait_seconds != null
                ? ` · 最长等待 ${formatWait(summary.data.oldest_wait_seconds)}`
                : ""}
              {` · 规则 ${summary.data?.assign_mode === "least_busy" ? "最少负载" : "手动领取"}`}
            </small>
          </div>
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
                  {v.unread_count > 0 && (
                    <Badge tone="amber">{v.unread_count} 未读</Badge>
                  )}
                  <small>{date(v.updated_at)}</small>
                </div>
                <p>{v.last_message}</p>
                <div className="row-between">
                  <span>{v.source_page}</span>
                  <Badge
                    tone={
                      v.timed_out
                        ? "amber"
                        : v.status === "waiting"
                          ? "amber"
                          : v.status === "human"
                            ? "green"
                            : "muted"
                    }
                  >
                    {v.timed_out
                      ? "排队超时"
                      : statusLabel[v.status]}
                    {v.status === "waiting" && v.waiting_seconds != null
                      ? ` · ${formatWait(v.waiting_seconds)}`
                      : ""}
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
                    {detail.source_page} · {statusLabel[detail.status]} ·
                    接待人：{detail.assigned_name}
                    {detail.waiting_since &&
                      ` · 排队起始 ${date(detail.waiting_since, true)}`}
                    {detail.waiting_seconds != null &&
                      ` · 已等待 ${formatWait(detail.waiting_seconds)}`}
                    {detail.timed_out && " · 已超时"}
                  </p>
                  {detail.handoff_note ? (
                    <p className="handoff-note">交接备注：{detail.handoff_note}</p>
                  ) : null}
                </div>
                <div className="actions">
                  {detail.status === "human" && canHandle && (
                    <>
                      <select
                        aria-label="转交接待人"
                        value={transferTo}
                        onChange={(e) => setTransferTo(e.target.value)}
                      >
                        <option value="">选择转交接待人</option>
                        {receptionStaff.data
                          ?.filter((s) => s.id !== detail.assigned_to_id)
                          .map((s) => (
                            <option key={s.id} value={s.id}>
                              {s.name} ·{" "}
                              {s.status === "online"
                                ? "在线"
                                : s.status === "away"
                                  ? "暂离"
                                  : "离线"}
                              {` · 接待 ${s.active_count ?? 0}/${s.max_active ?? "-"}`}
                            </option>
                          ))}
                      </select>
                      <input
                        aria-label="交接备注"
                        placeholder="交接备注（可选）"
                        value={transferNote}
                        maxLength={1000}
                        onChange={(e) => setTransferNote(e.target.value)}
                        style={{ minWidth: 140, maxWidth: 220 }}
                      />
                      <button
                        className="button secondary small"
                        disabled={busy || !transferTo}
                        onClick={async () => {
                          await action("transfer", {
                            assigned_to_id: transferTo,
                            note: transferNote,
                          });
                          setTransferNote("");
                        }}
                      >
                        转交
                      </button>
                    </>
                  )}
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
                {canHandle && detail.status === "human" && !!canned.data?.length && (
                  <div className="canned-replies" aria-label="常用回复">
                    {canned.data.map((item: any) => (
                      <button
                        key={item.id}
                        type="button"
                        className="button secondary small"
                        disabled={busy}
                        onClick={() =>
                          setReply((prev) =>
                            prev ? prev + (prev.endsWith("\n") ? "" : "\n") + item.content : item.content,
                          )
                        }
                      >
                        {item.title}
                      </button>
                    ))}
                  </div>
                )}
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
              <h3>
                判断依据{" "}
                <small>
                  规则分数 {detail.analysis?.score ?? 0} / 100 · 跟进辅助
                </small>
              </h3>
              {detail.analysis?.uncertainty && (
                <p className="footnote">
                  {detail.analysis?.confidence
                    ? `置信：${
                        (
                          {
                            high: "较高",
                            medium: "中等",
                            low: "较低",
                          } as Record<string, string>
                        )[detail.analysis.confidence] ||
                        detail.analysis.confidence
                      }。`
                    : ""}
                  {detail.analysis.uncertainty}
                </p>
              )}
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
