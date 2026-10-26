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
  const r = useResource<any[]>(
      `/admin/leads?include_demo=${includeDemo}`,
      version,
    ),
    toast = useToast();
  const [search, setSearch] = useState(""),
    [level, setLevel] = useState("all"),
    [stage, setStage] = useState("all"),
    [detail, setDetail] = useState<any>(null),
    [note, setNote] = useState(""),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    setDetail(null);
    setLevel("all");
    setStage("all");
  }, [customers]);
  const rows = (r.data || []).filter(
    (l) =>
      (!customers || l.stage === "已成交") &&
      (l.name + l.tags.join("") + l.owner + l.contact).includes(search) &&
      (level === "all" || l.level === level) &&
      (stage === "all" || l.stage === stage),
  );
  async function open(id: string) {
    try {
      const result = await api("/admin/leads/" + id);
      setDetail(result);
      originalLevel.current = result.level;
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
            owner: detail.owner,
            stage: detail.stage,
            level:
              detail.level !== originalLevel.current ? detail.level : undefined,
            contact_verified: detail.contact_verified,
          }),
        }),
      );
      r.reload();
      originalLevel.current = detail.level;
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
          !customers && (
            <a
              className="button secondary"
              href={`/api/admin/leads-export?include_demo=${includeDemo}`}
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
          共 <strong>{rows.length}</strong> 条{customers ? "客户" : "线索"}
        </span>
        <span>
          <span className="status-dot green" />
          高意向 {(r.data || []).filter((l) => l.level === "高意向").length}
        </span>
        <span>
          <span className="status-dot amber" />
          待分配 {(r.data || []).filter((l) => l.owner === "未分配").length}
        </span>
        <small>联系方式仅用于已说明的咨询用途；未核验不等于真实身份。</small>
      </div>
      <div className={"lead-layout " + (detail ? "with-detail" : "")}>
        <section className="panel">
          <div className="toolbar">
            <div className="search-field">
              <Search size={16} />
              <input
                aria-label="搜索线索"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索姓名、课程、负责人"
              />
            </div>
            <div className="filter-group">
              <select
                aria-label="意向等级"
                value={level}
                onChange={(e) => setLevel(e.target.value)}
              >
                <option value="all">全部意向</option>
                {levels.map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
              <select
                aria-label="跟进阶段"
                value={stage}
                onChange={(e) => setStage(e.target.value)}
              >
                <option value="all">全部阶段</option>
                {stages.map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </div>
          </div>
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
                              {l.is_demo && <Badge tone="muted">演示</Badge>}
                            </strong>
                            <small>
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
        </section>
        {detail && (
          <aside className="panel lead-detail">
            <div className="panel-heading">
              <div>
                <h2>{detail.name}</h2>
                <small>
                  {detail.is_demo ? "合成演示记录" : "网站咨询线索"}
                </small>
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
            </p>
            <div className="form-row">
              <label>
                意向等级
                <select
                  value={detail.level}
                  onChange={(e) =>
                    setDetail({ ...detail, level: e.target.value })
                  }
                >
                  {levels.map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
              <label>
                跟进阶段
                <select
                  value={detail.stage}
                  onChange={(e) =>
                    setDetail({ ...detail, stage: e.target.value })
                  }
                >
                  {stages.map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              负责人
              <input
                value={detail.owner}
                onChange={(e) =>
                  setDetail({ ...detail, owner: e.target.value })
                }
                maxLength={80}
              />
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
              意向证据 <small>规则分数 {detail.score} / 100</small>
            </h3>
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
                  <p>{f.content}</p>
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
