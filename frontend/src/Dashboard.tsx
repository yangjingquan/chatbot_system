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
  const resource = useResource<any>(
    `/admin/overview?include_demo=${includeDemo}`,
    version,
  );
  if (resource.loading) return <Loading />;
  if (resource.error)
    return <ErrorBox text={resource.error} retry={resource.reload} />;
  const d = resource.data,
    c = d.counts;
  const metrics = [
    ["咨询会话", c.conversations, MessagesSquare, "已记录有效咨询"],
    ["新增线索", c.leads, Users, "自愿提交联系请求"],
    ["高意向线索", c.high_intent, Target, "依据规则及对话证据"],
    ["公开知识", c.published, BookOpen, "已发布且可供访客使用"],
  ] as const;
  return (
    <>
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
              <p>规则分级，支持人工复核</p>
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
                      {v.is_demo && <Badge tone="muted">演示</Badge>}
                    </strong>
                    <p>{v.last_message}</p>
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
              description="打开官网演示，向右下角的学习顾问提问。"
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
  const r = useResource<any>(
    `/admin/overview?include_demo=${includeDemo}`,
    version,
  );
  if (r.loading) return <Loading />;
  if (r.error) return <ErrorBox text={r.error} retry={r.reload} />;
  const d = r.data,
    c = d.counts;
  return (
    <>
      <PageTitle
        title="数据分析"
        description="咨询、线索与知识质量，使用同一份明细口径。"
      />
      <div className="metric-strip">
        <div className="metric">
          <span>会话留资率</span>
          <strong>
            {c.conversations
              ? ((c.leads / c.conversations) * 100).toFixed(1)
              : "0"}
            <small>%</small>
          </strong>
          <p>留资会话数 ÷ 有效咨询会话数</p>
        </div>
        <div className="metric">
          <span>人工确认合格</span>
          <strong>{c.qualified}<small>条</small></strong>
          <p>阶段为已合格、商机推进或已成交</p>
        </div>
        <div className="metric">
          <span>DeepSeek 回答数</span>
          <strong>
            {d.usage.deepseek_answers}
            <small>次</small>
          </strong>
          <p>仅统计实际调用模型的消息</p>
        </div>
        <div className="metric">
          <span>模型 Token 用量</span>
          <strong>{d.usage.total_tokens.toLocaleString()}</strong>
          <p>未接入时为 0，不估算虚拟费用</p>
        </div>
      </div>
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
        统计说明：关闭“包含演示记录”后排除合成数据。关闭窗口不视为问题解决，规则评分不等于成交概率；成交效果需要销售如实回填。
      </p>
    </>
  );
}
