import { useEffect, useState, type FormEvent } from "react";
import {
  NavLink,
  Route,
  Routes,
  Link,
  Navigate,
  useLocation,
} from "react-router-dom";
import {
  GraduationCap,
  LayoutDashboard,
  BookOpen,
  MessagesSquare,
  Users,
  ContactRound,
  ChartNoAxesCombined,
  Bot,
  Settings,
  LogOut,
  ExternalLink,
  Menu,
  X,
  ChevronRight,
  ShieldCheck,
  RefreshCw,
  Activity,
} from "lucide-react";
import { api, useResource } from "./api";
import { ErrorBox, Loading } from "./ui";
import { Dashboard, Analytics } from "./Dashboard";
import { Knowledge } from "./Knowledge";
import { Conversations } from "./Conversations";
import { Leads } from "./Leads";
import { Configuration } from "./Configuration";
import { AuthProvider } from "./auth";
import { Staff } from "./Staff";
import { Courses } from "./Courses";
import { Students } from "./Students";
import { Todos } from "./Todos";
import { ReceptionStatus, Trials } from "./Reception";
import { Quality } from "./Quality";
import { DocumentJobs } from "./DocumentJobs";
import { Monitoring } from "./Monitoring";

const navigation = [
  ["/", "工作台", LayoutDashboard, "analytics.read"],
  ["/knowledge", "企业知识库", BookOpen, "knowledge.read"],
  ["/document-jobs", "文档处理任务", RefreshCw, "knowledge.read"],
  ["/courses", "课程与班次", GraduationCap, "courses.read"],
  ["/conversations", "咨询会话", MessagesSquare, "conversations.read"],
  ["/trials", "试听预约", GraduationCap, "conversations.read"],
  ["/leads", "咨询线索", Users, "leads.read"],
  ["/customers", "学员档案", ContactRound, "students.read"],
  ["/todos", "跟进待办", Users, "leads.read"],
  ["/analytics", "数据分析", ChartNoAxesCombined, "analytics.read"],
  ["/staff", "员工与权限", ShieldCheck, "staff.manage"],
  ["/robot", "机器人配置", Bot, "settings.write"],
  ["/quality", "质量与用量", ChartNoAxesCombined, "settings.write"],
  ["/ops", "运行监控", Activity, "settings.read"],
  ["/settings", "企业设置", Settings, "settings.write"],
] as const;

function Login({ onLogin }: { onLogin: (user: any) => void }) {
  const [email, setEmail] = useState("admin@zhixing.local");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(
        await api("/auth/login", {
          method: "POST",
          body: JSON.stringify({ email, password }),
        }),
      );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-layout">
      <aside className="login-story">
        <div className="brand">
          <GraduationCap size={30} />
          <span>知行客服</span>
        </div>
        <div>
          <h1>
            更懂学员的提问。
            <br />
            更有依据的回答。
          </h1>
          <p>
            把企业知识、咨询服务与学员跟进连接起来，
            <br />
            让课程顾问把精力留给真正需要帮助的人。
          </p>
          <div className="login-path">
            <span>知识</span>
            <ChevronRight size={18} />
            <span>咨询</span>
            <ChevronRight size={18} />
            <span>跟进</span>
          </div>
        </div>
        <small>培训服务与咨询管理</small>
      </aside>
      <main className="login-form">
        <form onSubmit={submit}>
          <div className="login-mark">
            <GraduationCap size={26} />
          </div>
          <h2>登录工作台</h2>
          <p>欢迎回来，继续处理您的咨询与学员需求。</p>
          <label>
            员工邮箱
            <input
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label>
            登录密码
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {error && <ErrorBox text={error} />}
          <button className="button full" disabled={busy}>
            {busy ? "正在登录…" : "登录工作台"}
          </button>
          <div className="login-help">
            <ShieldCheck size={18} />
            <span>请使用管理员分配的账号登录，妥善保管您的登录信息。</span>
          </div>
          <Link to="/demo" className="text-link">
            先体验官网智能客服 <ExternalLink size={14} />
          </Link>
        </form>
      </main>
    </div>
  );
}

export function App() {
  const [user, setUser] = useState<any>(null);
  const [checking, setChecking] = useState(true);
  const [mobile, setMobile] = useState(false);
  const includeDemo = false;
  const [version, setVersion] = useState(0);
  const config = useResource<any>("/admin/settings", user ? version : -1);
  const location = useLocation();
  useEffect(() => {
    api("/auth/me")
      .then(setUser)
      .catch(() => {})
      .finally(() => setChecking(false));
    const expire = () => setUser(null);
    window.addEventListener("session-expired", expire);
    return () => window.removeEventListener("session-expired", expire);
  }, []);
  useEffect(() => setMobile(false), [location.pathname]);
  const refresh = () => setVersion((v) => v + 1);
  if (checking) return <Loading />;
  if (!user) return <Login onLogin={setUser} />;
  const can = (permission: string) => user.permissions?.includes(permission);
  const home = can("analytics.read") ? "/" : "/knowledge";
  const guard = (permission: string, page: React.ReactNode) =>
    can(permission) ? page : <Navigate to={home} replace />;
  return (
    <AuthProvider user={user}>
      <div className="app-shell">
        <aside className={"sidebar " + (mobile ? "open" : "")}>
          <Link to="/" className="brand">
            <div className="brand-symbol">
              <GraduationCap size={24} />
            </div>
            <div>
              知行客服<small>培训机构工作台</small>
            </div>
          </Link>
          <button
            className="icon-button mobile-close"
            aria-label="关闭导航"
            onClick={() => setMobile(false)}
          >
            <X size={20} />
          </button>
          <div className="company-switch">
            <span className="company-avatar">知</span>
            <div>
              <strong>{config.data?.company_name || "知行公考"}</strong>
              <small>培训服务工作台</small>
            </div>
            <ChevronRight size={14} />
          </div>
          <nav aria-label="主导航">
            {navigation
              .filter((n) => can(n[3]))
              .map(([to, label, Icon]) => (
                <NavLink key={to} to={to} end={to === "/"}>
                  <Icon size={19} />
                  <span>{label}</span>
                  {to === "/robot" && <span className="nav-new">AI</span>}
                </NavLink>
              ))}
          </nav>
          <div className="sidebar-bottom">
            <div className="service-status">
              <span
                className={
                  "status-dot " +
                  (config.data?.model_configured ? "green" : "amber")
                }
              />
              <div>
                <strong>
                  {config.data?.model_configured
                    ? "DeepSeek 已配置"
                    : "资料摘录模式"}
                </strong>
                <small>
                  {config.data?.model_configured
                    ? "服务端安全接入"
                    : "接入 Key 后启用智能回答"}
                </small>
              </div>
            </div>
            <button
              className="user-button"
              onClick={async () => {
                await api("/auth/logout", { method: "POST" });
                setUser(null);
              }}
            >
              <span className="avatar">{user.name.slice(0, 1)}</span>
              <div>
                <strong>{user.name}</strong>
                <small>{user.role_label} · 退出登录</small>
              </div>
              <LogOut size={16} />
            </button>
          </div>
        </aside>
        {mobile && (
          <button
            className="nav-backdrop"
            aria-label="关闭导航"
            onClick={() => setMobile(false)}
          />
        )}
        <div className="main-shell">
          <header className="topbar">
            <div className="breadcrumb">
              <button
                className="icon-button mobile-menu"
                aria-label="打开导航"
                onClick={() => setMobile(true)}
              >
                <Menu size={20} />
              </button>
              <span>运营空间</span>
              <ChevronRight size={14} />
              <strong>
                {navigation.find((n) => n[0] === location.pathname)?.[1] ||
                  "工作台"}
              </strong>
            </div>
            <div className="topbar-actions">
              {can("conversations.handle") && <ReceptionStatus />}
              <a
                href="/demo"
                target="_blank"
                rel="noreferrer"
                className="button secondary small"
              >
                <ExternalLink size={15} />
                官网预览
              </a>
              <button
                aria-label="刷新数据"
                className="icon-button"
                onClick={refresh}
              >
                <RefreshCw size={18} />
              </button>
            </div>
          </header>
          <main className="workspace">
            <Routes>
              <Route
                path="/document-jobs"
                element={guard(
                  "knowledge.read",
                  <DocumentJobs version={version} />,
                )}
              />
              <Route
                path="/quality"
                element={guard("settings.write", <Quality version={version} />)}
              />
              <Route
                path="/trials"
                element={guard(
                  "conversations.read",
                  <Trials includeDemo={includeDemo} version={version} />,
                )}
              />
              <Route
                path="/courses"
                element={guard("courses.read", <Courses version={version} />)}
              />
              <Route
                path="/todos"
                element={guard(
                  "leads.read",
                  <Todos includeDemo={includeDemo} version={version} />,
                )}
              />
              <Route
                path="/"
                element={guard(
                  "analytics.read",
                  <Dashboard includeDemo={includeDemo} version={version} />,
                )}
              />
              <Route
                path="/knowledge"
                element={guard(
                  "knowledge.read",
                  <Knowledge version={version} onChange={refresh} />,
                )}
              />
              <Route
                path="/conversations"
                element={guard(
                  "conversations.read",
                  <Conversations includeDemo={includeDemo} version={version} />,
                )}
              />
              <Route
                path="/leads"
                element={guard(
                  "leads.read",
                  <Leads includeDemo={includeDemo} version={version} />,
                )}
              />
              <Route
                path="/customers"
                element={guard(
                  "students.read",
                  <Students includeDemo={includeDemo} version={version} />,
                )}
              />
              <Route
                path="/analytics"
                element={guard(
                  "analytics.read",
                  <Analytics includeDemo={includeDemo} version={version} />,
                )}
              />
              <Route
                path="/robot"
                element={guard(
                  "settings.write",
                  <Configuration robot version={version} onChange={refresh} />,
                )}
              />
              <Route
                path="/ops"
                element={guard(
                  "settings.read",
                  <Monitoring version={version} />,
                )}
              />
              <Route
                path="/settings"
                element={guard(
                  "settings.write",
                  <Configuration version={version} onChange={refresh} />,
                )}
              />
              <Route
                path="/staff"
                element={guard("staff.manage", <Staff version={version} />)}
              />
              <Route
                path="*"
                element={
                  <div className="empty">
                    <h2>页面不存在</h2>
                    <Link to="/">返回工作台</Link>
                  </div>
                }
              />
            </Routes>
          </main>
          <footer className="workspace-footer">
            <span>知行客服 · 每一次回答，都有据可循</span>
          </footer>
        </div>
      </div>
    </AuthProvider>
  );
}
