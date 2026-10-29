import { Select } from "./Select";
import { useEffect, useRef, useState } from "react";
import { Plus, Save, X } from "lucide-react";
import { api } from "./api";
import { useAccess } from "./auth";
import { Badge, Empty, ErrorBox, Loading, PageTitle, useToast } from "./ui";
import { exams, isoTime, localTime, Pager, usePaged } from "./lists";
import { batchDisplayName } from "./displayCopy";

const blankCourse = () => ({
  name: "",
  exam_type: "国考",
  hours: 40,
  class_type: "基础班",
  description: "",
  status: "active",
});
const blankBatch = () => ({
  name: "",
  mode: "online",
  location: "",
  schedule: "待人工确认",
  starts_at: null,
  ends_at: null,
  price_cents: null,
  price_from: null,
  price_until: null,
  sale_status: "draft",
});
const saleLabels: Record<string, string> = {
  draft: "草稿",
  open: "可报名",
  paused: "停售",
  sold_out: "已满班",
  ended: "已结束",
};
const courseSnapshot = (c: any) =>
  JSON.stringify(
    c && [
      c.name,
      c.exam_type,
      String(c.hours),
      c.class_type,
      c.description,
      c.status,
    ],
  );
const batchSnapshot = (b: any, price: string) =>
  JSON.stringify(
    b && [
      b.name,
      b.mode,
      b.location,
      b.schedule,
      b.starts_at,
      b.ends_at,
      b.sale_status,
      b.price_from,
      b.price_until,
      price,
    ],
  );
export function Courses({ version }: { version: number }) {
  const list = usePaged("/admin/courses", true, version),
    r = list.resource,
    { can } = useAccess(),
    toast = useToast();
  const [edit, setEdit] = useState<any>(null),
    [batch, setBatch] = useState<any>(null),
    [price, setPrice] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const write = can("courses.write");
  const originalCourse = useRef("null"),
    originalBatch = useRef("null");
  const courseDirty = !!edit && courseSnapshot(edit) !== originalCourse.current;
  const batchDirty =
    !!batch && batchSnapshot(batch, price) !== originalBatch.current;
  function mayDiscard() {
    return (
      !(courseDirty || batchDirty) ||
      window.confirm("有未保存的课程或班次修改，确定放弃吗？")
    );
  }
  useEffect(() => {
    if (!courseDirty && !batchDirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    const guardLink = (event: MouseEvent) => {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      const link =
        event.target instanceof Element
          ? (event.target.closest("a[href]") as HTMLAnchorElement | null)
          : null;
      if (
        !link ||
        link.target === "_blank" ||
        link.origin !== window.location.origin ||
        link.pathname === window.location.pathname
      )
        return;
      if (!mayDiscard()) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", warn);
    document.addEventListener("click", guardLink, true);
    return () => {
      window.removeEventListener("beforeunload", warn);
      document.removeEventListener("click", guardLink, true);
    };
  }, [courseDirty, batchDirty]);
  function select(c: any) {
    originalCourse.current = courseSnapshot(c);
    originalBatch.current = "null";
    setEdit({ ...c });
    setBatch(null);
    setError("");
  }
  async function open(id: string) {
    if (!mayDiscard()) return;
    try {
      select(await api("/admin/courses/" + id));
    } catch (e: any) {
      toast(e.message, true);
    }
  }
  async function saveCourse() {
    if (
      batchDirty &&
      !window.confirm("保存课程会关闭未保存的班次修改，确定放弃吗？")
    )
      return;
    setBusy(true);
    setError("");
    try {
      const { name, exam_type, hours, class_type, description, status } = edit;
      const result = await api(
        edit.id ? "/admin/courses/" + edit.id : "/admin/courses",
        {
          method: edit.id ? "PATCH" : "POST",
          body: JSON.stringify({
            name,
            exam_type,
            hours: Number(hours),
            class_type,
            description,
            status,
            ...(edit.id ? { expected_version: edit.version } : {}),
          }),
        },
      );
      select(result);
      r.reload();
      toast("课程业务数据已保存，机器人将使用最新信息");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function saveBatch() {
    if (
      courseDirty &&
      !window.confirm(
        "保存班次会重新加载课程，未保存的课程修改将丢失，确定继续吗？",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      if (price && !/^\d+(\.\d{1,2})?$/.test(price))
        throw new Error("价格最多保留两位小数");
      const payload = {
        ...batch,
        price_cents: price === "" ? null : Math.round(Number(price) * 100),
        ...(batch.id ? { expected_version: batch.version } : {}),
      };
      await api(
        batch.id
          ? "/admin/batches/" + batch.id
          : "/admin/courses/" + edit.id + "/batches",
        { method: batch.id ? "PATCH" : "POST", body: JSON.stringify(payload) },
      );
      select(await api("/admin/courses/" + edit.id));
      r.reload();
      toast("班次、价格与销售状态已更新");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  function editBatch(b: any) {
    if (batchDirty && !window.confirm("当前班次有未保存的修改，确定放弃吗？"))
      return;
    const nextPrice =
      b.price_cents == null ? "" : (b.price_cents / 100).toFixed(2);
    originalBatch.current = batchSnapshot(b, nextPrice);
    setBatch({ ...b });
    setPrice(nextPrice);
    setError("");
  }
  return (
    <>
      <PageTitle
        title="课程与班次"
        description="统一维护课程、价格和开班安排；机器人实时读取有效业务数据。"
        actions={
          write && (
            <button
              className="button"
              onClick={() => {
                if (mayDiscard()) select(blankCourse());
              }}
            >
              <Plus size={16} />
              新增课程
            </button>
          )
        }
      />
      <section className="panel">
        <div className="toolbar course-filters">
          <input
            aria-label="搜索课程"
            placeholder="搜索课程名称"
            value={list.filters.q || ""}
            onChange={(e) => list.change("q", e.target.value)}
          />
          <Select
            aria-label="课程考试"
            value={list.filters.exam_type || ""}
            onValueChange={(value) => list.change("exam_type", value)}
          >
            <option value="">全部考试</option>
            {exams.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </Select>
          <Select
            aria-label="课程销售状态"
            value={list.filters.status || ""}
            onValueChange={(value) => list.change("status", value)}
          >
            <option value="">全部状态</option>
            <option value="active">正常销售</option>
            <option value="paused">已停售</option>
          </Select>
        </div>
      </section>
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
                    <th>课程</th>
                    <th>考试 / 班型</th>
                    <th>课时</th>
                    <th>状态</th>
                    <th>班次</th>
                  </tr>
                </thead>
                <tbody>
                  {r.data.items.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <button
                          className="document-link"
                          onClick={() => open(c.id)}
                        >
                          <strong>{c.name}</strong>
                        </button>
                      </td>
                      <td>
                        {c.exam_type} · {c.class_type}
                      </td>
                      <td>{c.hours}</td>
                      <td>
                        <Badge tone={c.status === "active" ? "green" : "muted"}>
                          {c.status === "active" ? "销售中" : "已停售"}
                        </Badge>
                      </td>
                      <td>{c.batches.length}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              title="暂无课程"
              description="管理员可新建课程并配置班次。"
            />
          )}
          <Pager list={list} />
        </section>
        {edit && (
          <aside className="panel lead-detail">
            <div className="panel-heading">
              <h2>{edit.id ? edit.name : "新建课程"}</h2>
              <button
                className="icon-button"
                aria-label="关闭课程详情"
                onClick={() => {
                  if (mayDiscard()) {
                    setEdit(null);
                    setBatch(null);
                  }
                }}
              >
                <X size={18} />
              </button>
            </div>
            {error && <ErrorBox text={error} />}
            <fieldset className="knowledge-fields" disabled={busy || !write}>
              <label>
                课程名称
                <input
                  maxLength={160}
                  value={edit.name}
                  onChange={(e) => setEdit({ ...edit, name: e.target.value })}
                />
              </label>
              <div className="form-row">
                <label>
                  考试
                  <Select
                    value={edit.exam_type}
                    onValueChange={(value) =>
                      setEdit({ ...edit, exam_type: value })
                    }
                  >
                    {exams.map((x) => (
                      <option key={x}>{x}</option>
                    ))}
                  </Select>
                </label>
                <label>
                  班型
                  <input
                    maxLength={80}
                    value={edit.class_type}
                    onChange={(e) =>
                      setEdit({ ...edit, class_type: e.target.value })
                    }
                  />
                </label>
              </div>
              <div className="form-row">
                <label>
                  课时数
                  <input
                    type="number"
                    min={1}
                    max={10000}
                    value={edit.hours}
                    onChange={(e) =>
                      setEdit({ ...edit, hours: e.target.value })
                    }
                  />
                </label>
                <label>
                  课程状态
                  <Select
                    value={edit.status}
                    onValueChange={(value) =>
                      setEdit({ ...edit, status: value })
                    }
                  >
                    <option value="active">销售中</option>
                    <option value="paused">停售</option>
                  </Select>
                </label>
              </div>
              <label>
                课程说明
                <textarea
                  rows={4}
                  value={edit.description}
                  maxLength={6000}
                  onChange={(e) =>
                    setEdit({ ...edit, description: e.target.value })
                  }
                />
              </label>
            </fieldset>
            {write && (
              <button
                className="button full"
                disabled={busy || !edit.name.trim() || !edit.class_type.trim()}
                onClick={saveCourse}
              >
                <Save size={16} />
                保存课程
              </button>
            )}
            {edit.id && (
              <>
                <div className="row-between section-space">
                  <h3>班次与价格</h3>
                  {write && (
                    <button
                      className="button secondary small"
                      onClick={() => editBatch(blankBatch())}
                    >
                      新增班次
                    </button>
                  )}
                </div>
                {edit.batches?.map((b: any) => (
                  <button
                    className="revision-row"
                    key={b.id}
                    onClick={() => editBatch(b)}
                  >
                    <strong>
                      {batchDisplayName(b.name)} ·{" "}
                      {saleLabels[b.sale_status]}
                    </strong>
                    <small>
                      {b.mode === "online"
                        ? "线上"
                        : b.mode === "offline"
                          ? "线下"
                          : "混合"}{" "}
                      ·{" "}
                      {b.price_cents == null
                        ? "未报价"
                        : (b.price_cents / 100).toFixed(2) + " 元"}{" "}
                      · v{b.version}
                    </small>
                  </button>
                ))}
              </>
            )}
            {batch && (
              <div className="knowledge-workflow">
                <h3>{batch.id ? "编辑班次" : "新增班次"}</h3>
                <fieldset
                  disabled={busy || !write}
                  className="knowledge-fields"
                >
                  <label>
                    班次名称
                    <input
                      value={batch.name}
                      maxLength={160}
                      onChange={(e) =>
                        setBatch({ ...batch, name: e.target.value })
                      }
                    />
                  </label>
                  <div className="form-row">
                    <label>
                      授课方式
                      <Select
                        value={batch.mode}
                        onValueChange={(value) =>
                          setBatch({ ...batch, mode: value })
                        }
                      >
                        <option value="online">线上</option>
                        <option value="offline">线下</option>
                        <option value="hybrid">混合</option>
                      </Select>
                    </label>
                    <label>
                      销售状态
                      <Select
                        value={batch.sale_status}
                        onValueChange={(value) =>
                          setBatch({ ...batch, sale_status: value })
                        }
                      >
                        {Object.entries(saleLabels).map(([k, v]) => (
                          <option value={k} key={k}>
                            {v}
                          </option>
                        ))}
                      </Select>
                    </label>
                  </div>
                  <label>
                    上课地点
                    <input
                      value={batch.location}
                      maxLength={240}
                      onChange={(e) =>
                        setBatch({ ...batch, location: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    开班安排
                    <textarea
                      value={batch.schedule}
                      maxLength={3000}
                      rows={3}
                      onChange={(e) =>
                        setBatch({ ...batch, schedule: e.target.value })
                      }
                    />
                  </label>
                  {[
                    ["starts_at", "开班时间"],
                    ["ends_at", "结束时间"],
                  ].map(([key, label]) => (
                    <label key={key}>
                      {label}（北京时间）
                      <input
                        type="datetime-local"
                        value={localTime(batch[key])}
                        onChange={(e) =>
                          setBatch({ ...batch, [key]: isoTime(e.target.value) })
                        }
                      />
                    </label>
                  ))}
                  <label>
                    价格（元，留空待人工报价）
                    <input
                      type="number"
                      min={0}
                      max={1000000}
                      step="0.01"
                      value={price}
                      onChange={(e) => setPrice(e.target.value)}
                    />
                  </label>
                  {[
                    ["price_from", "价格生效时间"],
                    ["price_until", "价格失效时间"],
                  ].map(([key, label]) => (
                    <label key={key}>
                      {label}（北京时间）
                      <input
                        type="datetime-local"
                        value={localTime(batch[key])}
                        onChange={(e) =>
                          setBatch({ ...batch, [key]: isoTime(e.target.value) })
                        }
                      />
                    </label>
                  ))}
                </fieldset>
                <p className="footnote">
                  过期报价、停售、满班或已结束班次不会用于当前报价。草稿班次不对外展示。
                </p>
                {write && (
                  <button
                    className="button full"
                    disabled={busy || !batch.name.trim()}
                    onClick={saveBatch}
                  >
                    保存班次与价格
                  </button>
                )}
              </div>
            )}
          </aside>
        )}
      </div>
    </>
  );
}
