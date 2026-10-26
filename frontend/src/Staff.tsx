import { useState, type FormEvent } from "react";
import { Plus, Save, ShieldCheck, X } from "lucide-react";
import { api, useResource } from "./api";
import { useAccess } from "./auth";
import { Badge, ErrorBox, Loading, PageTitle, useToast } from "./ui";

const roles = {
  admin: "管理员",
  knowledge: "知识运营",
  support: "客服",
  advisor: "课程顾问",
};
const descriptions: Record<string, string> = {
  admin: "全部数据；管理员工、分配线索、导出数据及审核发布知识。",
  knowledge: "维护知识草稿、提交审核、查看版本和测试检索。",
  support: "查看机构内咨询与线索，接待访客、记录跟进和查看统计。",
  advisor: "仅查看分配给自己的线索、关联咨询和统计，进行接待与跟进。",
};
const blank = () => ({
  name: "",
  email: "",
  role: "advisor",
  password: "",
  is_active: true,
});
export function Staff({ version }: { version: number }) {
  const r = useResource<any[]>("/admin/staff", version),
    toast = useToast(),
    { user } = useAccess();
  const [edit, setEdit] = useState<any>(null),
    [password, setPassword] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  function open(member: any) {
    setEdit({ ...member });
    setPassword("");
    setError("");
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const payload = edit.id
        ? { name: edit.name, role: edit.role, is_active: edit.is_active }
        : { ...edit };
      const result = await api(
        edit.id ? "/admin/staff/" + edit.id : "/admin/staff",
        { method: edit.id ? "PATCH" : "POST", body: JSON.stringify(payload) },
      );
      setEdit(result);
      r.reload();
      toast("员工资料已保存");
      // Re-read the current login if its own role or status changed; the server revokes it immediately.
      if (
        user &&
        edit.id === user.id &&
        (result.role !== user.role || !result.is_active)
      )
        window.location.reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function reset(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/admin/staff/" + edit.id + "/reset-password", {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      setPassword("");
      toast("密码已重设，该员工的旧登录已失效");
      if (edit.id === user?.id) window.location.reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        title="员工与权限"
        description="按实际岗位授权；课程顾问的数据范围随线索分配生效。"
        actions={
          <button className="button" onClick={() => open(blank())}>
            <Plus size={16} />
            新增员工
          </button>
        }
      />
      <div className={"lead-layout " + (edit ? "with-detail" : "")}>
        <section className="panel">
          {r.loading ? (
            <Loading />
          ) : r.error ? (
            <ErrorBox text={r.error} retry={r.reload} />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>员工</th>
                    <th>岗位</th>
                    <th>状态</th>
                    <th>管理</th>
                  </tr>
                </thead>
                <tbody>
                  {r.data?.map((m) => (
                    <tr key={m.id}>
                      <td>
                        <strong>{m.name}</strong>
                        <small className="visibility">{m.email}</small>
                      </td>
                      <td>{roles[m.role as keyof typeof roles]}</td>
                      <td>
                        <Badge tone={m.is_active ? "green" : "muted"}>
                          {m.is_active ? "启用" : "已停用"}
                        </Badge>
                      </td>
                      <td>
                        <button
                          className="button secondary small"
                          onClick={() => open(m)}
                        >
                          编辑{m.id === user?.id ? "（我）" : ""}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="role-guide">
            <h3>
              <ShieldCheck size={17} /> 岗位权限
            </h3>
            {Object.entries(roles).map(([key, label]) => (
              <p key={key}>
                <strong>{label}</strong> · {descriptions[key]}
              </p>
            ))}
          </div>
        </section>
        {edit && (
          <aside className="panel lead-detail">
            <div className="panel-heading">
              <h2>{edit.id ? "编辑员工" : "新增员工"}</h2>
              <button
                className="icon-button"
                aria-label="关闭员工编辑"
                onClick={() => setEdit(null)}
              >
                <X size={18} />
              </button>
            </div>
            {error && <ErrorBox text={error} />}
            <form onSubmit={save}>
              <label>
                姓名
                <input
                  required
                  maxLength={80}
                  value={edit.name}
                  onChange={(e) => setEdit({ ...edit, name: e.target.value })}
                />
              </label>
              <label>
                登录邮箱
                <input
                  type="email"
                  required
                  maxLength={200}
                  disabled={Boolean(edit.id)}
                  autoComplete="off"
                  value={edit.email}
                  onChange={(e) => setEdit({ ...edit, email: e.target.value })}
                />
              </label>
              <label>
                岗位
                <select
                  value={edit.role}
                  onChange={(e) => setEdit({ ...edit, role: e.target.value })}
                >
                  {Object.entries(roles).map(([v, label]) => (
                    <option value={v} key={v}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <p className="footnote">{descriptions[edit.role]}</p>
              {!edit.id && (
                <label>
                  初始密码
                  <input
                    type="password"
                    minLength={12}
                    maxLength={128}
                    required
                    autoComplete="new-password"
                    value={edit.password}
                    onChange={(e) =>
                      setEdit({ ...edit, password: e.target.value })
                    }
                  />
                  <small>至少 12 个字符，通过安全渠道交给员工。</small>
                </label>
              )}
              {edit.id && (
                <label className="checkbox-line">
                  <input
                    type="checkbox"
                    checked={edit.is_active}
                    onChange={(e) =>
                      setEdit({ ...edit, is_active: e.target.checked })
                    }
                  />
                  启用该员工
                </label>
              )}
              <p className="footnote">
                停用或调整岗位将立即结束该员工的登录。至少保留一名启用的管理员。
              </p>
              <button className="button full" disabled={busy}>
                <Save size={16} />
                {busy ? "正在保存…" : "保存员工"}
              </button>
            </form>
            {edit.id && (
              <form onSubmit={reset} className="password-reset">
                <h3>重设密码</h3>
                <label>
                  新密码
                  <input
                    type="password"
                    required
                    minLength={12}
                    maxLength={128}
                    autoComplete="new-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </label>
                <button
                  className="button secondary full"
                  disabled={busy || password.length < 12}
                >
                  重设密码并结束旧登录
                </button>
              </form>
            )}
          </aside>
        )}
      </div>
    </>
  );
}
