import { Select } from "./Select";
import { useEffect, useState, type FormEvent } from "react";
import { Activity, Bell, HardDrive, Server } from "lucide-react";
import { api, date, useResource } from "./api";
import { Badge, Empty, ErrorBox, Loading, PageTitle, useToast } from "./ui";

const severityLabel: Record<string, string> = {
  info: "提示",
  warning: "警告",
  critical: "严重",
};
const statusLabel: Record<string, string> = {
  open: "未处理",
  acked: "已确认",
  resolved: "已恢复",
};
const componentLabel: Record<string, string> = {
  api: "API",
  alert_monitor: "告警巡检",
  database: "数据库",
  redis: "Redis",
  worker: "文档 Worker",
  jobs: "任务队列",
  model: "模型调用",
  latency: "请求耗时",
  disk: "磁盘",
  backup: "完整数据库备份",
  schema: "数据库版本",
};
const componentStatusLabel: Record<string, string> = {
  ok: "正常",
  warn: "提醒",
  fail: "异常",
  unknown: "未知",
  skipped: "未启用",
  degraded: "降级",
};

function StatusDot({ status }: { status: string }) {
  const tone =
    status === "ok" || status === "skipped"
      ? "green"
      : status === "warn" || status === "unknown" || status === "degraded"
        ? "amber"
        : "red";
  return <span className={"status-dot " + tone} />;
}

export function Monitoring({ version }: { version: number }) {
  const [tab, setTab] = useState("status");
  return (
    <>
      <PageTitle
        title="运行监控与告警"
        description="巡检数据库、Redis、Worker、任务、模型、耗时、磁盘与备份；邮件通知默认关闭，配置并启用后自动发送。"
      />
      <div className="quality-tabs" role="tablist" aria-label="运维监控">
        {[
          ["status", "服务状态"],
          ["alerts", "告警列表"],
          ["notify", "通知渠道"],
        ].map(([k, v]) => (
          <button
            key={k}
            role="tab"
            aria-selected={tab === k}
            className={"button " + (tab === k ? "" : "secondary")}
            onClick={() => setTab(k)}
          >
            {v}
          </button>
        ))}
      </div>
      {tab === "status" ? (
        <StatusPanel version={version} />
      ) : tab === "alerts" ? (
        <AlertsPanel version={version} />
      ) : (
        <NotifyPanel version={version} />
      )}
    </>
  );
}

function StatusPanel({ version }: { version: number }) {
  const status = useResource<any>("/admin/ops/status", version);
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const t = setInterval(status.reload, 15000);
    return () => clearInterval(t);
  }, []);
  async function evaluate() {
    setBusy(true);
    try {
      await api("/admin/ops/evaluate", { method: "POST" });
      status.reload();
      toast("巡检已完成");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function backupCheck() {
    setBusy(true);
    try {
      const r = await api("/admin/ops/backup-check", { method: "POST" });
      status.reload();
      toast(r.ok ? "逻辑备份检查通过" : "逻辑备份检查未通过，请查看告警");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  if (status.error) return <ErrorBox text={status.error} />;
  if (status.loading && !status.data) return <Loading />;
  const data = status.data || {};
  const components = data.components || {};
  return (
    <>
      <section className="panel">
        <div className="list-toolbar">
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <StatusDot status={data.status || "unknown"} />
            <strong>
              整体状态：
              {data.status === "ok"
                ? "正常"
                : data.status === "degraded"
                  ? "降级"
                  : "异常"}
            </strong>
            <span className="muted">
              未关闭告警 {data.open_alerts ?? "-"} · 检查于{" "}
              {data.checked_at ? date(data.checked_at) : "-"}
            </span>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              className="button secondary small"
              disabled={busy}
              onClick={evaluate}
            >
              <Activity size={14} /> 立即巡检
            </button>
            <button
              className="button secondary small"
              disabled={busy}
              onClick={backupCheck}
            >
              <HardDrive size={14} /> 检查逻辑备份
            </button>
            <button className="button secondary small" onClick={status.reload}>
              刷新
            </button>
          </div>
        </div>
        <div className="metric-grid" style={{ marginTop: 16 }}>
          {Object.entries(components).map(([key, raw]) => {
            const c = raw as any;
            return (
              <div className="metric-card" key={key}>
                <div className="metric-card-header">
                  <span className="metric-card-title">
                    <StatusDot status={c.status || "unknown"} />
                    <strong>{componentLabel[key] || key}</strong>
                  </span>
                  <Badge
                    tone={
                      c.status === "ok" || c.status === "skipped"
                        ? "green"
                        : c.status === "fail"
                          ? "red"
                          : "amber"
                    }
                  >
                    {componentStatusLabel[c.status] || c.status || "未知"}
                  </Badge>
                </div>
                {key === "schema" && !c.detail ? (
                  <dl className="schema-versions">
                    <div>
                      <dt>已应用版本</dt>
                      <dd>{c.applied ?? "—"}</dd>
                    </div>
                    <div>
                      <dt>目标版本</dt>
                      <dd>{c.expected ?? "—"}</dd>
                    </div>
                  </dl>
                ) : (
                  <p className="metric-card-detail">
                    {summarizeComponent(key, c)}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </section>
      <p className="footnote">
        公开 <code>/api/health</code>{" "}
        仅返回精简检查结果；本页含故障定位证据。通知默认不外发。
      </p>
    </>
  );
}

function summarizeComponent(key: string, c: any) {
  if (key === "jobs")
    return `排队 ${c.queued ?? 0} · 处理中 ${c.running ?? 0} · 近1小时失败 ${c.failed_1h ?? 0}`;
  if (key === "model")
    return `近1小时调用 ${c.calls_1h ?? 0} · 错误 ${c.errors_1h ?? 0} · 超时 ${c.timeouts_1h ?? 0} · 错误率 ${c.error_rate ?? 0}`;
  if (key === "latency")
    return `样本 ${c.count ?? 0} · p50 ${c.p50_ms ?? "-"}ms · p95 ${c.p95_ms ?? "-"}ms · max ${c.max_ms ?? "-"}ms`;
  if (key === "disk")
    return `已用 ${c.used_pct ?? "-"}% · 剩余 ${c.free_gb ?? "-"} GB`;
  if (key === "worker")
    return c.detail || (c.updated_at ? `心跳 ${date(c.updated_at)}` : "无心跳");
  return c.detail || c.latest_file || JSON.stringify(c).slice(0, 120);
}

function AlertsPanel({ version }: { version: number }) {
  const [filter, setFilter] = useState("open");
  const list = useResource<any>(`/admin/ops/alerts?status=${filter}`, version);
  const toast = useToast();
  const [busy, setBusy] = useState("");
  useEffect(() => {
    const t = setInterval(list.reload, 15000);
    return () => clearInterval(t);
  }, [filter]);
  async function act(id: string, action: "ack" | "resolve") {
    setBusy(id + action);
    try {
      await api(`/admin/ops/alerts/${id}/${action}`, { method: "POST" });
      list.reload();
      toast(action === "ack" ? "已确认告警" : "已关闭告警");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="panel">
      <div className="list-toolbar">
        <label>
          状态
          <Select value={filter} onValueChange={(value) => setFilter(value)}>
            <option value="open">未关闭</option>
            <option value="resolved">已恢复</option>
            <option value="">全部</option>
          </Select>
        </label>
        <button className="button secondary small" onClick={list.reload}>
          <Bell size={14} /> 刷新告警
        </button>
      </div>
      {list.error ? (
        <ErrorBox text={list.error} />
      ) : list.loading && !list.data ? (
        <Loading />
      ) : !list.data?.items?.length ? (
        <Empty title="暂无告警" description="巡检正常，或告警已被关闭。" />
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>级别</th>
                <th>告警</th>
                <th>定位信息</th>
                <th>首次/最近</th>
                <th>通知</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {list.data.items.map((a: any) => (
                <tr key={a.id}>
                  <td>
                    <Badge tone={a.severity === "critical" ? "amber" : "muted"}>
                      {severityLabel[a.severity] || a.severity}
                    </Badge>
                    <div className="muted">
                      {statusLabel[a.status] || a.status}
                    </div>
                  </td>
                  <td>
                    <strong>{a.title}</strong>
                    <div className="muted">{a.code}</div>
                  </td>
                  <td style={{ maxWidth: 320 }}>
                    <div>{a.message}</div>
                    <div className="muted" style={{ fontSize: 12 }}>
                      {JSON.stringify(a.evidence || {}).slice(0, 160)}
                    </div>
                  </td>
                  <td>
                    <div>{a.first_seen_at ? date(a.first_seen_at) : "-"}</div>
                    <div className="muted">
                      {a.last_seen_at ? date(a.last_seen_at) : "-"}
                    </div>
                  </td>
                  <td>
                    {a.notify_event === "recovery" ? "恢复通知：" : ""}
                    {(
                      {
                        pending: "待发送",
                        queued: "待发送",
                        sending: "发送中",
                        sent: "邮件服务器已接受",
                        failed: "发送失败",
                        skipped: "未启用",
                      } as Record<string, string>
                    )[a.notify_state] || a.notify_state}
                    {a.notify_last_error && (
                      <div className="muted">{a.notify_last_error}</div>
                    )}
                    {a.notify_state === "failed" &&
                      a.notify_next_attempt_at && (
                        <small className="muted">
                          下次重试：{date(a.notify_next_attempt_at, true)}
                        </small>
                      )}
                  </td>
                  <td>
                    {a.status !== "resolved" && (
                      <div
                        style={{ display: "flex", gap: 6, flexWrap: "wrap" }}
                      >
                        {a.status === "open" && (
                          <button
                            className="button secondary small"
                            disabled={!!busy}
                            onClick={() => act(a.id, "ack")}
                          >
                            确认
                          </button>
                        )}
                        <button
                          className="button secondary small"
                          disabled={!!busy}
                          onClick={() => act(a.id, "resolve")}
                        >
                          关闭
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function NotifyPanel({ version }: { version: number }) {
  const cfg = useResource<any>("/admin/ops/notify", version);
  const toast = useToast();
  const [form, setForm] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (cfg.data) setForm({ ...cfg.data });
  }, [cfg.data]);
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api("/admin/ops/notify", {
        method: "PUT",
        body: JSON.stringify({
          enabled: !!form.enabled,
          channel: form.channel || "none",
          email_to: form.email_to || "",
          webhook_url: form.webhook_url || "",
        }),
      });
      setForm(r);
      cfg.reload();
      toast("通知配置已保存");
    } catch (err: any) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function sendTest() {
    setBusy(true);
    try {
      await api("/admin/ops/notify/test", { method: "POST" });
      toast("测试邮件已排队，请在告警列表中查看发送状态");
    } catch (err: any) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  }
  if (cfg.error) return <ErrorBox text={cfg.error} />;
  if (!form) return <Loading />;
  return (
    <section className="panel notify-panel" aria-labelledby="notify-title">
      <div className="notify-heading">
        <h2 id="notify-title">通知设置</h2>
        <p>配置告警接收方式，服务异常时及时通知负责人。</p>
      </div>
      <div className="notify-service-status">
        <Server size={17} aria-hidden="true" />
        <div>
          <p>{form.note}</p>
          <p>
            {form.monitor
              ? `巡检进程最近记录：${date(form.monitor.last_seen_at, true)}；状态${form.monitor.status === "ok" ? "正常" : "异常，请检查服务"}；SMTP 参数${form.monitor.smtp_configured ? "已填写" : "未填写完整"}。${form.monitor.last_error ? ` 错误类型：${form.monitor.last_error}。` : ""}`
              : "尚未收到巡检进程心跳，请检查 alert-monitor 服务。"}
          </p>
        </div>
      </div>
      <form onSubmit={save} className="stack-form notify-form">
        <label className="toggle-label">
          <input
            type="checkbox"
            checked={!!form.enabled}
            onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
          />
          启用告警通知
        </label>
        <label>
          渠道
          <Select
            value={form.channel || "none"}
            onValueChange={(value) => setForm({ ...form, channel: value })}
          >
            <option value="none">不使用</option>
            <option value="email">邮件</option>
            <option value="webhook">Webhook（预留）</option>
          </Select>
        </label>
        {form.channel === "email" && (
          <label>
            收件邮箱
            <input
              type="email"
              value={form.email_to || ""}
              onChange={(e) => setForm({ ...form, email_to: e.target.value })}
              placeholder="ops@example.com"
            />
            <small>多人接收可填写邮件组地址。发送失败后系统会自动重试。</small>
          </label>
        )}
        {form.channel === "webhook" && (
          <label>
            Webhook URL
            <input
              value={form.webhook_url || ""}
              onChange={(e) =>
                setForm({ ...form, webhook_url: e.target.value })
              }
              placeholder="https://..."
            />
            <small>Webhook 暂未接入，保存地址后不会自动发送。</small>
          </label>
        )}
        <div className="notify-actions">
          <button className="button" disabled={busy}>
            {busy ? "保存中…" : "保存通知配置"}
          </button>
          <button
            type="button"
            className="button secondary"
            onClick={sendTest}
            disabled={
              busy ||
              !cfg.data?.enabled ||
              cfg.data?.channel !== "email" ||
              !cfg.data?.email_to ||
              form.enabled !== cfg.data.enabled ||
              form.channel !== cfg.data.channel ||
              form.email_to !== cfg.data.email_to
            }
          >
            发送测试邮件
          </button>
        </div>
        <p className="notify-hint">
          先保存设置，再发送测试邮件。测试成功后可在告警列表的“全部”或“已恢复”中查看。
        </p>
      </form>
    </section>
  );
}
