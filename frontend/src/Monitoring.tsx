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
  database: "数据库",
  redis: "Redis",
  worker: "文档 Worker",
  jobs: "任务队列",
  model: "模型调用",
  latency: "请求耗时",
  disk: "磁盘",
  backup: "备份",
};

function StatusDot({ status }: { status: string }) {
  const tone =
    status === "ok" || status === "skipped"
      ? "green"
      : status === "warn" || status === "unknown" || status === "degraded"
        ? "amber"
        : "amber";
  return <span className={"status-dot " + tone} />;
}

export function Monitoring({ version }: { version: number }) {
  const [tab, setTab] = useState("status");
  return (
    <>
      <PageTitle
        title="运行监控与告警"
        description="巡检 API、数据库、Redis、Worker、任务积压、模型错误、耗时、磁盘与备份结果；通知渠道默认关闭，需人工配置后才会登记外发。"
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
      toast(r.ok ? "备份检查通过" : "备份检查未通过，请查看告警");
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
              <HardDrive size={14} /> 检查备份
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
                <div className="list-toolbar" style={{ marginBottom: 6 }}>
                  <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <StatusDot status={c.status || "unknown"} />
                    <strong>{componentLabel[key] || key}</strong>
                  </span>
                  <Badge tone={c.status === "ok" || c.status === "skipped" ? "green" : "amber"}>
                    {c.status}
                  </Badge>
                </div>
                <p className="muted" style={{ fontSize: 13, margin: 0 }}>
                  {summarizeComponent(key, c)}
                </p>
              </div>
            );
          })}
        </div>
      </section>
      <p className="footnote">
        公开 <code>/api/health</code> 仅返回精简检查结果；本页含故障定位证据。通知默认不外发。
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
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="open">未关闭</option>
            <option value="resolved">已恢复</option>
            <option value="">全部</option>
          </select>
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
                    <div className="muted">{statusLabel[a.status] || a.status}</div>
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
                  <td>{a.notify_state}</td>
                  <td>
                    {a.status !== "resolved" && (
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
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
      toast("通知配置已保存（默认仍不自动外发）");
    } catch (err: any) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  }
  if (cfg.error) return <ErrorBox text={cfg.error} />;
  if (!form) return <Loading />;
  return (
    <section className="panel">
      <form onSubmit={save} className="stack-form">
        <p className="muted">
          <Server size={14} /> {form.note}
          真实邮件/企业微信等外发需机构自行配置 SMTP 或 Webhook 并完成验收。
        </p>
        <label className="toggle-label">
          <input
            type="checkbox"
            checked={!!form.enabled}
            onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
          />
          启用通知登记（本版仅登记待发送，不强制外发）
        </label>
        <label>
          渠道
          <select
            value={form.channel || "none"}
            onChange={(e) => setForm({ ...form, channel: e.target.value })}
          >
            <option value="none">不使用</option>
            <option value="email">邮件（预留）</option>
            <option value="webhook">Webhook（预留）</option>
          </select>
        </label>
        <label>
          收件邮箱
          <input
            value={form.email_to || ""}
            onChange={(e) => setForm({ ...form, email_to: e.target.value })}
            placeholder="ops@example.com"
          />
        </label>
        <label>
          Webhook URL
          <input
            value={form.webhook_url || ""}
            onChange={(e) => setForm({ ...form, webhook_url: e.target.value })}
            placeholder="https://..."
          />
        </label>
        <button className="button" disabled={busy}>
          {busy ? "保存中…" : "保存通知配置"}
        </button>
      </form>
    </section>
  );
}
