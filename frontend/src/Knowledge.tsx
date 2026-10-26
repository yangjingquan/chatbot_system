import { useRef, useState } from "react";
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
  Globe,
  LockKeyhole,
} from "lucide-react";
import { api, date, statusLabel, useResource } from "./api";
import { Badge, Empty, ErrorBox, Loading, PageTitle, useToast } from "./ui";

export function Knowledge({
  version,
  onChange,
}: {
  version: number;
  onChange: () => void;
}) {
  const r = useResource<any[]>("/admin/documents", version),
    toast = useToast();
  const [query, setQuery] = useState(""),
    [filter, setFilter] = useState("all"),
    [edit, setEdit] = useState<any>(null),
    [busy, setBusy] = useState(false);
  const [testQuery, setTestQuery] = useState("公安联考基础班多少钱？"),
    [results, setResults] = useState<any>(null),
    [testBusy, setTestBusy] = useState(false);
  const upload = useRef<HTMLInputElement>(null);
  const docs = (r.data || []).filter(
    (d) =>
      (d.title + d.category).includes(query) &&
      (filter === "all" || d.status === filter),
  );
  async function open(id: string) {
    try {
      setEdit(await api("/admin/documents/" + id));
    } catch (e: any) {
      toast(e.message, true);
    }
  }
  async function save() {
    setBusy(true);
    try {
      const payload = {
        title: edit.title,
        content: edit.content,
        category: edit.category,
        status: edit.status,
        visibility: edit.visibility,
      };
      const result = await api(
        edit.id ? "/admin/documents/" + edit.id : "/admin/documents",
        { method: edit.id ? "PATCH" : "POST", body: JSON.stringify(payload) },
      );
      setEdit(result);
      onChange();
      toast("知识已保存，发布状态已更新");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function uploadFile(file: File) {
    setBusy(true);
    const body = new FormData();
    body.append("file", file);
    try {
      setEdit(await api("/admin/documents/upload", { method: "POST", body }));
      onChange();
      toast("文件已解析，请检查内容后发布");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
      if (upload.current) upload.current.value = "";
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
        description="审核后发布的公开资料，是机器人回答的依据。"
        actions={
          <>
            <input
              ref={upload}
              type="file"
              className="sr-only"
              accept=".md,.txt,.pdf,.docx"
              onChange={(e) =>
                e.target.files?.[0] && uploadFile(e.target.files[0])
              }
            />
            <button
              className="button secondary"
              disabled={busy}
              onClick={() => upload.current?.click()}
            >
              <Upload size={16} />
              上传文件
            </button>
            <button
              className="button"
              onClick={() =>
                setEdit({
                  title: "",
                  category: "未分类",
                  content: "",
                  visibility: "public",
                  status: "draft",
                })
              }
            >
              <Plus size={17} />
              新建知识
            </button>
          </>
        }
      />
      <div className="knowledge-summary">
        <BookOpen size={20} />
        <span>
          <strong>{r.data?.length || 0}</strong> 份资料
        </span>
        <span className="divider" />
        <span>
          <span className="status-dot green" />{" "}
          {r.data?.filter(
            (d) => d.status === "published" && d.visibility === "public",
          ).length || 0}{" "}
          份对外发布
        </span>
        <small>支持 Markdown、TXT、文本 PDF、DOCX · 单文件 5 MB</small>
      </div>
      <div className={"knowledge-layout " + (edit ? "with-editor" : "")}>
        <section className="panel document-panel">
          <div className="toolbar">
            <div className="tabs">
              {[
                ["all", "全部"],
                ["published", "已发布"],
                ["draft", "待审核"],
                ["archived", "已下架"],
              ].map(([v, label]) => (
                <button
                  className={filter === v ? "active" : ""}
                  key={v}
                  onClick={() => setFilter(v)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="search-field">
              <Search size={16} />
              <input
                aria-label="搜索知识"
                placeholder="搜索资料名称或分类"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
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
                    <th>状态 / 可见性</th>
                    <th>切片</th>
                    <th>更新时间</th>
                    <th>
                      <span className="sr-only">查看</span>
                    </th>
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
                              {d.is_demo ? " · 演示资料" : ""}
                            </small>
                          </span>
                        </button>
                      </td>
                      <td>
                        <Badge
                          tone={
                            d.status === "published"
                              ? "green"
                              : d.status === "draft"
                                ? "amber"
                                : "muted"
                          }
                        >
                          {statusLabel[d.status]}
                        </Badge>
                        <small className="visibility">
                          {d.visibility === "internal" ? (
                            <LockKeyhole size={11} />
                          ) : (
                            <Globe size={11} />
                          )}{" "}
                          {statusLabel[d.visibility]}
                        </small>
                      </td>
                      <td className="tabular">{d.chunks}</td>
                      <td className="muted nowrap">{date(d.updated_at)}</td>
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
              description="试试其他关键词，或上传一份新的企业资料。"
            />
          )}
        </section>
        {edit && (
          <section className="panel editor-panel">
            <div className="panel-heading">
              <h2>{edit.id ? "编辑知识" : "新建知识"}</h2>
              <button
                className="icon-button"
                aria-label="关闭编辑器"
                onClick={() => setEdit(null)}
              >
                <X size={18} />
              </button>
            </div>
            <label>
              资料名称
              <input
                value={edit.title}
                onChange={(e) => setEdit({ ...edit, title: e.target.value })}
              />
            </label>
            <div className="form-row">
              <label>
                分类
                <input
                  value={edit.category}
                  onChange={(e) =>
                    setEdit({ ...edit, category: e.target.value })
                  }
                />
              </label>
              <label>
                发布状态
                <select
                  value={edit.status}
                  onChange={(e) => setEdit({ ...edit, status: e.target.value })}
                >
                  {["draft", "published", "archived"].map((v) => (
                    <option value={v} key={v}>
                      {statusLabel[v]}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              可见范围
              <select
                value={edit.visibility}
                onChange={(e) =>
                  setEdit({ ...edit, visibility: e.target.value })
                }
              >
                <option value="public">对外公开 · 允许访客检索</option>
                <option value="internal">内部资料 · 禁止访客检索</option>
              </select>
            </label>
            <label>
              资料正文
              <textarea
                className="knowledge-content"
                rows={18}
                value={edit.content}
                onChange={(e) => setEdit({ ...edit, content: e.target.value })}
              />
            </label>
            <p className="footnote">
              保存为“已发布”表示管理员已审核。内部资料即使已发布，也不会用于公开客服回答。
            </p>
            <button
              className="button full"
              disabled={busy || !edit.title.trim() || !edit.content.trim()}
              onClick={save}
            >
              <Save size={16} />
              {busy ? "正在保存…" : "保存知识"}
            </button>
          </section>
        )}
      </div>
      <section className="panel retrieval-panel">
        <div className="panel-heading">
          <div>
            <h2>
              <FlaskConical size={18} /> 检索测试
            </h2>
            <p>模拟公众权限，检查某个问题会找到哪些已发布资料。</p>
          </div>
          <Badge tone="blue">公众范围</Badge>
        </div>
        <div className="retrieval-input">
          <input
            aria-label="检索测试问题"
            value={testQuery}
            onChange={(e) => setTestQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && test()}
          />
          <button
            className="button secondary"
            disabled={testBusy || !testQuery.trim()}
            onClick={test}
          >
            {testBusy ? "检索中…" : "测试检索"}
          </button>
        </div>
        {results && (
          <div className="retrieval-results">
            <p className="footnote">
              当前检索：
              {results.mode === "hybrid" ? "中文向量＋关键词" : "中文关键词"} ·
              找到 {results.sources.length} 个片段
            </p>
            {results.sources.length ? (
              results.sources.map((s: any, i: number) => (
                <article key={s.chunk_id}>
                  <strong>
                    [{i + 1}] {s.title}
                  </strong>
                  <small>
                    版本 {s.version} · {s.category}
                  </small>
                  <p>{s.text}</p>
                </article>
              ))
            ) : (
              <Empty
                title="没有足够依据"
                description="请检查资料是否发布、是否公开，以及问题是否在知识覆盖范围内。"
              />
            )}
          </div>
        )}
      </section>
    </>
  );
}
