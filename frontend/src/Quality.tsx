import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, date, useResource } from "./api";
import { Badge, Empty, ErrorBox, Loading, PageTitle, useToast } from "./ui";

const labels: Record<string, string> = {
  open: "待处理",
  processing: "处理中",
  resolved: "已解决",
  dismissed: "无需补充",
};
const errors: Record<string, string> = {
  auth: "鉴权失败",
  balance: "余额不足",
  rate_limit: "限流",
  timeout: "超时",
  unavailable: "服务繁忙",
  parameters: "参数不兼容",
  network: "网络故障",
  protocol: "流不完整",
  interrupted: "中断",
};
export function Quality({ version }: { version: number }) {
  const [tab, setTab] = useState("gaps");
  return (
    <>
      <PageTitle
        title="机器人质量与用量"
        description="跟进回答反馈、补充知识、人工验收样本，并查看模型调用与预算。"
      />
      <div className="quality-tabs" role="tablist" aria-label="质量管理">
        {[
          ["gaps", "反馈与知识缺口"],
          ["usage", "用量与预算"],
          ["evaluations", "评测与人工验收"],
        ].map(([k, v]) => (
          <button
            role="tab"
            aria-selected={tab === k}
            className={"button " + (tab === k ? "" : "secondary")}
            key={k}
            onClick={() => setTab(k)}
          >
            {v}
          </button>
        ))}
      </div>
      {tab === "gaps" ? (
        <Gaps version={version} />
      ) : tab === "usage" ? (
        <Usage version={version} />
      ) : (
        <Evaluations version={version} />
      )}
    </>
  );
}
function Gaps({ version }: { version: number }) {
  const [status, setStatus] = useState(""),
    [page, setPage] = useState(1),
    [selected, setSelected] = useState<any>(null),
    [next, setNext] = useState("processing"),
    [note, setNote] = useState(""),
    [content, setContent] = useState(""),
    [busy, setBusy] = useState(false);
  const toast = useToast();
  const list = useResource<any>(
    `/admin/quality/gaps?status=${status}&page=${page}&page_size=20`,
    version,
  );
  useEffect(() => {
    setPage(1);
    setSelected(null);
  }, [status]);
  async function save(draft = false) {
    setBusy(true);
    try {
      const r = await api(
        `/admin/quality/gaps/${selected.id}${draft ? "/draft" : ""}`,
        {
          method: draft ? "POST" : "PATCH",
          body: JSON.stringify(draft ? { content } : { status: next, note }),
        },
      );
      if (draft) {
        setSelected({ ...selected, document_id: r.id, status: "processing" });
        toast("已创建草稿，请到知识库核对后审核发布");
      } else {
        setSelected(r);
        toast("处理结果已保存");
      }
      list.reload();
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <section className="panel">
        <div className="list-toolbar">
          <label>
            处理状态
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">全部</option>
              {Object.entries(labels).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <button className="button secondary small" onClick={list.reload}>
            刷新缺口
          </button>
        </div>
        {list.loading ? (
          <Loading />
        ) : list.error ? (
          <ErrorBox text={list.error} />
        ) : !list.data?.items.length ? (
          <Empty
            title="暂无知识缺口"
            description="未知回答与访客负反馈会在这里生成待处理记录。"
          />
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>咨询问题</th>
                  <th>来源</th>
                  <th>状态</th>
                  <th>记录时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((g: any) => (
                  <tr key={g.id}>
                    <td>{g.question}</td>
                    <td>
                      {g.reason === "unknown_answer"
                        ? "暂无依据"
                        : "需改进反馈"}
                      {g.rating && (
                        <small className="cell-sub">
                          最新评价：{g.rating === "up" ? "有帮助" : "需改进"}
                        </small>
                      )}
                    </td>
                    <td>
                      <Badge>{labels[g.status]}</Badge>
                    </td>
                    <td>{date(g.created_at, true)}</td>
                    <td>
                      <button
                        className="button secondary small"
                        onClick={() => {
                          setSelected(g);
                          setNote(g.note);
                          setNext(
                            g.status === "open" ? "processing" : g.status,
                          );
                          setContent("");
                        }}
                      >
                        处理缺口
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="list-toolbar">
          <span>共 {list.data?.total || 0} 条</span>
          <button
            className="button secondary small"
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
          >
            上一页
          </button>
          <span>第 {page} 页</span>
          <button
            className="button secondary small"
            disabled={page * 20 >= (list.data?.total || 0)}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </button>
        </div>
      </section>
      {selected && (
        <section className="panel quality-editor">
          <h2>{selected.question}</h2>
          <details>
            <summary>查看原回答与访客意见</summary>
            <p className="preserve-lines">{selected.answer}</p>
            <p>{selected.comment || "未填写意见"}</p>
          </details>
          <label>
            处理状态
            <select value={next} onChange={(e) => setNext(e.target.value)}>
              {Object.entries(labels).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label>
            人工处理结果
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={2000}
              placeholder="记录核实情况；创建草稿不等于已解决"
            />
          </label>
          <button className="button" disabled={busy} onClick={() => save()}>
            保存处理结果
          </button>
          {selected.document_id ? (
            <Link to="/knowledge">前往知识库审核补充草稿</Link>
          ) : (
            <>
              <label>
                补充知识草稿
                <textarea
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  placeholder="先核实事实，再填写可公开的知识内容；勿写入个人联系方式。"
                  maxLength={200000}
                />
              </label>
              <button
                className="button secondary"
                disabled={busy || content.trim().length < 10}
                onClick={() => save(true)}
              >
                创建待审核知识草稿
              </button>
            </>
          )}
        </section>
      )}
    </>
  );
}
function moneyOrUnknown(v: number | null | undefined) {
  return v === null || v === undefined ? "未知" : "¥" + Number(v).toFixed(4);
}
function Usage({ version }: { version: number }) {
  const r = useResource<any>("/admin/quality/usage", version),
    toast = useToast();
  const [form, setForm] = useState<any>({
      model: "",
      input_miss: "",
      input_hit: "",
      output: "",
      daily_budget: "",
      monthly_budget: "",
      enforce_over_budget: false,
    }),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    if (r.data?.pricing?.model)
      setForm({
        ...r.data.pricing,
        enforce_over_budget: !!r.data.pricing.enforce_over_budget,
      });
  }, [r.data]);
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const payload: Record<string, any> = {};
      for (const [k, v] of Object.entries(form)) {
        if (k === "updated_at") continue;
        if (k === "enforce_over_budget") payload[k] = !!v;
        else if (k === "model") payload[k] = v;
        else payload[k] = Number(v);
      }
      await api("/admin/quality/budget", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      toast(
        payload.enforce_over_budget
          ? "单价与预算已保存，并已显式启用超预算降级"
          : "单价与预算已保存（超预算仅告警，不自动降级）",
      );
      r.reload();
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  if (r.loading) return <Loading />;
  if (r.error) return <ErrorBox text={r.error} retry={r.reload} />;
  const d = r.data;
  return (
    <>
      <section className="panel quality-editor">
        <div className="row-between">
          <h2>调用记录</h2>
          <button className="button secondary small" onClick={r.reload}>
            刷新用量
          </button>
        </div>
        {(d.daily_alert || d.monthly_alert) && (
          <div className="error-box" role="alert">
            {d.daily_alert ? "今日" : ""}
            {d.daily_alert && d.monthly_alert ? "及" : ""}
            {d.monthly_alert ? "本月" : ""}
            估算费用已达到预算
            {d.enforce_over_budget
              ? "；已启用降级，新对话将改用资料摘录。"
              : "，请核对供应商账单（当前仅告警，未自动停止调用）。"}
          </div>
        )}
        {d.anomaly_growth && (
          <div className="error-box" role="status">
            {d.anomaly_growth.message} 今日 Token {d.anomaly_growth.today_tokens}
            ，近有记录日均约 {d.anomaly_growth.avg_tokens}。
          </div>
        )}
        {d.currently_degraded && (
          <p className="footnote">当前处于超预算降级状态，付费模型调用已暂停。</p>
        )}
        <p>{d.billing_note}</p>
        <dl className="usage-facts">
          <div>
            <dt>调用 / 失败</dt>
            <dd>
              {d.calls} / {d.failed_calls}
            </dd>
          </div>
          <div>
            <dt>已知 Token</dt>
            <dd>{Number(d.tokens || 0).toLocaleString()}</dd>
          </div>
          <div>
            <dt>今日 / 本月可估算部分</dt>
            <dd>
              {moneyOrUnknown(d.daily_estimated_cny)} /{" "}
              {moneyOrUnknown(d.monthly_estimated_cny)}
            </dd>
          </div>
          <div>
            <dt>无法估价 / 未知用量</dt>
            <dd>
              {d.unpriced_calls} / {d.unknown_usage_calls} 次
            </dd>
          </div>
        </dl>
        {(d.unpriced_calls > 0 || !d.pricing_ready) && (
          <p className="footnote">
            {!d.pricing_ready
              ? "尚未配置匹配模型的单价，费用显示为未知，不能视为免费。"
              : "存在无法估价的调用，上述金额不代表完整费用；未完成或中断的调用也可能产生供应商费用。"}
          </p>
        )}
        <h3>近 14 日用量趋势</h3>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>日期</th>
                <th>调用</th>
                <th>Token</th>
                <th>已估价</th>
                <th>估算费用</th>
              </tr>
            </thead>
            <tbody>
              {(d.trend_daily || []).map((row: any) => (
                <tr key={row.date}>
                  <td>{row.date}</td>
                  <td>{row.calls}</td>
                  <td>
                    {row.unknown_usage_calls && !row.tokens
                      ? "未知"
                      : Number(row.tokens || 0).toLocaleString()}
                  </td>
                  <td>
                    {row.priced_calls}/{row.calls}
                  </td>
                  <td>{moneyOrUnknown(row.estimated_cny)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3>账单对账辅助</h3>
        <p>{d.reconciliation?.note}</p>
        <dl className="usage-facts">
          <div>
            <dt>已估价调用</dt>
            <dd>{d.reconciliation?.priced_calls ?? 0}</dd>
          </div>
          <div>
            <dt>未估价 / 未知用量</dt>
            <dd>
              {d.reconciliation?.unpriced_calls ?? 0} /{" "}
              {d.reconciliation?.unknown_usage_calls ?? 0}
            </dd>
          </div>
          <div>
            <dt>站内已估合计</dt>
            <dd>{moneyOrUnknown(d.reconciliation?.estimated_cny_sum)}</dd>
          </div>
        </dl>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>模型 / 时间</th>
                <th>状态</th>
                <th>Token</th>
                <th>耗时</th>
                <th>估算费用</th>
              </tr>
            </thead>
            <tbody>
              {d.recent.map((c: any) => (
                <tr key={c.message_id}>
                  <td>
                    {c.model}
                    <small className="cell-sub">
                      {date(c.created_at, true)}
                    </small>
                  </td>
                  <td>
                    {c.state === "completed"
                      ? "已完成"
                      : c.state === "pending"
                        ? "处理中"
                        : "未完成"}
                    <small className="cell-sub">
                      {errors[c.error_type] || ""}
                    </small>
                  </td>
                  <td>{c.usage?.total_tokens ?? "未知"}</td>
                  <td>{c.latency_ms} ms</td>
                  <td>
                    {c.estimated_cny === null || c.estimated_cny === undefined
                      ? "未知"
                      : "¥" + Number(c.estimated_cny).toFixed(6)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!d.recent.length && (
            <p>尚无 v0.4 模型调用记录。历史消息的 Token 仍可在数据分析查看。</p>
          )}
        </div>
      </section>
      <form className="panel quality-editor" onSubmit={save}>
        <h2>估算单价与预算</h2>
        <p>
          请按当前供应商账单填写并定期核对，单价单位为人民币 / 百万
          Token。只对匹配模型的新调用估算，历史记录保留调用时单价。未配置单价或未收到用量时显示「未知」，不能当作免费。
        </p>
        <div className="form-grid">
          {[
            ["model", "模型名称（须与本地配置一致）"],
            ["input_miss", "输入：缓存未命中"],
            ["input_hit", "输入：缓存命中"],
            ["output", "输出单价"],
            ["daily_budget", "每日预算（元）"],
            ["monthly_budget", "每月预算（元）"],
          ].map(([k, label]) => (
            <label key={k}>
              {label}
              <input
                required
                type={k === "model" ? "text" : "number"}
                min={k.includes("budget") ? 0.01 : 0}
                step="any"
                value={form[k]}
                onChange={(e) => setForm({ ...form, [k]: e.target.value })}
              />
            </label>
          ))}
        </div>
        <label className="checkbox-line">
          <input
            type="checkbox"
            checked={!!form.enforce_over_budget}
            onChange={(e) =>
              setForm({ ...form, enforce_over_budget: e.target.checked })
            }
          />
          显式启用超预算降级（默认关闭；开启后达到日/月预算时新对话改为资料摘录，不再调用付费模型）
        </label>
        <button className="button" disabled={busy}>
          保存单价与预算策略
        </button>
      </form>
    </>
  );
}
function Evaluations({ version }: { version: number }) {
  const list = useResource<any[]>("/admin/quality/evaluations", version),
    [id, setId] = useState(""),
    [sample, setSample] = useState<any>(null),
    [answer, setAnswer] = useState(""),
    [note, setNote] = useState(""),
    [verdict, setVerdict] = useState("pass"),
    [busy, setBusy] = useState(false);
  const detail = useResource<any>(
      "/admin/quality/evaluations/" + id,
      id ? version : -1,
    ),
    toast = useToast();
  async function run() {
    setBusy(true);
    try {
      const r = await api("/admin/quality/evaluations", { method: "POST" });
      setId(r.id);
      setSample(null);
      list.reload();
      toast("已生成检索与标签样本，请人工核对实际回答");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function review(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api(`/admin/quality/evaluations/${id}/cases/${sample.id}`, {
        method: "PUT",
        body: JSON.stringify({ answer, note, verdict }),
      });
      detail.reload();
      list.reload();
      toast("已保存人工验收");
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <section className="panel quality-editor">
        <h2>五类问题验收集</h2>
        <p>
          覆盖八类考试课程，以及政策、未知、否定和售后。运行时仅检索资料并计算规则标签，不自动调用付费模型。
        </p>
        <div className="actions">
          <button className="button" disabled={busy} onClick={run}>
            {busy ? "处理中…" : "生成评测样本"}
          </button>
          <label>
            历史批次
            <select
              value={id}
              onChange={(e) => {
                setId(e.target.value);
                setSample(null);
              }}
            >
              <option value="">选择评测批次</option>
              {list.data?.map((r) => (
                <option key={r.id} value={r.id}>
                  {date(r.created_at, true)} · 已验收 {r.human_reviewed}/
                  {r.total}
                </option>
              ))}
            </select>
          </label>
        </div>
        {list.error && <ErrorBox text={list.error} />}
      </section>
      {id &&
        (detail.loading ? (
          <Loading />
        ) : detail.error ? (
          <ErrorBox text={detail.error} />
        ) : (
          detail.data && (
            <section className="panel quality-editor">
              <h2>
                人工验收 {detail.data.human_reviewed} /{" "}
                {detail.data.cases.length}
              </h2>
              <p>{detail.data.note}</p>
              <p>
                样本集通过率：
                {detail.data.answer_accuracy === null
                  ? "待全部人工验收"
                  : (detail.data.answer_accuracy * 100).toFixed(1) + "%"}
                。该数值仅代表本批样本。
              </p>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>类别</th>
                      <th>问题</th>
                      <th>检索来源</th>
                      <th>人工验收</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.data.cases.map((c: any) => (
                      <tr key={c.id}>
                        <td>{c.category}</td>
                        <td>{c.question}</td>
                        <td>
                          {c.sources.length} 条 · {c.analysis.level}
                        </td>
                        <td>
                          <button
                            className="button secondary small"
                            onClick={() => {
                              setSample(c);
                              setAnswer(c.answer);
                              setNote(c.note);
                              setVerdict(c.verdict || "pass");
                            }}
                          >
                            {c.verdict === "pass"
                              ? "通过"
                              : c.verdict === "fail"
                                ? "未通过"
                                : "填写验收"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )
        ))}
      {sample && (
        <form className="panel quality-editor" onSubmit={review}>
          <h2>{sample.question}</h2>
          <p>验收重点：{sample.expected}</p>
          <details>
            <summary>查看本次检索来源与标签</summary>
            {sample.sources.map((s: any, i: number) => (
              <p key={i}>
                <strong>
                  {s.title} · 版本 {s.version}
                </strong>
                <br />
                {s.text}
              </p>
            ))}
            <p>
              标签：{sample.analysis.tags?.join("、") || "待判定"} · 意向：
              {sample.analysis.level}
            </p>
          </details>
          <label>
            从官网测试后粘贴实际回答
            <textarea
              required
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              maxLength={10000}
            />
          </label>
          <label>
            验收结论
            <select
              value={verdict}
              onChange={(e) => setVerdict(e.target.value)}
            >
              <option value="pass">通过</option>
              <option value="fail">未通过</option>
            </select>
          </label>
          <label>
            人工核对依据
            <textarea
              required
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={2000}
            />
          </label>
          <button className="button" disabled={busy}>
            保存人工验收
          </button>
        </form>
      )}
    </>
  );
}
