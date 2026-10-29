import { Select } from "./Select";
import { useEffect, useId, useState } from "react";
import { ChevronDown } from "lucide-react";
import { useResource } from "./api";

export const exams = [
  "公安联考",
  "国考",
  "省考",
  "选调生",
  "事业单位",
  "社会招警",
  "辅警",
  "社区工作者",
];
export type Page<T = any> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
};
export function queryString(values: Record<string, any>) {
  return new URLSearchParams(
    Object.entries(values)
      .filter(([, v]) => v !== "" && v !== undefined && v !== null)
      .map(([k, v]) => [k, String(v)]),
  ).toString();
}
export function useFilterState() {
  const [filters, setFilters] = useState<Record<string, string>>({});
  return {
    filters,
    change: (key: string, value: string) =>
      setFilters((v) => ({ ...v, [key]: value })),
  };
}
export function usePaged(
  path: string,
  includeDemo: boolean,
  version: number,
  extra: Record<string, any> = {},
) {
  const [filters, setFilters] = useState<Record<string, string>>({}),
    [page, setPage] = useState(1),
    [size, setSize] = useState(20);
  const query = queryString({
    ...filters,
    ...extra,
    include_demo: includeDemo,
  });
  const resource = useResource<Page>(
    path + "?" + query + "&page=" + page + "&page_size=" + size,
    version,
  );
  useEffect(() => {
    if (
      !resource.loading &&
      resource.data &&
      page > Math.max(1, resource.data.pages)
    )
      setPage(Math.max(1, resource.data.pages));
  }, [resource.data, resource.loading, page]);
  const change = (key: string, value: string) => {
    setFilters((v) => ({ ...v, [key]: value }));
    setPage(1);
  };
  useEffect(() => setPage(1), [includeDemo]);
  return {
    resource,
    filters,
    change,
    query,
    page,
    setPage,
    size,
    setSize: (v: number) => {
      setSize(v);
      setPage(1);
    },
  };
}
export function Pager({ list }: { list: ReturnType<typeof usePaged> }) {
  const p = list.resource.data;
  return (
    <div className="pagination">
      <span>
        共 {p?.total || 0} 条 · 第 {list.page} / {Math.max(1, p?.pages || 0)} 页
      </span>
      <label>
        每页
        <Select
          aria-label="每页条数"
          value={list.size}
          onValueChange={(value) => list.setSize(Number(value))}
        >
          {[20, 50, 100].map((n) => (
            <option key={n}>{n}</option>
          ))}
        </Select>
      </label>
      <button
        className="button secondary small"
        disabled={list.resource.loading || list.page === 1}
        onClick={() => list.setPage(list.page - 1)}
      >
        上一页
      </button>
      <button
        className="button secondary small"
        disabled={list.resource.loading || !p || list.page >= p.pages}
        onClick={() => list.setPage(list.page + 1)}
      >
        下一页
      </button>
    </div>
  );
}
export function Filters({
  list,
  owners = [],
  business = true,
  placeholder = "搜索姓名、完整手机号或邮箱",
}: {
  list: Pick<ReturnType<typeof usePaged>, "filters" | "change">;
  owners?: any[];
  business?: boolean;
  placeholder?: string;
}) {
  const { filters: f, change } = list;
  const advancedKeys = business
    ? [
        "date_from",
        "date_to",
        "exam_type",
        "owner_id",
        "stage",
        "level",
        "contact_state",
      ]
    : ["date_from", "date_to", "exam_type", "owner_id"];
  const activeAdvancedCount = advancedKeys.filter((key) => f[key]).length;
  const [advancedOpen, setAdvancedOpen] = useState(
    () =>
      typeof window === "undefined" ||
      !window.matchMedia("(max-width: 767px)").matches,
  );
  const advancedId = useId();
  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const syncLayout = (event: MediaQueryListEvent) =>
      setAdvancedOpen(!event.matches);
    media.addEventListener("change", syncLayout);
    return () => media.removeEventListener("change", syncLayout);
  }, []);
  return (
    <div className="business-filters">
      <label className="filter-search">
        关键词
        <input
          aria-label="筛选关键词"
          value={f.q || ""}
          placeholder={placeholder}
          onChange={(e) => change("q", e.target.value)}
        />
      </label>
      <button
        type="button"
        className="business-filter-toggle"
        aria-expanded={advancedOpen}
        aria-controls={advancedId}
        onClick={() => setAdvancedOpen((open) => !open)}
      >
        <span>{advancedOpen ? "收起筛选" : "更多筛选"}</span>
        {activeAdvancedCount > 0 && (
          <span className="filter-active-count">
            已选 {activeAdvancedCount}
          </span>
        )}
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      <div
        id={advancedId}
        className="business-filter-advanced"
        hidden={!advancedOpen}
      >
        <label>
          创建日期起（北京时间）
          <input
            type="date"
            value={f.date_from || ""}
            onChange={(e) => change("date_from", e.target.value)}
          />
        </label>
        <label>
          创建日期止
          <input
            type="date"
            value={f.date_to || ""}
            onChange={(e) => change("date_to", e.target.value)}
          />
        </label>
        <label>
          考试方向
          <Select
            value={f.exam_type || ""}
            onValueChange={(value) => change("exam_type", value)}
          >
            <option value="">全部考试</option>
            {exams.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </Select>
        </label>
        <label>
          负责人
          <Select
            value={f.owner_id || ""}
            onValueChange={(value) => change("owner_id", value)}
          >
            <option value="">全部负责人</option>
            {business && <option value="unassigned">未分配</option>}
            {owners.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
                {o.is_active === false ? "（已停用）" : ""}
              </option>
            ))}
          </Select>
        </label>
        {business && (
          <>
            <label>
              跟进阶段
              <Select
                value={f.stage || ""}
                onValueChange={(value) => change("stage", value)}
              >
                <option value="">全部阶段</option>
                {[
                  "新线索",
                  "待分配",
                  "跟进中",
                  "已合格",
                  "商机推进",
                  "已成交",
                  "已失效",
                ].map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </Select>
            </label>
            <label>
              意向等级
              <Select
                value={f.level || ""}
                onValueChange={(value) => change("level", value)}
              >
                <option value="">全部意向</option>
                {[
                  "待判定",
                  "一般咨询",
                  "低意向",
                  "中意向",
                  "高意向",
                  "不适用",
                ].map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </Select>
            </label>
            <label>
              联系许可
              <Select
                value={f.contact_state || ""}
                onValueChange={(value) => change("contact_state", value)}
              >
                <option value="">全部许可状态</option>
                <option value="allowed">允许本次联系</option>
                <option value="withdrawn">已停止联系</option>
              </Select>
            </label>
          </>
        )}
      </div>
    </div>
  );
}

export const localTime = (value: string | null) =>
  value
    ? new Date(new Date(value).getTime() + 8 * 3600000)
        .toISOString()
        .slice(0, 16)
    : "";
export const isoTime = (value: string) =>
  value ? new Date(value + ":00+08:00").toISOString() : null;
