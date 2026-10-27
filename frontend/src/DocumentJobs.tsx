import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, date, useResource } from "./api";
import { useAccess } from "./auth";
import { Badge, Empty, ErrorBox, Loading, PageTitle, useToast } from "./ui";
const labels: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  succeeded: "已完成",
  failed: "失败",
  superseded: "版本已更新",
};
const errorLabels: Record<string, string> = {
  size: "文件过大",
  type: "类型不支持",
  ocr: "需 OCR / 无文字",
  empty: "文件为空",
  parse: "解析失败",
  other: "其他失败",
};
export function DocumentJobs({ version }: { version: number }) {
  const [status, setStatus] = useState(""),
    [kind, setKind] = useState(""),
    [fileType, setFileType] = useState(""),
    [errorCode, setErrorCode] = useState(""),
    [page, setPage] = useState(1),
    [busy, setBusy] = useState(""),
    [selected, setSelected] = useState<string[]>([]);
  const query = new URLSearchParams(
    Object.entries({
      status,
      kind,
      file_type: fileType,
      error_code: errorCode,
      page: String(page),
      page_size: "20",
    }).filter(([, v]) => v !== ""),
  ).toString();
  const list = useResource<any>(`/admin/document-jobs?${query}`, version),
    toast = useToast(),
    { can } = useAccess();
  useEffect(() => {
    const t = setInterval(list.reload, 5000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => setSelected([]), [status, kind, fileType, errorCode, page]);
  async function retry(id: string) {
    setBusy(id);
    try {
      await api(`/admin/document-jobs/${id}/retry`, { method: "POST" });
      list.reload();
      toast("任务已重新排队");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy("");
    }
  }
  async function retrySelected() {
    if (!selected.length) return;
    setBusy("batch");
    try {
      const result = await api("/admin/document-jobs/retry-batch", {
        method: "POST",
        body: JSON.stringify({ job_ids: selected }),
      });
      list.reload();
      setSelected([]);
      toast(
        `批量重试：成功 ${result.succeeded}，跳过/失败 ${result.failed}`,
        !!result.failed,
      );
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy("");
    }
  }
  function toggle(id: string, checked: boolean) {
    setSelected((ids) =>
      checked ? Array.from(new Set([...ids, id])) : ids.filter((x) => x !== id),
    );
  }
  const failedRows =
    list.data?.items?.filter(
      (j: any) => j.status === "failed" && !j.validation_error && j.attempts < 3,
    ) || [];
  return (
    <>
      <PageTitle
        title="文档处理任务"
        description="支持批量上传：个别文件失败不影响其余文件。文件解析成草稿后仍需审核发布；发布文字索引即时生效，后台继续构建向量索引。扫描 PDF 已启用 OCR（中英），单文件上限 50 MB。"
      />
      <section className="panel">
        <div className="list-toolbar">
          <label>
            任务状态
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setPage(1);
              }}
            >
              <option value="">全部</option>
              {Object.entries(labels).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label>
            任务类型
            <select
              value={kind}
              onChange={(e) => {
                setKind(e.target.value);
                setPage(1);
              }}
            >
              <option value="">全部类型</option>
              <option value="parse">文件解析</option>
              <option value="index">向量索引</option>
            </select>
          </label>
          <label>
            文件类型
            <select
              value={fileType}
              onChange={(e) => {
                setFileType(e.target.value);
                setPage(1);
              }}
            >
              <option value="">全部后缀</option>
              {[".md", ".txt", ".pdf", ".docx"].map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label>
            失败原因
            <select
              value={errorCode}
              onChange={(e) => {
                setErrorCode(e.target.value);
                setPage(1);
              }}
            >
              <option value="">全部原因</option>
              {Object.entries(errorLabels).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <button className="button secondary small" onClick={list.reload}>
            刷新任务
          </button>
          {can("knowledge.write") && (
            <button
              className="button secondary small"
              disabled={!selected.length || busy === "batch"}
              onClick={retrySelected}
            >
              批量重试所选 ({selected.length})
            </button>
          )}
          {can("knowledge.write") && failedRows.length > 0 && (
            <button
              className="button secondary small"
              onClick={() =>
                setSelected(failedRows.map((j: any) => j.id as string))
              }
            >
              全选可重试失败项
            </button>
          )}
          <Link to="/knowledge">返回知识库上传文件</Link>
        </div>
        {list.error ? (
          <ErrorBox text={list.error} />
        ) : list.loading && !list.data ? (
          <Loading />
        ) : !list.data?.items.length ? (
          <Empty
            title="暂无文档任务"
            description="批量或单文件上传后，处理进度会显示在这里。"
          />
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th />
                  <th>任务</th>
                  <th>状态与进度</th>
                  <th>尝试次数</th>
                  <th>失败原因</th>
                  <th>更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((j: any) => (
                  <tr key={j.id}>
                    <td>
                      {can("knowledge.write") &&
                        j.status === "failed" &&
                        !j.validation_error &&
                        j.attempts < 3 && (
                          <input
                            type="checkbox"
                            aria-label="选择重试"
                            checked={selected.includes(j.id)}
                            onChange={(e) => toggle(j.id, e.target.checked)}
                          />
                        )}
                    </td>
                    <td>
                      {j.kind === "parse" ? "文件解析" : "向量索引"}
                      <small className="cell-sub">
                        {j.filename || j.id.slice(0, 8)}
                        {j.file_type ? ` · ${j.file_type}` : ""}
                      </small>
                    </td>
                    <td>
                      <Badge
                        tone={
                          j.status === "failed"
                            ? "amber"
                            : j.status === "succeeded"
                              ? "green"
                              : "muted"
                        }
                      >
                        {labels[j.status]}
                      </Badge>
                      <small className="cell-sub">
                        {j.stage} · {j.progress}%
                      </small>
                      <progress
                        max={100}
                        value={j.progress}
                        aria-label="任务进度"
                      />
                    </td>
                    <td>{j.attempts} / 3</td>
                    <td>
                      {j.error ? (
                        <>
                          {errorLabels[j.error_code]
                            ? `[${errorLabels[j.error_code]}] `
                            : ""}
                          {j.error}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>{date(j.updated_at, true)}</td>
                    <td>
                      {j.document_id && (
                        <Link
                          className="text-link"
                          to={"/knowledge?document_id=" + j.document_id}
                        >
                          查看资料
                        </Link>
                      )}
                      {can("knowledge.write") &&
                        j.status === "failed" &&
                        !j.validation_error &&
                        j.attempts < 3 && (
                          <button
                            className="button secondary small"
                            disabled={busy === j.id}
                            onClick={() => retry(j.id)}
                          >
                            重试任务
                          </button>
                        )}
                      {j.validation_error && (
                        <span>请修正后重新上传</span>
                      )}
                      {j.status === "failed" &&
                        !j.validation_error &&
                        j.attempts >= 3 && (
                          <span>请修正后重新上传或重建索引</span>
                        )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
      <p className="footnote">
        单文件上限 50
        MB；扫描 PDF 已启用 OCR。任务保存在数据库，工作进程意外退出后租约过期会重新领取，最多尝试
        3 次。校验失败（类型/大小）须重新上传，不能批量重试。
      </p>
    </>
  );
}
