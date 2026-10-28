import { Select } from "./Select";
import { exampleBatchCopy } from "./displayCopy";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, date, useResource } from "./api";
import { PageTitle, ErrorBox, Loading, Empty, Badge, useToast } from "./ui";
import { connectReceptionSocket, pollMs } from "./receptionSocket";
export const trialLabels: Record<string, string> = {
  pending: "待确认",
  confirmed: "已确认",
  attended: "已参加",
  no_show: "未参加",
  cancelled: "已取消",
};
const localTime = (v: string) => {
  const d = new Date(v);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};
export function ReceptionStatus() {
  const [status, setStatus] = useState("offline"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const counts = useResource<any>("/admin/reception-summary");
  const toast = useToast();
  const [wsLive, setWsLive] = useState(false);
  useEffect(() => {
    const stop = connectReceptionSocket({
      onStatus: setWsLive,
      onEvent: () => counts.reload(),
    });
    return () => stop();
  }, []);
  useEffect(() => {
    const beat = () =>
      api("/admin/presence", {
        method: "PUT",
        body: JSON.stringify({ status }),
      })
        .then(() => setError(""))
        .catch((e) => setError(e.message));
    beat();
    const timer = setInterval(
      () => {
        beat();
        counts.reload();
      },
      pollMs(wsLive, 60000, 15000),
    );
    return () => clearInterval(timer);
  }, [status, wsLive]);
  async function claimNext() {
    setBusy(true);
    try {
      const row = await api("/admin/reception/claim-next", {
        method: "POST",
        body: JSON.stringify({ prefer_timed_out: true }),
      });
      toast("已接管排队会话");
      counts.reload();
      window.location.assign(
        "/conversations?conversation_id=" + row.conversation_id,
      );
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  const timed = counts.data?.timed_out || 0;
  return (
    <div className="reception-status">
      <label>
        接待状态{" "}
        <Select
          aria-label="我的在线状态"
          value={status}
          onValueChange={(value) => setStatus(value)}
        >
          <option value="online">在线</option>
          <option value="away">暂离</option>
          <option value="offline">离线</option>
        </Select>
      </label>
      <Link to="/conversations?status=waiting">
        待接待 {counts.data?.waiting ?? "—"}
        {timed > 0 ? ` · 超时 ${timed}` : ""} · 未读{" "}
        {counts.data?.unread ?? "—"} · 我的接待 {counts.data?.my_active ?? "—"}
      </Link>
      <button
        className="button secondary small"
        disabled={busy || !(counts.data?.waiting > 0)}
        onClick={claimNext}
      >
        接下一位
      </button>
      {error && <small role="alert">{error}</small>}
    </div>
  );
}
export function Trials({
  includeDemo,
  version,
}: {
  includeDemo: boolean;
  version: number;
}) {
  const toast = useToast();
  const [status, setStatus] = useState(""),
    [page, setPage] = useState(1),
    [selected, setSelected] = useState<any>(null),
    [advisor, setAdvisor] = useState(""),
    [time, setTime] = useState(""),
    [note, setNote] = useState(""),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false);
  const list = useResource<any>(
      `/admin/trials?include_demo=${includeDemo}&status=${status}&page=${page}&page_size=20`,
      version,
    ),
    staff = useResource<any[]>("/admin/reception-staff", version);
  useEffect(() => {
    setPage(1);
    setSelected(null);
  }, [status, includeDemo]);
  async function choose(t: any) {
    try {
      const detail = await api("/admin/trials/" + t.id);
      setSelected(detail);
      setAdvisor(detail.advisor_id || "");
      setTime(localTime(detail.scheduled_at || detail.requested_at));
      setNote(detail.note || "");
      setReason(detail.reason || "");
    } catch (e: any) {
      toast(e.message, true);
    }
  }
  async function change(next: string) {
    if ((next === "cancelled" || next === "no_show") && !reason.trim()) {
      toast("取消或未参加须填写原因", true);
      return;
    }
    setBusy(true);
    try {
      const detail = await api("/admin/trials/" + selected.id, {
        method: "PATCH",
        body: JSON.stringify({
          version: selected.version,
          status: next,
          advisor_id: advisor || null,
          scheduled_at: time ? new Date(time).toISOString() : null,
          note,
          reason: reason.trim(),
        }),
      });
      setSelected(detail);
      setAdvisor(detail.advisor_id || "");
      setTime(localTime(detail.scheduled_at || detail.requested_at));
      setNote(detail.note || "");
      setReason(detail.reason || "");
      list.reload();
      toast("试听状态已更新");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        title="试听预约"
        description="与线索、学员档案同一业务链：确认/参加/未参加/取消会自动写跟进待办；改阶段与关联档案仍需顾问人工确认。"
      />
      <section className="panel">
        <div className="list-toolbar">
          <label>
            申请状态{" "}
            <Select value={status} onValueChange={(value) => setStatus(value)}>
              <option value="">全部状态</option>
              {Object.entries(trialLabels).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </Select>
          </label>
          <button className="button secondary small" onClick={list.reload}>
            刷新申请
          </button>
        </div>
        {list.error ? (
          <ErrorBox text={list.error} retry={list.reload} />
        ) : list.loading ? (
          <Loading />
        ) : !list.data?.items.length ? (
          <Empty
            title="暂无试听申请"
            description="访客可在官网客服留下联系方式后申请试听。"
          />
        ) : (
          <>
            <p className="table-scroll-hint">左右滚动查看完整试听申请表</p>
            <div
              className="table-wrap trial-table"
              role="region"
              aria-label="试听申请列表，可横向滚动"
              tabIndex={0}
            >
              <table>
                <thead>
                  <tr>
                    <th>课程 / 班次</th>
                    <th>线索</th>
                    <th>意向时间</th>
                    <th>确认时间</th>
                    <th>顾问</th>
                    <th>状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {list.data.items.map((t: any) => (
                    <tr key={t.id}>
                      <td>
                        {t.course_name}
                        <small className="cell-sub">{t.batch_name}</small>
                      </td>
                      <td>
                        {t.lead_name || "—"}
                        <small className="cell-sub">
                          {t.lead_stage || ""}
                          {t.student_name ? ` · 学员 ${t.student_name}` : ""}
                        </small>
                      </td>
                      <td>{date(t.requested_at, true)}</td>
                      <td>
                        {t.scheduled_at ? date(t.scheduled_at, true) : "待确认"}
                      </td>
                      <td>{t.advisor_name}</td>
                      <td>
                        <Badge>{trialLabels[t.status]}</Badge>
                      </td>
                      <td>
                        <button
                          className="button secondary small"
                          onClick={() => choose(t)}
                        >
                          处理申请
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        <div className="list-toolbar">
          <span>共 {list.data?.total || 0} 条</span>
          <button
            className="button secondary small"
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
          >
            上一页
          </button>
          <span>第 {page} 页</span>
          <button
            className="button secondary small"
            disabled={page * 20 >= (list.data?.total || 0)}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </button>
        </div>
      </section>
      {selected && (
        <section className="panel quality-editor">
          <h2>
            {selected.course_name} · {trialLabels[selected.status]}
          </h2>
          <div className="actions" style={{ flexWrap: "wrap", gap: 8 }}>
            <Link
              to={"/conversations?conversation_id=" + selected.conversation_id}
            >
              查看咨询
            </Link>
            {selected.lead_id && (
              <Link to={"/leads?lead_id=" + selected.lead_id}>
                线索：{selected.lead_name}（{selected.lead_stage}）
              </Link>
            )}
            {selected.student_id && (
              <Link to={"/customers?student_id=" + selected.student_id}>
                学员：{selected.student_name || selected.student_id}
              </Link>
            )}
            {selected.next_followup_at && (
              <span>下次跟进 {date(selected.next_followup_at, true)}</span>
            )}
          </div>
          {selected.auto_todo_rule && (
            <p className="muted">自动待办：{selected.auto_todo_rule}</p>
          )}
          {!!selected.manual_actions?.length && (
            <ul className="muted">
              {selected.manual_actions.map((a: string) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          )}
          <label>
            接待顾问
            <Select
              value={advisor}
              onValueChange={(value) => setAdvisor(value)}
            >
              <option value="">请选择顾问</option>
              {staff.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ·{" "}
                  {s.status === "online"
                    ? "在线"
                    : s.status === "away"
                      ? "暂离"
                      : "离线"}
                </option>
              ))}
            </Select>
          </label>
          <label>
            确认时间
            <input
              type="datetime-local"
              value={time}
              onChange={(e) => setTime(e.target.value)}
            />
          </label>
          <label>
            内部备注
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={1000}
            />
          </label>
          <label>
            取消 / 未参加原因
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={1000}
              placeholder="取消或标记未参加时必填"
            />
          </label>
          <div className="actions">
            {["pending", "confirmed"].includes(selected.status) && (
              <>
                <button
                  className="button"
                  disabled={busy || !advisor || !time}
                  onClick={() => change("confirmed")}
                >
                  确认时间与顾问
                </button>
                {selected.status === "confirmed" && (
                  <>
                    <button
                      className="button secondary"
                      disabled={busy}
                      onClick={() => change("attended")}
                    >
                      记录已参加
                    </button>
                    <button
                      className="button secondary"
                      disabled={busy || !reason.trim()}
                      onClick={() => change("no_show")}
                    >
                      记录未参加
                    </button>
                  </>
                )}
                <button
                  className="button secondary"
                  disabled={busy || !reason.trim()}
                  onClick={() => change("cancelled")}
                >
                  取消预约
                </button>
              </>
            )}
          </div>
          {!!selected.followups?.length && (
            <div>
              <h3>线索跟进记录</h3>
              <ul>
                {selected.followups.map((f: any) => (
                  <li key={f.id}>
                    <strong>{f.author}</strong> · {date(f.created_at, true)}
                    <div>{f.content}</div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </>
  );
}

export function WidgetTrial({
  credential,
  hasContact,
  onContact,
}: {
  credential: any;
  hasContact: boolean;
  onContact: () => void;
}) {
  const [open, setOpen] = useState(false),
    [batch, setBatch] = useState(""),
    [time, setTime] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const headers = { Authorization: "Bearer " + credential.token };
  const batches = useResource<any[]>("/widget/trial-batches");
  const [trials, setTrials] = useState<any[]>([]);
  useEffect(() => {
    let active = true;
    const load = () =>
      api(`/widget/sessions/${credential.id}/trials`, { headers })
        .then((v) => {
          if (active) setTrials(v);
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    load();
    const t = setInterval(load, 10000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, [credential.id]);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(`/widget/sessions/${credential.id}/trials`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          batch_id: batch,
          requested_at: new Date(time).toISOString(),
          client_id: requestId,
        }),
      });
      setTrials(
        await api(`/widget/sessions/${credential.id}/trials`, { headers }),
      );
      setOpen(false);
      setRequestId(crypto.randomUUID());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="widget-trials">
      <button
        className="button secondary small"
        onClick={() => setOpen((v) => !v)}
      >
        申请试听 / 查看预约
      </button>
      {open && (
        <form className="contact-form" onSubmit={submit}>
          <h3>申请试听</h3>
          <p>提交意向时间后，由顾问核实并确认。</p>
          {!hasContact ? (
            <button type="button" className="button" onClick={onContact}>
              先留下本次试听联系方式
            </button>
          ) : (
            <>
              <label>
                课程与班次
                <Select
                  required
                  value={batch}
                  onValueChange={(value) => setBatch(value)}
                >
                  <option value="">请选择班次</option>
                  {batches.data?.map((b) => (
                    <option value={b.id} key={b.id}>
                      {b.course_name} · {exampleBatchCopy(b.name, b.is_demo)}
                      {b.is_demo ? "（示例）" : ""}
                    </option>
                  ))}
                </Select>
              </label>
              {batches.error && <ErrorBox text={batches.error} />}
              <label>
                意向时间
                <input
                  required
                  type="datetime-local"
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                />
              </label>
              <button className="button" disabled={busy || !batch || !time}>
                {busy ? "提交中…" : "提交待确认申请"}
              </button>
            </>
          )}
          {trials.map((t) => (
            <p key={t.id}>
              <strong>{trialLabels[t.status]}</strong> · {t.course_name}
              <br />
              {t.scheduled_at
                ? "确认时间：" + date(t.scheduled_at, true)
                : "意向时间：" + date(t.requested_at, true)}{" "}
              · {t.advisor_name}
            </p>
          ))}
        </form>
      )}
      {error && <ErrorBox text={error} />}
    </div>
  );
}
