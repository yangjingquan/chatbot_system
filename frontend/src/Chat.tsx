import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  GraduationCap,
  Send,
  Headphones,
  X,
  Plus,
  UserRound,
  ShieldCheck,
  ChevronRight,
  Loader2,
} from "lucide-react";
import { api } from "./api";
import { Badge, ErrorBox } from "./ui";
import { MessageBubble } from "./Conversations";
import {
  consumeEvents,
  restoreSession,
  type Credential,
} from "./chatTransport";

export function Chat() {
  const [attempt, setAttempt] = useState(0);
  const [config, setConfig] = useState<any>(null),
    [credential, setCredential] = useState<Credential | null>(null),
    [messages, setMessages] = useState<any[]>([]),
    [status, setStatus] = useState("bot");
  const [question, setQuestion] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [contactOpen, setContactOpen] = useState(false),
    [hasContact, setHasContact] = useState(false),
    [submitted, setSubmitted] = useState(false);
  const [name, setName] = useState(""),
    [contact, setContact] = useState(""),
    [consent, setConsent] = useState(false),
    [contactBusy, setContactBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null),
    busyRef = useRef(false),
    init = useRef<Promise<any> | null>(null);
  const parentOrigin = new URLSearchParams(location.search).get("parentOrigin");
  const embedded = window.parent !== window;
  const tokenHeaders = (c: Credential) => ({
    Authorization: "Bearer " + c.token,
  });
  function persist(c: Credential) {
    if (embedded && parentOrigin)
      window.parent.postMessage(
        { type: "zhixing:save", session: c },
        parentOrigin,
      );
    else
      try {
        localStorage.setItem("zhixing_session", JSON.stringify(c));
      } catch {}
  }
  async function create() {
    const c = await api<Credential>("/widget/sessions", {
      method: "POST",
      body: JSON.stringify({ source_page: embedded ? "官网嵌入" : "官网演示" }),
    });
    persist(c);
    return c;
  }
  useEffect(() => {
    let active = true;
    if (!init.current)
      init.current = (async () => {
        const conf = await api("/widget/config");
        let saved: any = null;
        if (embedded && parentOrigin) {
          saved = await new Promise((resolve) => {
            let settled = false;
            const handler = (event: MessageEvent) => {
              if (
                event.origin === parentOrigin &&
                event.source === window.parent &&
                event.data?.type === "zhixing:init"
              ) {
                settled = true;
                window.removeEventListener("message", handler);
                resolve(event.data.session);
              }
            };
            window.addEventListener("message", handler);
            window.parent.postMessage({ type: "zhixing:ready" }, parentOrigin);
            setTimeout(() => {
              window.removeEventListener("message", handler);
              if (!settled) resolve(null);
            }, 2000);
          });
        } else {
          if (embedded) {
            try {
              if (window.parent.location.origin !== location.origin)
                throw Error();
            } catch {
              throw new Error("请使用工作台提供的正式接入脚本打开客服");
            }
          }
          try {
            saved = JSON.parse(
              localStorage.getItem("zhixing_session") || "null",
            );
          } catch {}
        }
        const savedCredential =
          saved &&
          typeof saved.id === "string" &&
          typeof saved.token === "string"
            ? saved
            : null;
        const restored = await restoreSession(
          savedCredential,
          (c) => api(`/widget/sessions/${c.id}`, { headers: tokenHeaders(c) }),
          create,
        );
        return { conf, c: restored.credential, history: restored.history };
      })();
    init.current
      .then(({ conf, c, history }) => {
        if (active) {
          setConfig(conf);
          setCredential(c);
          setMessages(history?.messages || []);
          setStatus(history?.status || "bot");
          setHasContact(history?.has_contact || false);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [attempt]);
  useEffect(() => {
    if (!credential) return;
    const c = credential;
    const timer = setInterval(() => {
      api(`/widget/sessions/${c.id}`, { headers: tokenHeaders(c) })
        .then((h) => {
          setStatus(h.status);
          setHasContact(h.has_contact);
          if (!busyRef.current) setMessages(h.messages);
        })
        .catch(() => {});
    }, 3500);
    return () => clearInterval(timer);
  }, [credential]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest", behavior: "auto" });
  }, [messages, messages[messages.length - 1]?.content, contactOpen]);
  async function send(text = question) {
    if (!text.trim() || !credential || busy) return;
    setError("");
    setQuestion("");
    setBusy(true);
    busyRef.current = true;
    const id = crypto.randomUUID(),
      assistantId = crypto.randomUUID();
    setMessages((old) => [
      ...old,
      { id, role: "user", content: text, created_at: new Date().toISOString() },
    ]);
    let added = false;
    try {
      const response = await fetch(
        `/api/widget/sessions/${credential.id}/messages`,
        {
          method: "POST",
          headers: {
            ...tokenHeaders(credential),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ content: text, client_id: id }),
        },
      );
      if (!response.ok) {
        const result = await response.json();
        throw new Error(result.detail || "发送失败，请稍后重试");
      }
      if (!response.body) throw new Error("浏览器不支持流式响应，请升级浏览器");
      await consumeEvents(response.body, (kind, item) => {
        if (kind === "meta") {
          added = true;
          setMessages((old) => [
            ...old,
            {
              id: assistantId,
              role: "assistant",
              content: "",
              sources: item.sources,
              mode: item.mode,
              created_at: new Date().toISOString(),
            },
          ]);
        }
        if (kind === "delta") {
          if (!added) {
            added = true;
            setMessages((old) => [
              ...old,
              { id: assistantId, role: "assistant", content: item.text },
            ]);
          } else
            setMessages((old) =>
              old.map((m) =>
                m.id === assistantId
                  ? { ...m, content: m.content + item.text }
                  : m,
              ),
            );
        }
        if (kind === "done") setStatus(item.status || "bot");
        if (kind === "error") {
          setError(item.message);
          setMessages((old) =>
            old.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    state: "failed",
                    content: m.content || "回答暂时未能完成。",
                  }
                : m,
            ),
          );
        }
      });
    } catch (e: any) {
      setError(e.message);
      if (!added) setQuestion(text);
      else
        setMessages((old) =>
          old.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  state: "failed",
                  content: m.content || "回答暂时未能完成。",
                }
              : m,
          ),
        );
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function handoff() {
    if (!credential) return;
    try {
      await api(`/widget/sessions/${credential.id}/handoff`, {
        method: "POST",
        headers: tokenHeaders(credential),
      });
      setStatus("waiting");
    } catch (e: any) {
      setError(e.message);
    }
  }
  async function submitContact(e: FormEvent) {
    e.preventDefault();
    if (!credential) return;
    setContactBusy(true);
    setError("");
    try {
      await api(`/widget/sessions/${credential.id}/contact`, {
        method: "POST",
        headers: tokenHeaders(credential),
        body: JSON.stringify({ name, contact, consent }),
      });
      setHasContact(true);
      setSubmitted(true);
      setContactOpen(false);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setContactBusy(false);
    }
  }
  async function newChat() {
    if (busy) return;
    try {
      const c = await create();
      setCredential(c);
      setMessages([]);
      setStatus("bot");
      setHasContact(false);
      setSubmitted(false);
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  }
  return (
    <div className="chat-page">
      <header className="chat-header">
        <div className="chat-brand">
          <span className="chat-logo">
            <GraduationCap size={23} />
          </span>
          <div>
            <strong>{config?.bot_name || "知行学习顾问"}</strong>
            <small>
              <span className="status-dot green" />
              {status === "human"
                ? "人工顾问接待中"
                : status === "waiting"
                  ? "已登记人工接待"
                  : status === "closed"
                    ? "咨询已结束"
                    : "企业知识库支持"}
            </small>
          </div>
        </div>
        <div className="actions">
          <button
            className="chat-icon"
            aria-label="新建咨询"
            title="新建咨询"
            disabled={busy}
            onClick={newChat}
          >
            <Plus size={20} />
          </button>
          {embedded && parentOrigin && (
            <button
              className="chat-icon"
              aria-label="关闭客服"
              onClick={() =>
                window.parent.postMessage(
                  { type: "zhixing:close" },
                  parentOrigin,
                )
              }
            >
              <X size={20} />
            </button>
          )}
        </div>
      </header>
      <div className="chat-mode">
        <ShieldCheck size={13} />
        {config?.mode === "deepseek"
          ? "DeepSeek · 依据公开知识回答"
          : "资料摘录模式 · 尚未调用大模型"}
        <span>AI 服务</span>
      </div>
      <div className="chat-transcript" role="log" aria-live="polite">
        <div className="welcome-block">
          <div className="welcome-symbol">
            <GraduationCap size={27} />
          </div>
          <h1>您好，今天想了解什么？</h1>
          <p>{config?.welcome || "正在连接企业知识库…"}</p>
          {!messages.length && (
            <div className="suggested-list">
              {config?.suggested_questions?.map((q: string) => (
                <button
                  key={q}
                  disabled={busy || !credential}
                  onClick={() => send(q)}
                >
                  {q}
                  <ChevronRight size={15} />
                </button>
              ))}
            </div>
          )}
        </div>
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
        {busy && (
          <div className="typing" role="status">
            <Loader2 size={15} className="spin" />
            正在检索与组织回答…
          </div>
        )}
        {status === "waiting" && (
          <div className="chat-notice">
            <Headphones size={17} />
            <p>
              已登记人工接待请求，您可以继续补充需求。暂无法保证等待时长，离开前可自愿留下联系方式。
            </p>
          </div>
        )}
        {submitted && (
          <div className="chat-notice green-text">
            <ShieldCheck size={17} />
            <p>已记录联系请求，顾问核实后会就本次咨询回复。</p>
          </div>
        )}
        {contactOpen && (
          <form className="contact-form" onSubmit={submitContact}>
            <div className="row-between">
              <h3>请顾问联系我</h3>
              <button
                type="button"
                className="icon-button"
                aria-label="关闭留资表单"
                onClick={() => setContactOpen(false)}
              >
                <X size={16} />
              </button>
            </div>
            <label>
              您的称呼
              <input
                required
                value={name}
                maxLength={80}
                onChange={(e) => setName(e.target.value)}
                placeholder="方便我们如何称呼您"
              />
            </label>
            <label>
              手机号或邮箱
              <input
                required
                value={contact}
                maxLength={160}
                onChange={(e) => setContact(e.target.value)}
                placeholder="请使用合成信息测试"
              />
            </label>
            <label className="checkbox-line">
              <input
                type="checkbox"
                required
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
              />
              <span>{config?.contact_notice}</span>
            </label>
            <button className="button full" disabled={!consent || contactBusy}>
              {contactBusy ? "正在提交…" : "提交联系请求"}
            </button>
            <small>您也可以不留联系方式，继续咨询公开课程信息。</small>
          </form>
        )}
        {error && (
          <ErrorBox
            text={error}
            retry={
              !credential
                ? () => {
                    init.current = null;
                    setError("");
                    setAttempt((v) => v + 1);
                  }
                : undefined
            }
          />
        )}
        <div ref={bottom} />
      </div>
      <div className="chat-compose">
        <div className="chat-tools">
          <button
            disabled={!credential || status === "human" || status === "waiting"}
            onClick={handoff}
          >
            <Headphones size={14} />
            转人工
          </button>
          <button
            disabled={!credential}
            onClick={() => setContactOpen((v) => !v)}
          >
            <UserRound size={14} />
            {hasContact ? "更新联系信息" : "请顾问联系我"}
          </button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <textarea
            aria-label="咨询问题"
            maxLength={2000}
            rows={2}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={
              status === "closed"
                ? "咨询已结束，请点击右上角新建"
                : "例如：想了解公安联考课程和试听安排"
            }
            disabled={busy || status === "closed" || !credential}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button
            className="chat-send"
            aria-label="发送问题"
            disabled={
              busy || !question.trim() || !credential || status === "closed"
            }
          >
            <Send size={18} />
          </button>
        </form>
        <small>经营资料为演示设定 · 考试政策以官方公告为准</small>
      </div>
    </div>
  );
}
