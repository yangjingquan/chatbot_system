import { Link } from "react-router-dom";
import {
  ArrowUpRight,
  ArrowRight,
  MessagesSquare,
  Users,
  Target,
  BookOpen,
  Clock3,
  Headphones,
  CheckCircle2,
  Bot,
} from "lucide-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
} from "recharts";
import { date, useResource } from "./api";
import { messageCopy } from "./displayCopy";
import { Filters, queryString, useFilterState } from "./lists";
import { Badge, Empty, ErrorBox, Level, Loading, PageTitle } from "./ui";

function Trend({ data }: { data: any[] }) {
  return (
    <div className="chart-frame" aria-label="最近七天咨询与留资趋势">
      <ResponsiveContainer width="100%" height={240}>
        <AreaChart
          data={data}
          margin={{ top: 12, right: 10, left: -20, bottom: 0 }}
        >
          <CartesianGrid vertical={false} stroke="#e9edf2" />
          <XAxis
            dataKey="date"
            tickFormatter={(v) => v.slice(5)}
            tickLine={false}
            axisLine={false}
            fontSize={12}
            tickMargin={12}
          />
          <YAxis
            allowDecimals={false}
            tickLine={false}
            axisLine={false}
            fontSize={12}
          />
          <Tooltip
            labelFormatter={(v) => String(v)}
            contentStyle={{
              borderRadius: 8,
              border: "1px solid #e3e7ed",
              fontSize: 13,
            }}
          />
          <Area
            name="咨询会话"
            type="monotone"
            dataKey="conversations"
            stroke="#3068c8"
            fill="#e9f0fd"
            strokeWidth={2.5}
          />
          <Area
            name="留资线索"
            type="monotone"
            dataKey="leads"
            stroke="#24856b"
            fill="#e8f5ef"
            strokeWidth={2}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function Intent({ data }: { data: any[] }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  return (
    <div className="intent-list">
      {data.length ? (
        data.map((row, i) => (
          <div key={row.name}>
            <div className="row-between">
              <Level value={row.name} />
              <span>
                {row.value} <small>条</small>
              </span>
            </div>
            <div className="progress-track">
              <span
                style={{
                  width: `${(row.value / Math.max(total, 1)) * 100}%`,
                  background: ["#278367", "#4b77c4", "#a4b8d4", "#c6cdd6"][
                    i % 4
                  ],
                }}
              />
            </div>
          </div>
        ))
      ) : (
        <Empty
          title="暂无线索分级"
          description="访客留资后，将展示有证据的等级分布。"
        />
      )}
    </div>
  );
}

export function Dashboard({
  includeDemo,
  version,
}: {
  includeDemo: boolean;
  version: number;
}) {
  const filterState = useFilterState(),
    owners = useResource<any[]>("/admin/filter-options", version);
  const resource = useResource<any>(
    `/admin/overview?${queryString({ ...filterState.filters, include_demo: includeDemo })}`,
    version,
  );
  const header = (
    <PageTitle
      title="工作台"
      description="掌握咨询动态，把需要跟进的事放在眼前。"
      actions={
        <Link className="button" to="/conversations">
          <MessagesSquare size={16} />
          处理咨询
        </Link>
      }
    />
  );
  const filterControls = (
    <section className="panel">
      <Filters list={filterState} owners={owners.data || []} />
    </section>
  );
  if (resource.loading || resource.error)
    return (
      <>
        {header}
        {filterControls}
        {resource.loading ? (
          <Loading />
        ) : (
          <ErrorBox text={resource.error} retry={resource.reload} />
        )}
      </>
    );
  const d = resource.data,
    c = d.counts;
  const metrics = [
    ["咨询会话", c.conversations, MessagesSquare, "已记录有效咨询"],
    ["新增线索", c.leads, Users, "自愿提交联系请求"],
    ["高意向线索", c.high_intent, Target, "规则辅助，非成交概率"],
    ["公开知识", c.published, BookOpen, "已发布且可供访客使用"],
  ] as const;
  return (
    <>
      {header}
      {filterControls}
      <div className="metric-strip">
        {metrics.map(([title, value, Icon, note]) => (
          <div className="metric" key={title}>
            <div className="row-between">
              <span>{title}</span>
              <Icon size={19} />
            </div>
            <strong>
              {value}
              <small>{title === "公开知识" ? "份" : "条"}</small>
            </strong>
            <p>{note}</p>
          </div>
        ))}
      </div>
      <section className="action-band">
        <div className="action-band-icon">
          <Clock3 size={22} />
        </div>
        <div>
          <strong>
            {c.pending
              ? `有 ${c.pending} 条线索等待跟进`
              : "所有新的咨询，都值得认真回应"}
          </strong>
          <p>
            {c.handoffs
              ? `${c.handoffs} 个会话正在等待人工接待。`
              : "及时完善知识、查看咨询，让服务保持连续。"}
          </p>
        </div>
        <Link to={c.handoffs ? "/conversations" : "/leads"}>
          前往处理 <ArrowRight size={17} />
        </Link>
      </section>
      <section className="panel section-space">
        <div className="panel-heading">
          <div>
            <h2>跟进待办</h2>
            <p>
              当前筛选中 {d.todo_count || 0}{" "}
              条待办，停止联系和已结案线索不提醒外呼。
            </p>
          </div>
          <Link className="text-link" to="/todos">
            查看全部
          </Link>
        </div>
        {d.todos?.length ? (
          <div className="table-wrap">
            <table>
              <tbody>
                {d.todos.map((l: any) => (
                  <tr key={l.todo_key}>
                    <td>
                      <Link to={"/leads?lead_id=" + l.id}>{l.name}</Link>
                    </td>
                    <td>{date(l.next_followup_at, true)}</td>
                    <td>
                      {l.todo_type === "trial" ? (
                        <Link to={"/trials?trial_id=" + l.trial_id}>
                          {l.todo_label} · {l.course_name}
                        </Link>
                      ) : (
                        l.todo_label
                      )}
                    </td>
                    <td>{l.owner}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="role-guide">当前没有需要跟进的待办。</div>
        )}
      </section>
      <div className="dashboard-grid">
        <section className="panel trend-panel">
          <div className="panel-heading">
            <div>
              <h2>咨询与留资趋势</h2>
              <p>最近 7 天 · 按创建日期统计</p>
            </div>
            <div className="chart-legend">
              <span>
                <i className="legend-blue" />
                咨询
              </span>
              <span>
                <i className="legend-green" />
                留资
              </span>
            </div>
          </div>
          <Trend data={d.trend} />
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>线索意向分布</h2>
              <p>规则辅助分级，支持人工复核；非成交概率</p>
            </div>
            <Link to="/leads" aria-label="查看线索">
              <ArrowUpRight size={20} />
            </Link>
          </div>
          <Intent data={d.intent} />
        </section>
        <section className="panel recent-panel">
          <div className="panel-heading">
            <div>
              <h2>最近咨询</h2>
              <p>了解访客正在关注什么</p>
            </div>
            <Link to="/conversations" className="text-link">
              查看全部 <ArrowRight size={15} />
            </Link>
          </div>
          {d.recent_conversations.length ? (
            <div className="recent-list">
              {d.recent_conversations.map((v: any) => (
                <Link to="/conversations" key={v.id}>
                  <span className="visitor-avatar">
                    {v.visitor_name.slice(0, 1)}
                  </span>
                  <div>
                    <strong>
                      {v.visitor_name}
                    </strong>
                    <p>
                      {messageCopy(v.last_message || "")}
                    </p>
                  </div>
                  <span className="recent-time">
                    {date(v.updated_at, true)}
                  </span>
                </Link>
              ))}
            </div>
          ) : (
            <Empty
              title="还没有咨询"
              description="打开咨询页面，向右下角的学习顾问提问。"
            >
              <a href="/demo" className="button secondary small">
                体验官网客服
              </a>
            </Empty>
          )}
        </section>
        <section className="panel service-panel">
          <div className="panel-heading">
            <div>
              <h2>服务准备情况</h2>
              <p>从资料到接待，逐项准备</p>
            </div>
          </div>
          <div className="service-row">
            <div className="round-icon blue">
              <BookOpen size={19} />
            </div>
            <div>
              <strong>企业知识库</strong>
              <small>
                {c.documents} 份资料，{c.published} 份对外发布
              </small>
            </div>
            <CheckCircle2 size={18} className="green-text" />
          </div>
          <div className="service-row">
            <div className="round-icon green">
              <Bot size={19} />
            </div>
            <div>
              <strong>
                {d.model_configured ? "DeepSeek 已配置" : "DeepSeek 等待配置"}
              </strong>
              <small>
                {d.model_configured
                  ? "真实服务状态需完成 API 联调"
                  : "当前使用可溯源的资料摘录"}
              </small>
            </div>
            <Link to="/robot">
              <ArrowRight size={17} />
            </Link>
          </div>
          <div className="service-row">
            <div className="round-icon amber">
              <Headphones size={19} />
            </div>
            <div>
              <strong>人工接待</strong>
              <small>{c.handoffs} 个会话待接管</small>
            </div>
            <Link to="/conversations">
              <ArrowRight size={17} />
            </Link>
          </div>
          <div className="service-note">
            销售意向与服务优先级分别管理，售后咨询同样得到正常接待。
          </div>
        </section>
      </div>
    </>
  );
}

export function Analytics({
  includeDemo,
  version,
}: {
  includeDemo: boolean;
  version: number;
}) {
  const filterState = useFilterState(),
    owners = useResource<any[]>("/admin/filter-options", version);
  const r = useResource<any>(
    `/admin/overview?${queryString({ ...filterState.filters, include_demo: includeDemo })}`,
    version,
  );
  const header = (
    <PageTitle
      title="数据分析"
      description="咨询、线索与知识质量，使用同一份明细口径。"
    />
  );
  const filterControls = (
    <section className="panel">
      <Filters list={filterState} owners={owners.data || []} />
    </section>
  );
  if (r.loading || r.error)
    return (
      <>
        {header}
        {filterControls}
        {r.loading ? <Loading /> : <ErrorBox text={r.error} retry={r.reload} />}
      </>
    );
  const d = r.data,
    c = d.counts;
  return (
    <>
      {header}
      {filterControls}
      <div className="metric-strip">
        <div className="metric">
          <span>会话留资率</span>
          <strong>
            {c.conversations
              ? ((c.contacted_conversations / c.conversations) * 100).toFixed(1)
              : "0"}
            <small>%</small>
          </strong>
          <p>留资会话数 ÷ 有效咨询会话数</p>
        </div>
        <div className="metric">
          <span>试听申请</span>
          <strong>
            {c.trials ?? 0}
            <small>条</small>
          </strong>
          <p>有效咨询关联的试听（含各状态）</p>
        </div>
        <div className="metric">
          <span>成交 / 合格</span>
          <strong>
            {c.customers}
            <small> / {c.qualified}</small>
          </strong>
          <p>已成交数 / 合格及以上（需回填）</p>
        </div>
        <div className="metric">
          <span>人工首次回复</span>
          <strong>
            {d.reply_latency?.avg_seconds != null
              ? d.reply_latency.avg_seconds
              : "—"}
            <small>{d.reply_latency?.avg_seconds != null ? "秒" : ""}</small>
          </strong>
          <p>
            排队到首条客服消息
            {d.reply_latency?.sample_count
              ? `（${d.reply_latency.sample_count} 个样本）`
              : "（暂无样本）"}
          </p>
        </div>
      </div>
      <p className="footnote" style={{ marginTop: 8 }}>
        模型调用 {d.usage.deepseek_answers} 次，Token{" "}
        {d.usage.total_tokens.toLocaleString()}
        ；未配置单价时费用见「回答质量」用量页。
      </p>
      <div className="dashboard-grid">
        <section className="panel">
          <div className="panel-heading">
            <h2>咨询与线索趋势</h2>
            <span className="muted">最近 7 天</span>
          </div>
          <Trend data={d.trend} />
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>意向分布</h2>
          </div>
          <Intent data={d.intent} />
        </section>
        <section className="panel">
          <div className="panel-heading">
            <h2>考试关注分布</h2>
          </div>
          {d.exams.length ? (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart
                data={d.exams}
                layout="vertical"
                margin={{ left: 15, right: 25 }}
              >
                <XAxis type="number" allowDecimals={false} hide />
                <YAxis
                  type="category"
                  dataKey="name"
                  width={85}
                  axisLine={false}
                  tickLine={false}
                  fontSize={12}
                />
                <Tooltip />
                <Bar
                  dataKey="value"
                  name="关联线索"
                  fill="#4776bd"
                  radius={[0, 4, 4, 0]}
                />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <Empty title="暂无考试标签" />
          )}
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              {(d.knowledge_todo_count || 0) > 0 && (
                <div className="panel" style={{ marginBottom: 12 }}>
                  <div className="list-toolbar">
                    <strong>知识到期待办</strong>
                    <Badge tone="amber">{d.knowledge_todo_count} 条</Badge>
                    <Link className="text-link" to="/knowledge">
                      去知识库处理
                    </Link>
                  </div>
                  <ul className="plain-list">
                    {(d.knowledge_todos || []).map((t: any) => (
                      <li key={t.id}>
                        {t.title}
                        <small className="cell-sub">{t.responsible_name}</small>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <h2>待补充知识</h2>
              <p>没有找到足够依据的问题</p>
            </div>
            <Badge>{d.knowledge_gaps.length} 条</Badge>
          </div>
          {d.knowledge_gaps.length ? (
            <div className="gap-list">
              {d.knowledge_gaps.map((q: any, i: number) => (
                <div key={i}>
                  <p>{q.question}</p>
                  <small>{date(q.created_at, true)}</small>
                </div>
              ))}
            </div>
          ) : (
            <Empty
              title="暂未发现知识缺口"
              description="未知问题会在这里汇总，供运营人员审核补充。"
            />
          )}
        </section>
      </div>
      <p className="footnote">
        各项指标按当前筛选范围统计；规则评分不等于成交概率，成交需如实回填。
      </p>
    </>
  );
}
