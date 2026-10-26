import { createContext, useContext, useState, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, Loader2, X, Inbox } from "lucide-react";

const ToastContext = createContext<(text: string, error?: boolean) => void>(
  () => {},
);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ text: string; error: boolean } | null>(
    null,
  );
  const show = (text: string, error = false) => {
    setToast({ text, error });
    window.setTimeout(() => setToast(null), 5000);
  };
  return (
    <ToastContext.Provider value={show}>
      {children}
      {toast && (
        <div role="status" className={"toast " + (toast.error ? "error" : "")}>
          {toast.error ? <AlertCircle size={18} /> : <CheckCircle2 size={18} />}
          <span>{toast.text}</span>
          <button
            className="icon-button"
            aria-label="关闭通知"
            onClick={() => setToast(null)}
          >
            <X size={16} />
          </button>
        </div>
      )}
    </ToastContext.Provider>
  );
}
export const useToast = () => useContext(ToastContext);
export function Badge({
  children,
  tone = "default",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={"badge " + tone}>{children}</span>;
}
export function Level({ value }: { value: string }) {
  return (
    <Badge
      tone={
        value === "高意向"
          ? "green"
          : value === "中意向"
            ? "blue"
            : value === "不适用"
              ? "muted"
              : "default"
      }
    >
      {value || "待判定"}
    </Badge>
  );
}
export function Empty({
  title = "暂无记录",
  description = "新的数据会在这里显示。",
  children,
}: {
  title?: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <Inbox size={32} strokeWidth={1.3} />
      <h3>{title}</h3>
      <p>{description}</p>
      {children}
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <Loader2 className="spin" size={20} />
      正在读取数据…
    </div>
  );
}
export function ErrorBox({
  text,
  retry,
}: {
  text: string;
  retry?: () => void;
}) {
  return (
    <div className="error-box" role="alert">
      <AlertCircle size={18} />
      <span>{text}</span>
      {retry && (
        <button className="button secondary small" onClick={retry}>
          重试
        </button>
      )}
    </div>
  );
}
export function PageTitle({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-title">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}
