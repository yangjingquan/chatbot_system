import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Plus, Save, X } from "lucide-react";
import { api, date, useResource } from "./api";
import { useAccess } from "./auth";
import { Badge, Empty, ErrorBox, Loading, PageTitle, useToast } from "./ui";
import { exams, Filters, Pager, usePaged } from "./lists";

export function StudentAssociation({
  lead,
  onChanged,
}: {
  lead: any;
  onChanged: () => void;
}) {
  const { can } = useAccess(),
    toast = useToast();
  const [search, setSearch] = useState(""),
    [target, setTarget] = useState(""),
    [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false);
  const candidates = useResource<any[]>(
    "/admin/leads/" + lead.id + "/student-candidates",
  );
  const choices = useResource<any>(
    "/admin/students?page_size=100&include_demo="+lead.is_demo+"&q=" + encodeURIComponent(search),
  );
  async function attach(id?: string) {
    setBusy(true);
    try {
      await api("/admin/leads/" + lead.id + "/student", {
        method: "POST",
        body: JSON.stringify({
          student_id: id || null,
          identity_confirmed: confirmed,
        }),
      });
      onChanged();
      toast("已关联独立学员档案");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function unlink() {
    setBusy(true);
    try {
      await api("/admin/leads/" + lead.id + "/student", { method: "DELETE" });
      onChanged();
      toast("已解除关联，原咨询和学员档案均保留");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="knowledge-workflow">
      <h3>学员档案</h3>
      {lead.student_id ? (
        <>
          <Link
            className="text-link"
            to={"/customers?student_id=" + lead.student_id}
          >
            打开关联学员档案
          </Link>
          {can("students.merge") && (
            <button
              className="button secondary small"
              disabled={busy}
              onClick={unlink}
            >
              解除错误关联
            </button>
          )}
          <p className="footnote">调整负责人会同步该学员及全部关联线索。</p>
        </>
      ) : (
        <>
          <button
            className="button secondary full"
            disabled={busy}
            onClick={() => attach()}
          >
            从本次咨询独立建档
          </button>
          {Boolean(candidates.data?.length) && (
            <p className="review-notice">
              发现 {candidates.data?.length}{" "}
              份联系方式相同的档案，仅提示疑似重复。未核验联系方式不能认定为同一人。
            </p>
          )}
          <label>
            查找已有学员
            <input
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setTarget("");
              }}
              placeholder="输入学员姓名或完整联系方式"
            />
          </label>
          <label>
            关联到已有档案
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">请选择人工核实后的档案</option>
              {choices.data?.items.map((s: any) => (
                <option key={s.id} value={s.id}>
                  {s.name} · {s.owner} · {s.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          <p className="footnote">
            最多显示 100 个候选，请输入更具体的姓名或联系方式缩小范围。
          </p>
          <label className="checkbox-line">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            我已核实本次咨询与所选档案为同一学员
          </label>
          <button
            className="button secondary full"
            disabled={busy || !target || !confirmed}
            onClick={() => attach(target)}
          >
            关联已有学员
          </button>
          {choices.error && <ErrorBox text={choices.error} />}
        </>
      )}
    </div>
  );
}

export function Students({
  includeDemo,
  version,
}: {
  includeDemo: boolean;
  version: number;
}) {
  const list = usePaged("/admin/students", includeDemo, version),
    r = list.resource,
    toast = useToast(),
    { can } = useAccess();
  const owners = useResource<any[]>("/admin/filter-options", version),
    courses = useResource<any>("/admin/courses?page_size=100", version);
  const [params] = useSearchParams();
  const [edit, setEdit] = useState<any>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [interest, setInterest] = useState<any>(null),
    [mergeSearch, setMergeSearch] = useState(""),
    [mergeId, setMergeId] = useState(""),
    [reason, setReason] = useState(""),
    [confirmed, setConfirmed] = useState(false);
  const choices = useResource<any>(
    "/admin/students?page_size=100&include_demo="+includeDemo+"&q=" + encodeURIComponent(mergeSearch),
    can("students.merge") ? version : -1,
  );
  // The selected profile is read through its own scoped endpoint, including deep links.
  async function open(id: string) {
    setError("");
    setBusy(true);
    try {
      setEdit(await api("/admin/students/" + id));
      setInterest(null);
      setMergeId("");
      setReason("");
      setConfirmed(false);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const requestedStudent = params.get("student_id");
  useEffect(() => {
    if (requestedStudent) void open(requestedStudent);
  }, [requestedStudent]);
  async function save() {
    setBusy(true);
    setError("");
    try {
      const payload = {
        name: edit.name,
        exam_targets: edit.exam_targets,
        notes: edit.notes,
        ...(edit.id
          ? {
              expected_version: edit.version,
              ...(can("leads.assign") ? { owner_id: edit.owner_id } : {}),
            }
          : {}),
      };
      setEdit(
        await api(edit.id ? "/admin/students/" + edit.id : "/admin/students", {
          method: edit.id ? "PATCH" : "POST",
          body: JSON.stringify(payload),
        }),
      );
      r.reload();
      toast("学员档案已保存");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function saveInterest() {
    setBusy(true);
    setError("");
    try {
      setEdit(
        await api(
          "/admin/students/" +
            edit.id +
            "/interests" +
            (interest.id ? "/" + interest.id : ""),
          {
            method: interest.id ? "PATCH" : "POST",
            body: JSON.stringify({
              ...interest,
              course_id: interest.course_id || null,
              target_year: interest.target_year
                ? Number(interest.target_year)
                : null,
            }),
          },
        ),
      );
      setInterest(null);
      r.reload();
      toast("课程需求已保存");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function merge() {
    const target = choices.data?.items.find((s: any) => s.id === mergeId);
    if (!target) return;
    setBusy(true);
    setError("");
    try {
      setEdit(
        await api("/admin/students/" + edit.id + "/merge", {
          method: "POST",
          body: JSON.stringify({
            target_id: target.id,
            source_version: edit.version,
            target_version: target.version,
            identity_confirmed: confirmed,
            reason,
          }),
        }),
      );
      r.reload();
      setMergeId("");
      setReason("");
      setConfirmed(false);
      toast("档案已人工合并，各次联系许可保持独立");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        title="学员档案"
        description="独立记录学员的多次咨询、考试目标与课程需求；相同联系方式仅提示疑似重复。"
        actions={
          <button
            className="button"
            onClick={() => {
              setEdit({
                name: "",
                notes: "",
                exam_targets: [],
                owner_id: null,
              });
              setInterest(null);
              setError("");
            }}
          >
            <Plus size={16} />
            新建档案
          </button>
        }
      />
      <section className="panel">
        <Filters list={list} owners={owners.data || []} business={false} />
      </section>
      {error && <ErrorBox text={error} />}
      <div className={"lead-layout " + (edit ? "with-detail" : "")}>
        <section className="panel">
          {r.loading ? (
            <Loading />
          ) : r.error ? (
            <ErrorBox text={r.error} retry={r.reload} />
          ) : r.data?.items.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>学员</th>
                    <th>考试目标</th>
                    <th>负责人</th>
                    <th>创建时间</th>
                  </tr>
                </thead>
                <tbody>
                  {r.data.items.map((s) => (
                    <tr key={s.id}>
                      <td>
                        <button
                          className="document-link"
                          onClick={() => open(s.id)}
                        >
                          <strong>{s.name}</strong>
                        </button>
                        {s.is_demo && <Badge tone="muted">演示</Badge>}
                      </td>
                      <td>{s.exam_targets.join("、") || "待了解"}</td>
                      <td>{s.owner}</td>
                      <td>{date(s.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              title="暂无学员档案"
              description="可从线索独立建档，或手动新建，不会按联系方式自动合并。"
            />
          )}
          <Pager list={list} />
        </section>
        {edit && (
          <aside className="panel lead-detail">
            <div className="panel-heading">
              <h2>{edit.id ? edit.name : "新建学员"}</h2>
              <button
                className="icon-button"
                aria-label="关闭学员详情"
                onClick={() => setEdit(null)}
              >
                <X size={18} />
              </button>
            </div>
            {edit.status === "merged" ? (
              <>
                <p className="review-notice">该档案已合并，历史来源保留。</p>
                <button
                  className="button"
                  onClick={() => open(edit.merged_into_id)}
                >
                  打开目标档案
                </button>
              </>
            ) : (
              <>
                <label>
                  姓名
                  <input
                    maxLength={100}
                    value={edit.name}
                    onChange={(e) => setEdit({ ...edit, name: e.target.value })}
                  />
                </label>
                <label>
                  考试目标
                  <select
                    multiple
                    value={edit.exam_targets}
                    onChange={(e) =>
                      setEdit({
                        ...edit,
                        exam_targets: Array.from(
                          e.target.selectedOptions,
                          (o) => o.value,
                        ),
                      })
                    }
                  >
                    {exams.map((x) => (
                      <option key={x}>{x}</option>
                    ))}
                  </select>
                  <small>按住 Command / Ctrl 可选择多个目标。</small>
                </label>
                {edit.id && (
                  <label>
                    负责人
                    {can("leads.assign") ? (
                      <select
                        value={edit.owner_id || ""}
                        onChange={(e) =>
                          setEdit({ ...edit, owner_id: e.target.value || null })
                        }
                      >
                        <option value="">未分配</option>
                        {owners.data
                          ?.filter((o) => o.is_active || o.id === edit.owner_id)
                          .map((o) => (
                            <option key={o.id} value={o.id}>
                              {o.name}
                              {!o.is_active ? "（已停用，需重新选择）" : ""}
                            </option>
                          ))}
                      </select>
                    ) : (
                      <input readOnly value={edit.owner} />
                    )}
                    <small>更改后同步该学员的全部关联线索。</small>
                  </label>
                )}
                <label>
                  学员备注
                  <textarea
                    rows={3}
                    maxLength={6000}
                    value={edit.notes}
                    onChange={(e) =>
                      setEdit({ ...edit, notes: e.target.value })
                    }
                  />
                </label>
                <button
                  className="button full"
                  disabled={busy || !edit.name.trim()}
                  onClick={save}
                >
                  <Save size={16} />
                  保存学员档案
                </button>
                {edit.id && (
                  <>
                    <h3>联系方式与许可</h3>
                    {edit.contacts?.length ? (
                      edit.contacts.map((c: any, i: number) => (
                        <div className="need-list" key={i}>
                          <p>
                            {c.contact}{" "}
                            <Badge tone={c.contact_allowed ? "muted" : "amber"}>
                              {c.contact_allowed
                                ? "允许本次咨询联系"
                                : "已停止联系"}
                            </Badge>
                            <small>
                              {c.verified ? "人工核验" : "未核验"} ·
                              许可来源于对应咨询，不能跨咨询合并授权。
                            </small>
                          </p>
                        </div>
                      ))
                    ) : (
                      <p className="footnote">尚未关联留资咨询。</p>
                    )}
                    <div className="row-between section-space">
                      <h3>考试与课程需求</h3>
                      <button
                        className="button secondary small"
                        onClick={() =>
                          setInterest({
                            exam_type: "国考",
                            course_id: "",
                            target_year: null,
                            status: "exploring",
                            notes: "",
                          })
                        }
                      >
                        新增需求
                      </button>
                    </div>
                    {edit.interests?.map((i: any) => (
                      <button
                        className="revision-row"
                        key={i.id}
                        onClick={() => setInterest({ ...i })}
                      >
                        <strong>
                          {i.exam_type} · {i.target_year || "年度待明确"} ·{" "}
                          {
                            (
                              {
                                exploring: "了解中",
                                interested: "有意向",
                                enrolled: "已报名",
                                closed: "已结束",
                              } as any
                            )[i.status]
                          }
                        </strong>
                        <small>
                          {i.course_name || "课程待明确"} · {i.notes}
                        </small>
                      </button>
                    ))}
                    {interest && (
                      <div className="knowledge-workflow">
                        <label>
                          需求考试
                          <select
                            value={interest.exam_type}
                            onChange={(e) =>
                              setInterest({
                                ...interest,
                                exam_type: e.target.value,
                                course_id: "",
                              })
                            }
                          >
                            {exams.map((x) => (
                              <option key={x}>{x}</option>
                            ))}
                          </select>
                        </label>
                        <label>
                          目标年度
                          <input
                            type="number"
                            min={2000}
                            max={2100}
                            value={interest.target_year || ""}
                            onChange={(e) =>
                              setInterest({
                                ...interest,
                                target_year: e.target.value,
                              })
                            }
                          />
                        </label>
                        <label>
                          课程
                          <select
                            value={interest.course_id || ""}
                            onChange={(e) =>
                              setInterest({
                                ...interest,
                                course_id: e.target.value,
                              })
                            }
                          >
                            <option value="">课程待明确</option>
                            {courses.data?.items
                              .filter(
                                (c: any) => c.exam_type === interest.exam_type,
                              )
                              .map((c: any) => (
                                <option key={c.id} value={c.id}>
                                  {c.name}
                                </option>
                              ))}
                          </select>
                        </label>
                        <label>
                          需求状态
                          <select
                            value={interest.status}
                            onChange={(e) =>
                              setInterest({
                                ...interest,
                                status: e.target.value,
                              })
                            }
                          >
                            <option value="exploring">了解中</option>
                            <option value="interested">有意向</option>
                            <option value="enrolled">已报名</option>
                            <option value="closed">已结束</option>
                          </select>
                        </label>
                        <label>
                          需求说明
                          <textarea
                            rows={2}
                            value={interest.notes}
                            onChange={(e) =>
                              setInterest({
                                ...interest,
                                notes: e.target.value,
                              })
                            }
                          />
                        </label>
                        <button
                          className="button secondary full"
                          disabled={busy}
                          onClick={saveInterest}
                        >
                          保存课程需求
                        </button>
                      </div>
                    )}
                    <h3>关联咨询</h3>
                    {edit.leads?.map((l: any) => (
                      <article className="revision-row" key={l.id}>
                        <Link
                          className="text-link"
                          to={
                            "/conversations?conversation_id=" +
                            l.conversation_id
                          }
                        >
                          {l.name} · {date(l.created_at, true)}
                        </Link>
                        <small>
                          {l.stage} · {l.level} ·{" "}
                          {l.contact_allowed ? "允许本次联系" : "已停止联系"}
                        </small>
                      </article>
                    ))}
                    <h3>跟进记录</h3>
                    {edit.followups?.map((f: any) => (
                      <blockquote className="evidence" key={f.id}>
                        <strong>
                          {f.author} · {date(f.created_at, true)}
                        </strong>
                        <p>{f.content}</p>
                      </blockquote>
                    ))}
                    {can("students.merge") && (
                      <details className="merge-panel">
                        <summary>人工合并重复档案</summary>
                        <p className="review-notice">
                          仅在核实同一人后合并。各次联系许可保持独立；原档案保留合并去向，负责人使用目标档案负责人。
                        </p>
                        <label>
                          查找目标档案
                          <input
                            value={mergeSearch}
                            onChange={(e) => {
                              setMergeSearch(e.target.value);
                              setMergeId("");
                            }}
                            placeholder="输入目标学员姓名或完整联系方式"
                          />
                        </label>
                        <label>
                          合并到
                          <select
                            value={mergeId}
                            onChange={(e) => setMergeId(e.target.value)}
                          >
                            <option value="">请选择目标档案</option>
                            {choices.data?.items
                              .filter((s: any) => s.id !== edit.id)
                              .map((s: any) => (
                                <option value={s.id} key={s.id}>
                                  {s.name} · {s.id.slice(0, 8)}
                                </option>
                              ))}
                          </select>
                        </label>
                        <label>
                          核验及合并原因
                          <textarea
                            value={reason}
                            maxLength={1000}
                            onChange={(e) => setReason(e.target.value)}
                            rows={2}
                          />
                        </label>
                        <label className="checkbox-line">
                          <input
                            type="checkbox"
                            checked={confirmed}
                            onChange={(e) => setConfirmed(e.target.checked)}
                          />
                          已确认同一人，未仅凭相同的未核验联系方式判断
                        </label>
                        <button
                          className="button secondary full"
                          disabled={
                            busy || !mergeId || !confirmed || !reason.trim()
                          }
                          onClick={merge}
                        >
                          确认人工合并
                        </button>
                      </details>
                    )}
                  </>
                )}
              </>
            )}
          </aside>
        )}
      </div>
    </>
  );
}
