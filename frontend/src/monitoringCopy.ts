export const componentNameLabel: Record<string, string> = {
  api: "接口服务（API）",
  alert_monitor: "告警巡检进程",
  database: "数据库",
  redis: "缓存服务（Redis）",
  worker: "文档处理进程（Worker）",
  jobs: "文档任务队列",
  model: "模型调用服务",
  latency: "接口响应时间",
  disk: "数据磁盘",
  backup: "完整数据库备份",
  schema: "数据库结构版本",
};

export const componentStatusLabel: Record<string, string> = {
  ok: "正常",
  warn: "需关注",
  fail: "异常",
  unknown: "未知",
  skipped: "未启用",
  degraded: "降级",
};

export function summarizeComponent(
  key: string,
  component: any,
  formatDate: (value: string) => string = (value) => value,
): string {
  if (key === "jobs")
    return `队列：等待 ${component.queued ?? 0}、处理中 ${component.running ?? 0} · 最近 1 小时失败 ${component.failed_1h ?? 0}`;
  if (key === "model")
    return `最近 1 小时：调用 ${component.calls_1h ?? 0} 次、错误 ${component.errors_1h ?? 0} 次、超时 ${component.timeouts_1h ?? 0} 次 · 错误率 ${Math.round(Number(component.error_rate || 0) * 100)}%`;
  if (key === "latency")
    return `样本 ${component.count ?? 0} 次 · 中位响应（P50）${component.p50_ms ?? "—"} 毫秒 · P95 ${component.p95_ms ?? "—"} 毫秒 · 最大 ${component.max_ms ?? "—"} 毫秒`;
  if (key === "disk")
    return `已用 ${component.used_pct ?? "—"}% · 剩余 ${component.free_gb ?? "—"} GB`;
  if (key === "worker") {
    if (component.age_seconds != null)
      return component.status === "ok"
        ? `最近一次心跳在 ${component.age_seconds} 秒前`
        : `心跳已过期（${component.age_seconds} 秒）`;
    if (component.detail === "尚无 worker 心跳")
      return "尚未收到文档处理进程心跳";
  }
  if (key === "database" && component.detail === "database reachable")
    return "数据库连接正常";
  if (key === "redis") {
    const redisCopy: Record<string, string> = {
      "REDIS_URL not configured; in-memory rate limit is used":
        "未配置 Redis，当前使用应用内存限流",
      "redis client unavailable": "Redis 客户端不可用，请检查服务配置",
      PONG: "Redis 连接正常",
      "no PONG": "Redis 未响应",
    };
    if (redisCopy[component.detail]) return redisCopy[component.detail];
  }
  if (key === "alert_monitor" && component.updated_at)
    return component.detail || `最近巡检于 ${formatDate(component.updated_at)}`;
  if (key === "worker" && component.updated_at && !component.detail)
    return `最近运行记录：${formatDate(component.updated_at)}`;
  return component.detail || component.latest_file || "暂无更多信息";
}
