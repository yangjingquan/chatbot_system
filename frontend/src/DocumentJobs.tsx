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
export function DocumentJobs({ version }: { version: number }) {
  const [status, setStatus] = useState(""),
    [page, setPage] = useState(1),
    [busy, setBusy] = useState("");
  const list = useResource<any>(
      `/admin/document-jobs?status=${status}&page=${page}&page_size=20`,
      version,
    ),
    toast = useToast(),
    { can } = useAccess();
  useEffect(() => {
    const t = setInterval(list.reload, 5000);
    return () => clearInterval(t);
  }, []);
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
  return (
    <>
      <PageTitle
        title="文档处理任务"
        description="文件解析成草稿后仍需审核发布。发布文字索引即时生效，后台继续构建向量索引。"
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
          <button className="button secondary small" onClick={list.reload}>
            刷新任务
          </button>
          <Link to="/knowledge">返回知识库上传文件</Link>
        </div>
        {list.error ? (
          <ErrorBox text={list.error} />
        ) : list.loading && !list.data ? (
          <Loading />
        ) : !list.data?.items.length ? (
          <Empty
            title="暂无文档任务"
            description="上传文件或发布资料后，处理进度会显示在这里。"
          />
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
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
                      {j.kind === "parse" ? "文件解析" : "向量索引"}
                      <small className="cell-sub">{j.id.slice(0, 8)}</small>
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
                    <td>{j.error || "—"}</td>
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
                        j.attempts < 3 && (
                          <button
                            className="button secondary small"
                            disabled={busy === j.id}
                            onClick={() => retry(j.id)}
                          >
                            重试任务
                          </button>
                        )}
                      {j.status === "failed" && j.attempts >= 3 && (
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
        任务保存在数据库。工作进程意外退出后，租约过期会重新领取，最多尝试 3
        次；超过上限保留失败原因。旧版本任务不能覆盖新版本索引。
      </p>
    </>
  );
}
