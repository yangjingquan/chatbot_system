import { useEffect, useState } from "react";

export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch("/api" + path, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const data = await response
      .json()
      .catch(() => ({ detail: "服务暂时不可用，请稍后重试" }));
    if (response.status === 401 && !path.startsWith("/auth/"))
      window.dispatchEvent(new Event("session-expired"));
    throw Object.assign(
      new Error(
        typeof data.detail === "string" ? data.detail : "输入内容不符合要求",
      ),
      { status: response.status },
    );
  }
  return response.json();
}

export function useResource<T = any>(path: string, version: number = 0) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api<T>(path, { signal: controller.signal })
      .then(setData)
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, version, tick]);
  return { data, setData, loading, error, reload: () => setTick((t) => t + 1) };
}

export const date = (value: string, full = false) =>
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    ...(full ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(new Date(value));
export const statusLabel: Record<string, string> = {
  published: "已发布",
  draft: "待审核",
  archived: "已下架",
  public: "对外公开",
  internal: "内部资料",
  bot: "机器人接待",
  waiting: "等待人工",
  human: "人工接待",
  closed: "已结束",
};
