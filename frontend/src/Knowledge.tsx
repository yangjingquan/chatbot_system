import { Select } from "./Select";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  BookOpen,
  Search,
  Plus,
  Upload,
  FileText,
  ChevronRight,
  X,
  Save,
  FlaskConical,
  History,
} from "lucide-react";
import { api, date, statusLabel, useResource } from "./api";
import { useAccess } from "./auth";
import { Filters, Pager, usePaged } from "./lists";
import { Badge, Empty, ErrorBox, Loading, PageTitle, useToast } from "./ui";

const exams = [
  "通用",
  "公安联考",
  "国考",
  "省考",
  "选调生",
  "事业单位",
  "社会招警",
  "辅警",
  "社区工作者",
];
const fresh = () => ({
  title: "",
  category: "未分类",
  content: "",
  visibility: "public",
  status: "draft",
  knowledge_type: "general",
  exam_type: "通用",
  region: "全国",
  exam_year: null,
  source_url: "",
  valid_from: null,
  valid_until: null,
  responsible_id: "",
  policy_verified: false,
});
const localTime = (value: string | null) =>
  value
    ? new Date(new Date(value).getTime() + 8 * 3600000)
        .toISOString()
        .slice(0, 16)
    : "";
const isoTime = (value: string) =>
  value ? new Date(value + ":00+08:00").toISOString() : null;

export function Knowledge({
  version,
  onChange,
}: {
  version: number;
  onChange: () => void;
}) {
  const list = usePaged("/admin/documents", true, version),
    r = list.resource;
  const people = useResource<any[]>("/admin/knowledge-responsibles", version);
  const todos = useResource<any>(
    "/admin/knowledge-todos?status=open&page_size=10",
    version,
  );
  const { can, user } = useAccess(),
    toast = useToast(),
    upload = useRef<HTMLInputElement>(null);
  const [edit, setEdit] = useState<any>(null);
  const [busy, setBusy] = useState(false),
    [dirty, setDirty] = useState(false),
    [error, setError] = useState(""),
    [note, setNote] = useState("");
  const [history, setHistory] = useState<any[] | null>(null),
    [preview, setPreview] = useState<any>(null);
  const [testQuery, setTestQuery] = useState("公安联考基础班多少钱？"),
    [results, setResults] = useState<any>(null),
    [testBusy, setTestBusy] = useState(false);
  const docs = r.data?.items || [];
  const [params] = useSearchParams();
  useEffect(() => {
    const id = params.get("document_id");
    if (id)
      api("/admin/documents/" + id)
        .then(replace)
        .catch((e) => setError(e.message));
  }, [params]);
  const locked = edit?.draft_status === "submitted";
  function replace(result: any) {
    setEdit(result);
    setDirty(false);
    setError("");
    setHistory(null);
    setPreview(null);
    setNote("");
  }
  function change(key: string, value: any) {
    setEdit({
      ...edit,
      [key]: value,
      ...(edit.knowledge_type === "policy" &&
      [
        "title",
        "content",
        "source_url",
        "exam_year",
        "exam_type",
        "region",
      ].includes(key)
        ? { policy_verified: false }
        : {}),
    });
    setDirty(true);
  }
  async function open(id: string) {
    if (dirty) {
      toast("请先保存草稿，或放弃未保存的修改。", true);
      return;
    }
    try {
      replace(await api("/admin/documents/" + id));
    } catch (e: any) {
      toast(e.message, true);
    }
  }
  async function save() {
    setBusy(true);
    setError("");
    try {
      const payload: any = {};
      for (const key of [
        "title",
        "content",
        "category",
        "visibility",
        "knowledge_type",
        "exam_type",
        "region",
        "exam_year",
        "source_url",
        "valid_from",
        "valid_until",
        "responsible_id",
        "policy_verified",
      ])
        payload[key] = edit[key];
      payload.source_url = payload.source_url || null;
      payload.responsible_id = payload.responsible_id || user?.id;
      if (edit.id) payload.expected_sequence = edit.edit_sequence;
      const result = await api(
        edit.id ? "/admin/documents/" + edit.id : "/admin/documents",
        { method: edit.id ? "PATCH" : "POST", body: JSON.stringify(payload) },
      );
      replace(result);
      onChange();
      toast("草稿已保存，正式版本将在审核发布后更新");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function act(action: string, extra = {}) {
    if (dirty) return;
    setBusy(true);
    setError("");
    try {
      const result = await api("/admin/documents/" + edit.id + "/" + action, {
        method: "POST",
        body: JSON.stringify({
          expected_sequence: edit.edit_sequence,
          note,
          ...extra,
        }),
      });
      replace(result);
      onChange();
      toast(
        (
          {
            submit: "已提交审核",
            publish: "已审核发布，正式检索已更新",
            reject: "已退回草稿",
            archive: "已下架，公众检索立即停止引用",
            restore: "已生成新版本并发布",
          } as Record<string, string>
        )[action],
      );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function uploadFiles(fileList: FileList | File[]) {
    if (dirty) {
      toast("请先处理未保存的修改。", true);
      return;
    }
    const files = Array.from(fileList || []);
    if (!files.length) return;
    setBusy(true);
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    try {
      const result = await api("/admin/document-jobs/upload-batch", {
        method: "POST",
        body,
      });
      onChange();
      todos.reload();
      const queued = result.queued || 0;
      const failed = result.failed || 0;
      toast(
        failed
          ? `已排队 ${queued} 个，${failed} 个失败（其余不受影响）。详见文档处理任务。`
          : `已排队 ${queued} 个文件，请到“文档处理任务”查看进度`,
        !!failed,
      );
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
      if (upload.current) upload.current.value = "";
    }
  }
  async function loadHistory() {
    setBusy(true);
    try {
      setHistory(await api("/admin/documents/" + edit.id + "/revisions"));
      setPreview(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function viewRevision(id: string) {
    setBusy(true);
    try {
      setPreview(await api("/admin/documents/" + edit.id + "/revisions/" + id));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function test() {
    setTestBusy(true);
    try {
      setResults(
        await api("/admin/retrieval-test", {
          method: "POST",
          body: JSON.stringify({ query: testQuery, public_only: true }),
        }),
      );
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setTestBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        title="企业知识库"
        description="草稿独立保存，审核发布后更新机器人的回答依据。"
        actions={
          <>
            <input
              ref={upload}
              type="file"
              className="sr-only"
              accept=".md,.txt,.pdf,.docx"
              multiple
              onChange={(e) => e.target.files && uploadFiles(e.target.files)}
            />
            <button
              className="button secondary"
              disabled={busy || dirty}
              onClick={() => upload.current?.click()}
            >
              <Upload size={16} />
              批量上传
            </button>
            <button
              className="button"
              disabled={dirty || busy}
              onClick={() => replace({ ...fresh(), responsible_id: user?.id })}
            >
              <Plus size={17} />
              新建知识
            </button>
          </>
        }
      />
      {todos.data?.items?.length ? (
        <section className="panel">
          <div className="list-toolbar">
            <strong>资料到期提醒</strong>
            <span>共 {todos.data.total} 条待办</span>
            <button className="button secondary small" onClick={todos.reload}>
              刷新
            </button>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>提醒</th>
                  <th>负责人</th>
                  <th>到期时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {todos.data.items.map((t: any) => (
                  <tr key={t.id}>
                    <td>
                      <Badge tone={t.kind === "expired" ? "amber" : "muted"}>
                        {t.kind === "expired" ? "已过期" : "即将到期"}
                      </Badge>
                      <div>
                        <strong>{t.title}</strong>
                        <small className="cell-sub">{t.message}</small>
                      </div>
                    </td>
                    <td>{t.responsible_name}</td>
                    <td>{date(t.due_at, true)}</td>
                    <td>
                      <button
                        className="text-link"
                        onClick={() => open(t.document_id)}
                      >
                        打开资料
                      </button>
                      {can("knowledge.write") && (
                        <button
                          className="button secondary small"
                          disabled={busy}
                          onClick={async () => {
                            try {
                              await api(
                                "/admin/knowledge-todos/" + t.id + "/complete",
                                {
                                  method: "POST",
                                  body: JSON.stringify({ action: "done" }),
                                },
                              );
                              todos.reload();
                              toast("待办已完成");
                            } catch (e: any) {
                              toast(e.message, true);
                            }
                          }}
                        >
                          标记完成
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="footnote">
            扫描 PDF 已启用
            OCR（中英）。过期与内部资料不会进入公开回答。单文件上限 50 MB。
          </p>
        </section>
      ) : null}
      <div className="knowledge-summary">
        <BookOpen size={20} />
        <span>
          筛选共 <strong>{r.data?.total || 0}</strong> 份资料
        </span>
        <small>
          草稿与正式资料独立 · 支持 Markdown、TXT、PDF（含扫描
          OCR）、DOCX；单文件上限 50 MB
        </small>
      </div>
      <div className={"knowledge-layout " + (edit ? "with-editor" : "")}>
        <section className="panel document-panel">
          <Filters
            list={list}
            owners={people.data || []}
            business={false}
            placeholder="搜索知识名称"
          />
          <div className="toolbar">
            <Select
              aria-label="知识状态筛选"
              value={list.filters.status || ""}
              onValueChange={(value) => list.change("status", value)}
            >
              <option value="">全部状态</option>
              {[
                "published",
                "draft",
                "submitted",
                "scheduled",
                "expired",
                "archived",
              ].map((v) => (
                <option value={v} key={v}>
                  {statusLabel[v]}
                </option>
              ))}
            </Select>
            <Select
              aria-label="可见范围筛选"
              value={list.filters.visibility || ""}
              onValueChange={(value) => list.change("visibility", value)}
            >
              <option value="">全部可见范围</option>
              <option value="public">公开</option>
              <option value="internal">内部</option>
            </Select>
          </div>
          {r.loading ? (
            <Loading />
          ) : r.error ? (
            <ErrorBox text={r.error} retry={r.reload} />
          ) : docs.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>知识名称</th>
                    <th>正式 / 草稿状态</th>
                    <th>适用范围</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {docs.map((d) => (
                    <tr
                      key={d.id}
                      className={edit?.id === d.id ? "selected-row" : ""}
                    >
                      <td>
                        <button
                          className="document-link"
                          onClick={() => open(d.id)}
                        >
                          <span className="file-icon">
                            <FileText size={19} />
                          </span>
                          <span>
                            <strong>{d.title}</strong>
                            <small>
                              {d.category}
                              {d.is_demo ? " · 示例资料" : ""}
                            </small>
                          </span>
                        </button>
                      </td>
                      <td>
                        <Badge
                          tone={
                            d.effective_status === "published"
                              ? "green"
                              : "muted"
                          }
                        >
                          {d.version ? "v" + d.version + " · " : ""}
                          {statusLabel[d.effective_status]}
                        </Badge>
                        {d.draft_status && (
                          <small className="visibility">
                            <Badge tone="amber">
                              v{d.draft_number} {statusLabel[d.draft_status]}
                            </Badge>
                          </small>
                        )}
                      </td>
                      <td>
                        <small>
                          {d.exam_type} · {d.region}
                          <br />
                          {d.exam_year
                            ? d.exam_year + " 年度"
                            : "未限制年度"} · {statusLabel[d.visibility]}
                        </small>
                      </td>
                      <td>
                        <button
                          className="icon-button"
                          aria-label={"查看" + d.title}
                          onClick={() => open(d.id)}
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
              title="没有匹配的知识"
              description="试试其他关键词，或创建新的企业资料。"
            />
          )}
          <Pager list={list} />
        </section>
        {edit && (
          <section className="panel editor-panel">
            <div className="panel-heading">
              <div>
                <h2>{edit.id ? "知识详情" : "新建知识"}</h2>
                <small>
                  {edit.version ? "正式版本 v" + edit.version : "尚无正式版本"}
                  {edit.draft_status
                    ? " · " + statusLabel[edit.draft_status]
                    : ""}
                </small>
              </div>
              <button
                className="icon-button"
                aria-label="关闭编辑器"
                disabled={dirty || busy}
                onClick={() => setEdit(null)}
              >
                <X size={18} />
              </button>
            </div>
            {error && <ErrorBox text={error} />}
            {dirty && (
              <div className="draft-warning">
                <span>有未保存的修改</span>
                <button
                  className="text-link"
                  disabled={busy}
                  onClick={async () => {
                    try {
                      replace(
                        edit.id
                          ? await api("/admin/documents/" + edit.id)
                          : { ...fresh(), responsible_id: user?.id },
                      );
                    } catch (e: any) {
                      setError(e.message);
                    }
                  }}
                >
                  放弃并重新加载
                </button>
              </div>
            )}
            {locked && (
              <p className="review-notice">
                已提交审核，当前草稿已锁定。需要修改时由管理员退回。
              </p>
            )}
            {edit.knowledge_type === "course" && (
              <p className="review-notice">
                课程文件作为参考存档。机器人的课程、价格、课时和开班安排统一读取“课程与班次”，请在那里维护当前业务信息。
              </p>
            )}
            {edit.review_note && (
              <p className="review-notice">审核意见：{edit.review_note}</p>
            )}
            <fieldset disabled={busy || locked} className="knowledge-fields">
              <label>
                资料名称
                <input
                  maxLength={240}
                  value={edit.title}
                  onChange={(e) => change("title", e.target.value)}
                />
              </label>
              <div className="form-row">
                <label>
                  分类
                  <input
                    maxLength={100}
                    value={edit.category}
                    onChange={(e) => change("category", e.target.value)}
                  />
                </label>
                <label>
                  知识类型
                  <Select
                    value={edit.knowledge_type}
                    onValueChange={(value) => change("knowledge_type", value)}
                  >
                    <option value="general">通用说明</option>
                    <option value="course">课程资料</option>
                    <option value="policy">具体考试政策</option>
                  </Select>
                </label>
              </div>
              <div className="form-row">
                <label>
                  适用考试
                  <Select
                    value={edit.exam_type}
                    onValueChange={(value) => change("exam_type", value)}
                  >
                    {exams.map((v) => (
                      <option key={v}>{v}</option>
                    ))}
                  </Select>
                </label>
                <label>
                  适用年度
                  <input
                    type="number"
                    min={2000}
                    max={2100}
                    placeholder="不限年度"
                    value={edit.exam_year ?? ""}
                    onChange={(e) =>
                      change(
                        "exam_year",
                        e.target.value ? Number(e.target.value) : null,
                      )
                    }
                  />
                </label>
              </div>
              <div className="form-row">
                <label>
                  适用地区
                  <input
                    maxLength={80}
                    placeholder="全国 / 江苏 / 浙江…"
                    value={edit.region}
                    onChange={(e) => change("region", e.target.value)}
                  />
                </label>
                <label>
                  可见范围
                  <Select
                    value={edit.visibility}
                    onValueChange={(value) => change("visibility", value)}
                  >
                    <option value="public">对外公开</option>
                    <option value="internal">内部资料</option>
                  </Select>
                </label>
              </div>
              <label>
                来源网址
                <input
                  type="url"
                  maxLength={2000}
                  placeholder="https://…"
                  value={edit.source_url || ""}
                  onChange={(e) => change("source_url", e.target.value)}
                />
              </label>
              <label>
                资料负责人
                <Select
                  value={edit.responsible_id || ""}
                  onValueChange={(value) => change("responsible_id", value)}
                >
                  <option value="">请选择负责人</option>
                  {edit.responsible_id &&
                    !people.data?.some((p) => p.id === edit.responsible_id) && (
                      <option value={edit.responsible_id}>
                        {edit.responsible_name || "当前负责人"}（需重新指定）
                      </option>
                    )}
                  {people.data?.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </Select>
              </label>
              <div className="form-row date-fields">
                <label>
                  生效时间（北京时间）
                  <input
                    type="datetime-local"
                    value={localTime(edit.valid_from)}
                    onChange={(e) =>
                      change("valid_from", isoTime(e.target.value))
                    }
                  />
                </label>
                <label>
                  失效时间（北京时间）
                  <input
                    type="datetime-local"
                    value={localTime(edit.valid_until)}
                    onChange={(e) =>
                      change("valid_until", isoTime(e.target.value))
                    }
                  />
                </label>
              </div>
              <p className="footnote">
                留空表示不限制。到期资料自动停止引用；年度和地区未明确时，机器人不会套用特定政策。
                发布未来生效的版本会替换旧正式版本，生效前该资料暂停引用。
              </p>
              <label>
                资料正文
                <textarea
                  className="knowledge-content"
                  rows={12}
                  value={edit.content}
                  onChange={(e) => change("content", e.target.value)}
                />
              </label>
              {edit.knowledge_type === "policy" && (
                <label className="checkbox-line">
                  <input
                    type="checkbox"
                    checked={edit.policy_verified}
                    onChange={(e) =>
                      change("policy_verified", e.target.checked)
                    }
                  />
                  我已人工核对来源公告及考试、年度、地区
                </label>
              )}
            </fieldset>
            {people.error && (
              <ErrorBox text={people.error} retry={people.reload} />
            )}
            <button
              className="button full"
              disabled={
                busy ||
                locked ||
                !edit.title.trim() ||
                !edit.content.trim() ||
                (edit.id && !dirty)
              }
              onClick={save}
            >
              <Save size={16} />
              {busy
                ? "处理中…"
                : edit.id && !edit.draft_status
                  ? "保存为新草稿"
                  : "保存草稿"}
            </button>
            {edit.id && (
              <div className="knowledge-workflow">
                <label>
                  审核 / 回滚说明
                  <textarea
                    rows={2}
                    maxLength={1000}
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="退回时必填；发布和回滚可注明原因"
                  />
                </label>
                <div className="actions wrap-actions">
                  {edit.draft_status === "draft" && (
                    <button
                      className="button secondary small"
                      disabled={busy || dirty}
                      onClick={() => act("submit")}
                    >
                      提交审核
                    </button>
                  )}
                  {can("knowledge.publish") && edit.draft_status && (
                    <button
                      className="button small"
                      disabled={busy || dirty}
                      onClick={() => act("publish")}
                    >
                      审核并发布 v{edit.draft_number}
                    </button>
                  )}
                  {can("knowledge.publish") && locked && (
                    <button
                      className="button secondary small"
                      disabled={busy || dirty || !note.trim()}
                      onClick={() => act("reject")}
                    >
                      退回修改
                    </button>
                  )}
                  {can("knowledge.publish") && edit.status === "published" && (
                    <button
                      className="button secondary small"
                      disabled={busy || dirty}
                      onClick={() => act("archive")}
                    >
                      下架正式版本
                    </button>
                  )}
                  <button
                    className="button secondary small"
                    disabled={busy}
                    onClick={loadHistory}
                  >
                    <History size={15} />
                    版本历史
                  </button>
                  {edit.status === "published" && can("knowledge.write") && (
                    <button
                      className="button secondary small"
                      disabled={busy}
                      onClick={async () => {
                        setBusy(true);
                        try {
                          await api("/admin/document-jobs/reindex/" + edit.id, {
                            method: "POST",
                          });
                          toast("重建任务已排队，请到文档处理任务查看进度");
                        } catch (e: any) {
                          toast(e.message, true);
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      重建向量索引
                    </button>
                  )}
                </div>
                {dirty && (
                  <p className="footnote">保存草稿后才能提交审核或发布。</p>
                )}
              </div>
            )}
            {history && (
              <div className="revision-panel">
                <h3>版本历史</h3>
                <p className="footnote">
                  v0.1 资料以当前内容补建历史基线，早期覆盖的正文无法追溯。
                </p>
                {history.map((h) => (
                  <button
                    className={
                      "revision-row " +
                      (preview?.id === h.id ? "selected-row" : "")
                    }
                    key={h.id}
                    onClick={() => viewRevision(h.id)}
                    disabled={busy}
                  >
                    <strong>
                      v{h.number} · {statusLabel[h.status]}
                    </strong>
                    <small>
                      {h.author_name} · {date(h.updated_at, true)}
                      {h.reviewer_name ? " · 审核 " + h.reviewer_name : ""}
                    </small>
                  </button>
                ))}
                {preview && (
                  <article className="revision-preview">
                    <h3>
                      v{preview.number} · {preview.data.title}
                    </h3>
                    <p className="footnote">
                      {preview.data.exam_type} · {preview.data.region} ·{" "}
                      {preview.data.exam_year || "不限年度"} ·{" "}
                      {statusLabel[preview.data.visibility]}
                      <br />
                      来源：{preview.data.source_url || "未填写"}
                      <br />
                      审核意见：{preview.review_note || "无"}
                    </p>
                    <pre>{preview.data.content}</pre>
                    {can("knowledge.publish") &&
                      ["published", "superseded"].includes(preview.status) && (
                        <>
                          <button
                            className="button secondary full"
                            disabled={
                              busy || dirty || Boolean(edit.draft_status)
                            }
                            onClick={() =>
                              act("restore", { revision_id: preview.id })
                            }
                          >
                            将 v{preview.number} 恢复为新版本并发布
                          </button>
                          {edit.draft_status && (
                            <p className="footnote">
                              已有待处理草稿，请先完成审核后回滚。
                            </p>
                          )}
                        </>
                      )}
                  </article>
                )}
              </div>
            )}
          </section>
        )}
      </div>
      <section className="panel retrieval-panel">
        <div className="panel-heading">
          <div>
            <h2>
              <FlaskConical size={18} />
              检索验证
            </h2>
            <p>
              仅检索当前有效的正式公开资料。政策问题请明确考试、年度和地区。
            </p>
          </div>
          <Badge tone="blue">公众范围</Badge>
        </div>
        <div className="retrieval-input">
          <input
            aria-label="检索验证问题"
            value={testQuery}
            onChange={(e) => setTestQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && test()}
          />
          <button
            className="button secondary"
            disabled={testBusy || !testQuery.trim()}
            onClick={test}
          >
            {testBusy ? "检索中…" : "验证检索"}
          </button>
        </div>
        {results && (
          <div className="retrieval-results">
            <p className="footnote">
              {results.mode === "hybrid" ? "中文向量＋关键词" : "中文关键词"} ·
              找到 {results.sources.length} 个片段 · 考试{" "}
              {results.context?.exam_type || "未明确"} / 年度{" "}
              {results.context?.exam_year || "未明确"} / 地区{" "}
              {results.context?.region || "未明确"}
            </p>
            {results.sources.length ? (
              results.sources.map((s: any, i: number) => (
                <article key={s.chunk_id}>
                  <strong>
                    [{i + 1}] {s.title}
                  </strong>
                  <small>
                    版本 {s.version} · {s.exam_type} · {s.region} ·{" "}
                    {s.exam_year || "不限年度"}
                  </small>
                  <p>{s.text}</p>
                </article>
              ))
            ) : (
              <Empty
                title="没有足够依据"
                description="请检查发布状态、有效期和适用的考试、年度、地区。"
              />
            )}
          </div>
        )}
      </section>
    </>
  );
}
