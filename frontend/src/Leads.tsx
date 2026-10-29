import { Select } from "./Select";
import { useEffect, useRef, useState } from "react";
import {
  Search,
  Download,
  ChevronRight,
  X,
  Save,
  Plus,
  Phone,
  Users,
  LockKeyhole,
} from "lucide-react";
import { api, date, useResource } from "./api";
import { Link, useSearchParams } from "react-router-dom";
import { Filters, Pager, usePaged, localTime, isoTime } from "./lists";
import { StudentAssociation } from "./Students";
import { trialLabels } from "./Reception";
import { followupCopy } from "./timeCopy";
import { exampleBatchCopy } from "./displayCopy";
import { useAccess } from "./auth";
import {
  Badge,
  Empty,
  ErrorBox,
  Level,
  Loading,
  PageTitle,
  useToast,
} from "./ui";

const stages = [
  "新线索",
  "待分配",
  "跟进中",
  "已合格",
  "商机推进",
  "已成交",
  "已失效",
];
const levels = ["待判定", "一般咨询", "低意向", "中意向", "高意向", "不适用"];
export function Leads({
  includeDemo,
  version,
  customers = false,
}: {
  includeDemo: boolean;
  version: number;
  customers?: boolean;
}) {
  const originalLevel = useRef("");
  const originalOwner = useRef<string | null>(null);
  const { can } = useAccess();
  const staff = useResource<any[]>(
    "/admin/staff/options",
    can("leads.assign") ? version : -1,
  );
  const list = usePaged("/admin/leads", includeDemo, version);
  const r = list.resource,
    toast = useToast();
  const owners = useResource<any[]>("/admin/filter-options", version);
  const [detail, setDetail] = useState<any>(null),
    [note, setNote] = useState(""),
    [busy, setBusy] = useState(false);
  const rows = r.data?.items || [];
  const [params] = useSearchParams();
  const requestedLead = params.get("lead_id");
  useEffect(() => {
    if (requestedLead) void open(requestedLead);
  }, [requestedLead]);
  async function open(id: string) {
    try {
      const result = await api("/admin/leads/" + id);
      setDetail(result);
      originalLevel.current = result.level;
      originalOwner.current = result.owner_id;
      setNote("");
    } catch (e: any) {
      toast(e.message, true);
    }
  }
  async function save() {
    setBusy(true);
    try {
      setDetail(
        await api("/admin/leads/" + detail.id, {
          method: "PATCH",
          body: JSON.stringify({
            ...(can("leads.assign") && detail.owner_id !== originalOwner.current
              ? { owner_id: detail.owner_id || null }
              : {}),
            stage: detail.stage,
            level:
              detail.level !== originalLevel.current ? detail.level : undefined,
            contact_verified: detail.contact_verified,
            next_followup_at: detail.next_followup_at || null,
          }),
        }),
      );
      r.reload();
      originalLevel.current = detail.level;
      originalOwner.current = detail.owner_id;
      toast("线索跟进状态已更新");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function restoreAutomatic() {
    setBusy(true);
    try {
      const result = await api("/admin/leads/" + detail.id, {
        method: "PATCH",
        body: JSON.stringify({ automatic_level: true }),
      });
      setDetail(result);
      originalLevel.current = result.level;
      r.reload();
      toast("已恢复按最新对话自动分级");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function withdrawContact() {
    if (!detail?.contact_allowed) return;
    if (!confirm("确认登记该线索已停止联系？此后待办将不再提醒外呼。")) return;
    setBusy(true);
    try {
      await api("/admin/leads/" + detail.id + "/withdraw-contact", {
        method: "POST",
      });
      const result = await api("/admin/leads/" + detail.id);
      setDetail(result);
      r.reload();
      toast("已登记停止联系");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function addNote() {
    setBusy(true);
    try {
      setDetail(
        await api("/admin/leads/" + detail.id + "/followups", {
          method: "POST",
          body: JSON.stringify({ content: note }),
        }),
      );
      setNote("");
      toast("跟进记录已添加");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        title={customers ? "客户档案" : "咨询线索"}
        description={
          customers
            ? "首版从已成交线索形成客户列表，保留咨询与跟进记录。"
            : "把需求、对话证据和后续行动放在一起。"
        }
        actions={
          !customers &&
          can("leads.export") && (
            <a
              className="button secondary"
              href={"/api/admin/leads-export?" + list.query}
              download
            >
              <Download size={16} />
              导出线索
            </a>
          )
        }
      />
      <div className="lead-summary">
        <span>
          当前筛选共 <strong>{r.data?.total || 0}</strong> 条线索
        </span>
        <small>完整筛选条件同样用于导出；联系方式未核验不等于真实身份。</small>
      </div>
      <div className={"lead-layout " + (detail ? "with-detail" : "")}>
        <section className="panel">
          <Filters list={list} owners={owners.data || []} />
          {r.loading ? (
            <Loading />
          ) : r.error ? (
            <ErrorBox text={r.error} retry={r.reload} />
          ) : rows.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>咨询人 / 联系方式</th>
                    <th>意向等级</th>
                    <th>关注课程</th>
                    <th>跟进阶段</th>
                    <th>负责人</th>
                    <th>创建时间</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((l) => (
                    <tr
                      key={l.id}
                      className={detail?.id === l.id ? "selected-row" : ""}
                    >
                      <td>
                        <button
                          className="lead-person"
                          onClick={() => open(l.id)}
                        >
                          <span className="visitor-avatar">
                            {l.name.slice(0, 1)}
                          </span>
                          <span>
                            <strong>
                              {l.name}
                              {l.is_demo && <Badge tone="muted">示例</Badge>}
                            </strong>
                            <small>
                              {!l.contact_allowed && (
                                <Badge tone="amber">已停止联系</Badge>
                              )}{" "}
                              {l.contact} ·{" "}
                              {l.contact_verified ? "已核验" : "待核验"}
                            </small>
                          </span>
                        </button>
                      </td>
                      <td>
                        <Level value={l.level} />
                        {l.manual_level && (
                          <small className="visibility">
                            <LockKeyhole size={10} />
                            人工确认
                          </small>
                        )}
                      </td>
                      <td>
                        <div className="tag-wrap">
                          {l.tags
                            .filter(
                              (t: string) =>
                                ![
                                  "可联系",
                                  "明确需求",
                                  "主动询价",
                                  "近期计划",
                                  "预算明确",
                                  "预约试听",
                                  "售后服务",
                                ].includes(t),
                            )
                            .slice(0, 2)
                            .map((t: string) => (
                              <Badge key={t}>{t}</Badge>
                            ))}
                        </div>
                      </td>
                      <td>
                        <Badge
                          tone={
                            l.stage === "已成交"
                              ? "green"
                              : l.stage === "已失效"
                                ? "muted"
                                : "default"
                          }
                        >
                          {l.stage}
                        </Badge>
                      </td>
                      <td className="muted nowrap">{l.owner}</td>
                      <td className="muted nowrap">{date(l.created_at)}</td>
                      <td>
                        <button
                          className="icon-button"
                          aria-label={"查看" + l.name}
                          onClick={() => open(l.id)}
                        >
                          <ChevronRight size={17} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              title={customers ? "暂无已成交客户" : "没有匹配的线索"}
              description={
                customers
                  ? "在线索详情中如实更新“已成交”，客户将显示在这里。"
                  : "访客在官网自愿留资后，会进入这里供顾问跟进。"
              }
            />
          )}
          <Pager list={list} />
        </section>
        {detail && (
          <aside className="panel lead-detail">
            <div className="panel-heading">
              <div>
                <h2>{detail.name}</h2>
                <small>{detail.is_demo ? "示例记录" : "网站咨询线索"}</small>
              </div>
              <button
                className="icon-button"
                aria-label="关闭线索详情"
                onClick={() => setDetail(null)}
              >
                <X size={18} />
              </button>
            </div>
            <p className="contact-line">
              <Phone size={16} />
              {detail.contact}
              <Badge tone="muted">
                {!detail.contact_allowed
                  ? "已停止联系"
                  : detail.contact_verified
                    ? "人工核验"
                    : "待核验"}
              </Badge>
              {detail.contact_allowed && can("leads.write") && (
                <button
                  className="button secondary small"
                  disabled={busy}
                  onClick={withdrawContact}
                >
                  停止联系
                </button>
              )}
            </p>
            <div className="form-row">
              <label>
                意向等级
                <Select
                  value={detail.level}
                  onValueChange={(value) =>
                    setDetail({ ...detail, level: value })
                  }
                >
                  {levels.map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </Select>
              </label>
              <label>
                跟进阶段
                <Select
                  value={detail.stage}
                  onValueChange={(value) =>
                    setDetail({ ...detail, stage: value })
                  }
                >
                  {stages.map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </Select>
              </label>
            </div>
            <label>
              负责人
              {can("leads.assign") ? (
                <Select
                  value={detail.owner_id || ""}
                  disabled={staff.loading || Boolean(staff.error)}
                  onValueChange={(value) =>
                    setDetail({ ...detail, owner_id: value || null })
                  }
                >
                  <option value="">
                    未分配
                    {!detail.owner_id && detail.owner !== "未分配"
                      ? `（历史归属：${detail.owner}）`
                      : ""}
                  </option>
                  {detail.owner_id &&
                    !staff.data?.some((m) => m.id === detail.owner_id) && (
                      <option value={detail.owner_id}>
                        {detail.owner}（已停用或岗位已变更）
                      </option>
                    )}
                  {staff.data?.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </Select>
              ) : (
                <input readOnly value={detail.owner} />
              )}
              {staff.error && (
                <ErrorBox text={staff.error} retry={staff.reload} />
              )}
            </label>
            <label>
              下次跟进（北京时间）
              <input
                type="datetime-local"
                value={localTime(detail.next_followup_at)}
                onChange={(e) =>
                  setDetail({
                    ...detail,
                    next_followup_at: isoTime(e.target.value),
                  })
                }
              />
              <small>留空取消待办；停止联系的线索不会提醒外呼。</small>
            </label>
            <label className="checkbox-line">
              <input
                type="checkbox"
                checked={detail.contact_verified}
                onChange={(e) =>
                  setDetail({ ...detail, contact_verified: e.target.checked })
                }
              />
              我已通过正式渠道核验此联系方式
            </label>
            <button className="button full" disabled={busy} onClick={save}>
              <Save size={16} />
              保存跟进状态
            </button>
            {detail.manual_level && (
              <button
                className="button secondary full"
                disabled={busy}
                onClick={restoreAutomatic}
                style={{ marginTop: 10 }}
              >
                恢复自动分级
              </button>
            )}
            <h3>
              意向证据{" "}
              <small>规则分数 {detail.score} / 100 · 仅作跟进辅助</small>
            </h3>
            {(detail.score_meta?.uncertainty ||
              detail.score_meta?.confidence) && (
              <p className="footnote">
                {detail.score_meta?.confidence
                  ? `置信：${
                      (
                        {
                          high: "较高",
                          medium: "中等",
                          low: "较低",
                        } as Record<string, string>
                      )[detail.score_meta.confidence] ||
                      detail.score_meta.confidence
                    }。`
                  : ""}
                {detail.score_meta?.uncertainty || ""}
                {detail.manual_level
                  ? " 当前为人工确认等级，自动规则不会覆盖。"
                  : " 可随时人工改级或恢复自动分级。"}
              </p>
            )}
            {detail.evidence.length ? (
              detail.evidence.map((e: any, i: number) => (
                <blockquote className="evidence" key={i}>
                  <strong>
                    {e.label} <small>+{e.points}</small>
                  </strong>
                  <p>“{e.quote}”</p>
                </blockquote>
              ))
            ) : (
              <p className="footnote">暂无明确采购信号，需继续了解。</p>
            )}
            <StudentAssociation
              key={detail.id + String(detail.student_id)}
              lead={detail}
              onChanged={() => {
                void open(detail.id);
                r.reload();
              }}
            />
            <h3>试听记录</h3>
            {detail.trials?.length ? (
              detail.trials.map((t: any) => (
                <article className="revision-row" key={t.id}>
                  <Link className="text-link" to={"/trials"}>
                    {t.course_name} ·{" "}
                    {exampleBatchCopy(t.batch_name, t.is_demo)}
                    {t.is_demo ? "（示例）" : ""} ·{" "}
                    {trialLabels[t.status] || t.status}
                  </Link>
                  <small>
                    意向 {date(t.requested_at, true)}
                    {t.scheduled_at
                      ? ` · 确认 ${date(t.scheduled_at, true)}`
                      : ""}
                    {t.reason ? ` · 原因：${t.reason}` : ""}
                  </small>
                </article>
              ))
            ) : (
              <p className="footnote">本线索尚无试听申请。</p>
            )}
            <h3>添加跟进</h3>
            <textarea
              aria-label="跟进记录内容"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="记录已了解的情况、约定与下一步"
              rows={3}
              maxLength={4000}
            />
            <button
              className="button secondary full"
              disabled={busy || !note.trim()}
              onClick={addNote}
            >
              <Plus size={16} />
              添加跟进记录
            </button>
            <div className="timeline">
              {detail.followups.map((f: any) => (
                <article key={f.id}>
                  <span className="timeline-dot" />
                  <div className="row-between">
                    <strong>{f.author}</strong>
                    <small>{date(f.created_at, true)}</small>
                  </div>
                  <p>{followupCopy(f.content)}</p>
                </article>
              ))}
            </div>
            <a href="/conversations" className="text-link">
              查看咨询记录
            </a>
          </aside>
        )}
      </div>
    </>
  );
}
