import { Select } from "./Select";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, date, useResource } from "./api";
import { Badge, Empty, ErrorBox, Loading, PageTitle, useToast } from "./ui";
import { Filters, Pager, usePaged } from "./lists";
import { exampleBatchCopy } from "./displayCopy";
export function Todos({
  includeDemo,
  version,
}: {
  includeDemo: boolean;
  version: number;
}) {
  const list = usePaged("/admin/todos", includeDemo, version),
    r = list.resource,
    owners = useResource<any[]>("/admin/filter-options", version),
    toast = useToast();
  const [completing, setCompleting] = useState("");
  async function complete(todo: any) {
    if (completing) return;
    setCompleting(todo.todo_key);
    try {
      const path =
        todo.todo_type === "trial"
          ? "/admin/trials/" + todo.trial_id + "/complete-followup"
          : "/admin/leads/" + todo.id + "/complete-followup";
      await api(path, {
        method: "POST",
        body: JSON.stringify({
          content: "已完成本次跟进",
          ...(todo.todo_type === "trial" ? { version: todo.todo_version } : {}),
        }),
      });
      r.reload();
      toast("跟进待办已完成");
    } catch (e: any) {
      toast(e.message, true);
      r.reload();
    } finally {
      setCompleting("");
    }
  }
  return (
    <>
      <PageTitle
        title="跟进待办"
        description="只提醒允许联系且未结案的线索。停止联系后自动退出待办，历史记录保留。"
      />
      <section className="panel">
        <Filters list={list} owners={owners.data || []} />
        <div className="toolbar">
          <Select
            aria-label="待办时间范围"
            value={list.filters.state || "all"}
            onValueChange={(value) => list.change("state", value)}
          >
            <option value="all">全部待办</option>
            <option value="overdue">已逾期</option>
            <option value="today">今天</option>
            <option value="upcoming">待跟进</option>
          </Select>
        </div>
        {r.loading ? (
          <Loading />
        ) : r.error ? (
          <ErrorBox text={r.error} retry={r.reload} />
        ) : r.data?.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>咨询人</th>
                  <th>待办事项</th>
                  <th>下次跟进</th>
                  <th>负责人</th>
                  <th>阶段</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {r.data.items.map((l) => (
                  <tr key={l.todo_key}>
                    <td>
                      <Link to={"/leads?lead_id=" + l.id}>{l.name}</Link>
                      <small className="visibility">{l.contact}</small>
                    </td>
                    <td>
                      {l.todo_type === "trial" ? (
                        <>
                          <Link to={"/trials?trial_id=" + l.trial_id}>
                            {l.todo_label}
                          </Link>
                          <small className="visibility">
                            {l.course_name} ·{" "}
                            {exampleBatchCopy(l.batch_name, l.is_demo)}
                            {l.is_demo ? "（示例）" : ""}
                          </small>
                        </>
                      ) : (
                        l.todo_label
                      )}
                    </td>
                    <td>
                      {date(l.next_followup_at, true)}{" "}
                      {new Date(l.next_followup_at) < new Date() && (
                        <Badge tone="amber">逾期</Badge>
                      )}
                    </td>
                    <td>{l.owner}</td>
                    <td>{l.stage}</td>
                    <td>
                      <button
                        className="button secondary small"
                        disabled={!!completing}
                        onClick={() => complete(l)}
                      >
                        {completing === l.todo_key ? "处理中…" : "完成此待办"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="暂无待办"
            description="线索跟进和试听预约生成的待办会在这里分别提醒。"
          />
        )}
        <Pager list={list} />
      </section>
    </>
  );
}
