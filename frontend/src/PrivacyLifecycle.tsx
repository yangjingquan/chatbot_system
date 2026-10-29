import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, Clock3, Play, Search, ShieldCheck } from "lucide-react";
import { api, date } from "./api";
import { Badge, Empty, ErrorBox, Loading, useToast } from "./ui";

const statusText: Record<string, string> = {
  open: "待处理",
  needs_review: "待核实",
  completed: "已完成",
  rejected: "已拒绝",
};

const kindText: Record<string, string> = {
  withdraw_contact: "撤回联系许可",
  anonymize: "匿名化",
  delete: "删除个人信息",
};

function maskContact(value = "") {
  if (value.includes("@")) {
    const [local, domain] = value.split("@");
    return `${local.slice(0, 1)}***@${domain}`;
  }
  const digits = value.replace(/\D/g, "");
  return digits.length >= 7 ? `${digits.slice(0, 3)}****${digits.slice(-4)}` : "联系方式已登记";
}

function badgeTone(status: string) {
  if (status === "ok" || status === "completed") return "green";
  if (status === "fail" || status === "rejected") return "red";
  if (status === "needs_review" || status === "stale" || status === "open") return "amber";
  return "muted";
}

export function PrivacyLifecycle({ version }: { version: number }) {
  const toast = useToast();
  const [retention, setRetention] = useState<any>(null);
  const [retentionStatus, setRetentionStatus] = useState<any>(null);
  const [retentionBusy, setRetentionBusy] = useState(false);
  const [retentionError, setRetentionError] = useState("");
  const [requests, setRequests] = useState<any[]>([]);
  const [requestStatus, setRequestStatus] = useState("");
  const [requestLoading, setRequestLoading] = useState(true);
  const [requestError, setRequestError] = useState("");
  const [form, setForm] = useState({
    kind: "delete",
    subject_name: "",
    contact: "",
    note: "",
    lead_id: "",
    conversation_id: "",
  });
  const [lookup, setLookup] = useState("");
  const [matches, setMatches] = useState<any[]>([]);
  const [lookupBusy, setLookupBusy] = useState(false);
  const [requestMatches, setRequestMatches] = useState<Record<string, any[]>>({});
  const [requestTargets, setRequestTargets] = useState<Record<string, any>>({});
  const [requestSearch, setRequestSearch] = useState<Record<string, string>>({});
  const [requestBusy, setRequestBusy] = useState("");
  const [confirmAction, setConfirmAction] = useState("");
  const [rejectReason, setRejectReason] = useState<Record<string, string>>({});

  const loadRetention = useCallback(async () => {
    try {
      const [settings, status] = await Promise.all([
        api<any>("/admin/privacy/retention"),
        api<any>("/admin/privacy/retention/status"),
      ]);
      setRetention(settings);
      setRetentionStatus(status);
      setRetentionError("");
    } catch (error: any) {
      setRetentionError(error.message || "留存状态读取失败");
    }
  }, []);

  const loadRequests = useCallback(async () => {
    setRequestLoading(true);
    setRequestError("");
    try {
      const query = requestStatus ? `?status=${encodeURIComponent(requestStatus)}` : "";
      const result = await api<any>(`/admin/privacy/requests${query}`);
      setRequests(result.items || []);
    } catch (error: any) {
      setRequestError(error.message || "隐私请求读取失败");
    } finally {
      setRequestLoading(false);
    }
  }, [requestStatus]);

  useEffect(() => {
    void loadRetention();
  }, [loadRetention, version]);

  useEffect(() => {
    void loadRequests();
  }, [loadRequests, version]);

  useEffect(() => {
    const timer = window.setInterval(() => void loadRetention(), 60_000);
    return () => window.clearInterval(timer);
  }, [loadRetention]);

  async function saveRetention() {
    if (!retention) return;
    setRetentionBusy(true);
    try {
      const saved = await api<any>("/admin/privacy/retention", {
        method: "PATCH",
        body: JSON.stringify({
          retention_conversation_days: Number(retention.retention_conversation_days),
          retention_lead_days: Number(retention.retention_lead_days),
          retention_audit_days: Number(retention.retention_audit_days),
          retention_backup_days: Number(retention.retention_backup_days),
        }),
      });
      setRetention(saved);
      toast("留存策略已保存");
    } catch (error: any) {
      toast(error.message, true);
    } finally {
      setRetentionBusy(false);
    }
  }

  async function runRetention() {
    setRetentionBusy(true);
    try {
      await api("/admin/privacy/retention/run", { method: "POST", body: "{}" });
      await loadRetention();
      toast("留存清理完成，运行结果已记录");
    } catch (error: any) {
      await loadRetention();
      toast(error.message, true);
    } finally {
      setRetentionBusy(false);
    }
  }

  async function searchLeads(query: string) {
    const value = query.trim();
    if (!value) {
      toast("请输入姓名或完整联系方式后查找", true);
      return [];
    }
    const result = await api<any>(`/admin/leads?q=${encodeURIComponent(value)}&page=1&page_size=8`);
    return result.items || [];
  }

  async function searchForNewRequest() {
    setLookupBusy(true);
    try {
      const found = await searchLeads(lookup);
      setMatches(found);
      if (!found.length) toast("没有找到匹配线索，可先登记后核实");
    } catch (error: any) {
      toast(error.message, true);
    } finally {
      setLookupBusy(false);
    }
  }

  function chooseForNewRequest(lead: any) {
    setForm((current) => ({
      ...current,
      subject_name: lead.name || current.subject_name,
      contact: lead.contact || current.contact,
      lead_id: lead.id,
      conversation_id: lead.conversation_id,
    }));
    setMatches([]);
    setLookup("");
  }

  async function createRequest() {
    setRequestBusy("create");
    try {
      await api("/admin/privacy/requests", {
        method: "POST",
        body: JSON.stringify({
          ...form,
          lead_id: form.lead_id || null,
          conversation_id: form.conversation_id || null,
        }),
      });
      setForm({ kind: "delete", subject_name: "", contact: "", note: "", lead_id: "", conversation_id: "" });
      setMatches([]);
      await loadRequests();
      toast("隐私请求已登记");
    } catch (error: any) {
      toast(error.message, true);
    } finally {
      setRequestBusy("");
    }
  }

  async function searchForRequest(request: any) {
    const query = requestSearch[request.id] ?? request.subject_name ?? "";
    setRequestBusy(`search:${request.id}`);
    try {
      const found = await searchLeads(query);
      setRequestMatches((current) => ({ ...current, [request.id]: found }));
      if (!found.length) toast("没有找到匹配线索，请按授权核实后重试");
    } catch (error: any) {
      toast(error.message, true);
    } finally {
      setRequestBusy("");
    }
  }

  function chooseForRequest(request: any, lead: any) {
    setRequestTargets((current) => ({
      ...current,
      [request.id]: { lead_id: lead.id, conversation_id: lead.conversation_id, lead },
    }));
    setRequestMatches((current) => ({ ...current, [request.id]: [] }));
    setConfirmAction("");
  }

  async function completeRequest(request: any) {
    const selected = requestTargets[request.id];
    const target = selected || { lead_id: request.lead_id, conversation_id: request.conversation_id };
    if (!target.lead_id && !target.conversation_id) {
      toast("请先查找并核实关联线索", true);
      return;
    }
    if (request.kind === "withdraw_contact" && !target.lead_id) {
      toast("撤回联系许可需要关联线索", true);
      return;
    }
    if (confirmAction !== `complete:${request.id}`) {
      setConfirmAction(`complete:${request.id}`);
      return;
    }
    setRequestBusy(request.id);
    try {
      await api(`/admin/privacy/requests/${request.id}/complete`, {
        method: "POST",
        body: JSON.stringify({ lead_id: target.lead_id, conversation_id: target.conversation_id }),
      });
      setConfirmAction("");
      setRequestTargets((current) => {
        const next = { ...current };
        delete next[request.id];
        return next;
      });
      await loadRequests();
      toast("请求已处理，处理范围已记录");
    } catch (error: any) {
      toast(error.message, true);
    } finally {
      setRequestBusy("");
    }
  }

  async function rejectRequest(request: any) {
    const reason = (rejectReason[request.id] || "").trim();
    if (!reason) {
      toast("请填写拒绝原因", true);
      return;
    }
    if (confirmAction !== `reject:${request.id}`) {
      setConfirmAction(`reject:${request.id}`);
      return;
    }
    setRequestBusy(request.id);
    try {
      await api(`/admin/privacy/requests/${request.id}/complete`, {
        method: "POST",
        body: JSON.stringify({ reject: true, resolution: reason }),
      });
      setConfirmAction("");
      await loadRequests();
      toast("请求已标记为拒绝，原因已记录");
    } catch (error: any) {
      toast(error.message, true);
    } finally {
      setRequestBusy("");
    }
  }

  const status = retentionStatus?.status || "unknown";
  const statusSummary = status === "ok"
    ? `最近运行 ${date(retentionStatus.run_at, true)} · 清理会话 ${retentionStatus.conversations || 0}、线索 ${retentionStatus.leads || 0}、审计详情 ${retentionStatus.audits || 0} 条`
    : status === "fail"
      ? `最近运行失败 ${retentionStatus.run_at ? `· ${date(retentionStatus.run_at, true)}` : ""} · ${retentionStatus.last_error || "请检查服务日志"}`
      : status === "stale"
        ? `自动清理记录已过期 ${retentionStatus.run_at ? `· ${date(retentionStatus.run_at, true)}` : ""}`
        : "尚无运行记录；独立巡检进程会按计划执行清理。";

  return (
    <div className="privacy-lifecycle">
      <section className="panel privacy-retention-panel">
        <div className="panel-heading">
          <div>
            <h2>数据留存与清理</h2>
            <p className="muted">后台巡检进程默认每小时检查一次；设为 0 的类别不会自动清理。</p>
          </div>
          <Badge tone={badgeTone(status)}>{status === "ok" ? "运行正常" : status === "fail" ? "运行失败" : status === "stale" ? "记录过期" : "尚无记录"}</Badge>
        </div>
        {retentionError ? <ErrorBox text={retentionError} retry={loadRetention} /> : !retention ? <Loading /> : (
          <>
            <div className="privacy-retention-fields">
              {([
                ["retention_conversation_days", "咨询会话"],
                ["retention_lead_days", "线索联系方式"],
                ["retention_audit_days", "审计详情"],
                ["retention_backup_days", "备份保留建议"],
              ] as const).map(([key, label]) => (
                <label key={key}>{label}（天）<input type="number" min={0} max={3650} value={retention[key]} onChange={(event) => setRetention({ ...retention, [key]: event.target.value })} /></label>
              ))}
            </div>
            <div className="privacy-retention-status" role="status">
              <Clock3 size={17} />
              <div><strong>{statusSummary}</strong>{retentionStatus?.detail && <small>{retentionStatus.detail}</small>}</div>
            </div>
            <p className="muted privacy-retention-note">
              本地数据库的留存清理由巡检进程执行。物理备份和异地副本按机构确认的备份策略另行淘汰；此页面不会删除备份文件。
            </p>
            <div className="actions">
              <button className="button secondary" disabled={retentionBusy} onClick={saveRetention}>{retentionBusy ? "处理中…" : "保存留存策略"}</button>
              <button className="button" disabled={retentionBusy} onClick={runRetention}><Play size={15} />{retentionBusy ? "清理中…" : "立即运行清理"}</button>
            </div>
          </>
        )}
      </section>

      <section className="panel privacy-requests-panel">
        <div className="panel-heading">
          <div>
            <h2>个人信息请求</h2>
            <p className="muted">登记撤回联系许可、匿名化或删除申请；完成前核实关联对象，系统会记录处理结果。</p>
          </div>
          <Badge>{requests.filter((item) => ["open", "needs_review"].includes(item.status)).length} 项待处理</Badge>
        </div>

        <div className="privacy-request-form">
          <div className="privacy-request-fields">
            <label>请求类型<select value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}>
              <option value="delete">删除个人信息</option><option value="anonymize">匿名化</option><option value="withdraw_contact">撤回联系许可</option>
            </select></label>
            <label>申请人姓名<input maxLength={100} value={form.subject_name} onChange={(event) => setForm({ ...form, subject_name: event.target.value })} /></label>
            <label>联系方式<input maxLength={200} value={form.contact} onChange={(event) => setForm({ ...form, contact: event.target.value })} /></label>
            <label>登记说明<input maxLength={2000} value={form.note} onChange={(event) => setForm({ ...form, note: event.target.value })} /></label>
          </div>
          <div className="privacy-lookup-row">
            <label>查找并关联线索<input value={lookup} onChange={(event) => setLookup(event.target.value)} placeholder="输入姓名或完整手机号/邮箱" onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void searchForNewRequest(); } }} /></label>
            <button className="button secondary" disabled={lookupBusy} onClick={searchForNewRequest}><Search size={15} />{lookupBusy ? "查找中…" : "查找线索"}</button>
            <span className="muted">可先登记，之后在待核实请求中补充关联。</span>
          </div>
          {matches.length > 0 && <div className="privacy-matches" aria-label="匹配线索">
            {matches.map((lead) => <button type="button" className="privacy-match" key={lead.id} onClick={() => chooseForNewRequest(lead)}>
              <span><strong>{lead.name}</strong><small>{maskContact(lead.contact)} · {date(lead.created_at)}</small></span><span>关联此线索</span>
            </button>)}
          </div>}
          {form.lead_id && <div className="inline-note"><ShieldCheck size={17} /><p>已关联核实线索；同一学员档案下的关联对象会一并处理。</p><button className="button secondary small" onClick={() => setForm({ ...form, lead_id: "", conversation_id: "" })}>解除关联</button></div>}
          <button className="button" disabled={requestBusy === "create" || (!form.subject_name.trim() && !form.contact.trim())} onClick={createRequest}>
            {requestBusy === "create" ? "登记中…" : "登记隐私请求"}
          </button>
        </div>

        <div className="privacy-request-list-heading">
          <h3>请求记录</h3>
          <label>筛选状态<select value={requestStatus} onChange={(event) => setRequestStatus(event.target.value)}>
            <option value="">全部</option><option value="open">待处理</option><option value="needs_review">待核实</option><option value="completed">已完成</option><option value="rejected">已拒绝</option>
          </select></label>
        </div>
        {requestError ? <ErrorBox text={requestError} retry={loadRequests} /> : requestLoading ? <Loading /> : requests.length === 0 ? (
          <Empty title="暂无隐私请求" description="登记后的申请和处理结果会显示在这里。" />
        ) : (
          <div className="privacy-request-list">
            {requests.map((request) => {
              const target = requestTargets[request.id] || { lead_id: request.lead_id, conversation_id: request.conversation_id };
              const actionable = ["open", "needs_review"].includes(request.status);
              const decision = confirmAction.split(":")[0] === "complete" && confirmAction.endsWith(request.id) ? "complete" : confirmAction.split(":")[0] === "reject" && confirmAction.endsWith(request.id) ? "reject" : "";
              return <article className="privacy-request-row" key={request.id}>
                <div className="privacy-request-summary">
                  <div><strong>{kindText[request.kind] || request.kind}</strong><Badge tone={badgeTone(request.status)}>{statusText[request.status] || request.status}</Badge></div>
                  <p>{request.subject_name || "未填写姓名"} · {request.contact_hint || "未登记联系方式"} · 登记于 {date(request.created_at, true)}</p>
                  {request.note && <small>{request.note}</small>}
                  {request.resolution && <small>处理结果：{request.resolution}</small>}
                  {request.actor_name && <small>经办人：{request.actor_name}{request.completed_at ? ` · ${date(request.completed_at, true)}` : ""}</small>}
                </div>
                {actionable && <div className="privacy-request-actions">
                  {!target.lead_id && !target.conversation_id ? <div className="privacy-request-lookup">
                    <label>按姓名查找线索<input value={requestSearch[request.id] ?? request.subject_name ?? ""} onChange={(event) => setRequestSearch({ ...requestSearch, [request.id]: event.target.value })} /></label>
                    <button className="button secondary small" disabled={requestBusy === `search:${request.id}`} onClick={() => void searchForRequest(request)}><Search size={14} />查找</button>
                    {(requestMatches[request.id] || []).map((lead) => <button className="privacy-match compact" key={lead.id} onClick={() => chooseForRequest(request, lead)}>
                      <span>{lead.name} · {maskContact(lead.contact)}</span><span>选择</span>
                    </button>)}
                  </div> : <p className="privacy-selected-target">已关联线索：{requestTargets[request.id]?.lead?.name || request.lead_id || "会话"} · 处理时会再次校验机构与关联关系。</p>}
                  <div className="actions">
                    {decision === "complete" ? <>
                      <span className="privacy-confirm-copy"><AlertTriangle size={15} />确认执行{kindText[request.kind]}？</span>
                      <button className="button" disabled={requestBusy === request.id} onClick={() => void completeRequest(request)}>{requestBusy === request.id ? "处理中…" : "确认处理"}</button>
                      <button className="button secondary small" onClick={() => setConfirmAction("")}>取消</button>
                    </> : decision === "reject" ? <>
                      <label>拒绝原因<input value={rejectReason[request.id] || ""} onChange={(event) => setRejectReason({ ...rejectReason, [request.id]: event.target.value })} /></label>
                      <button className="button secondary small" disabled={requestBusy === request.id} onClick={() => void rejectRequest(request)}>{requestBusy === request.id ? "处理中…" : "确认拒绝"}</button>
                      <button className="button secondary small" onClick={() => setConfirmAction("")}>取消</button>
                    </> : <>
                      <button className="button" disabled={requestBusy === request.id || (!target.lead_id && !target.conversation_id)} onClick={() => void completeRequest(request)}><Check size={15} />核实并处理</button>
                      <button className="button secondary small" onClick={() => setConfirmAction(`reject:${request.id}`)}>拒绝请求</button>
                    </>}
                  </div>
                </div>}
              </article>;
            })}
          </div>
        )}
      </section>
    </div>
  );
}
