import { useEffect } from "react";
import {
  GraduationCap,
  ArrowRight,
  Check,
  BookOpen,
  MessagesSquare,
  ArrowUpRight,
} from "lucide-react";

const courses = [
  ["公安联考", "公安专业知识与公共科目", "police"],
  ["公务员国考", "公共科目基础与专项练习", "national"],
  ["公务员省考", "围绕地区考情安排学习", "provincial"],
  ["选调生考试", "公告梳理与笔面试训练", "selected"],
  ["事业单位考试", "根据岗位公告匹配科目", "public"],
  ["社会招警与辅警", "分清招录渠道与岗位性质", "auxiliary"],
  ["社区工作者考试", "社区治理与实务表达", "community"],
];
export function Demo() {
  useEffect(() => {
    const old = document.getElementById("zhixing-widget-loader");
    if (!old) {
      const script = document.createElement("script");
      script.src = "/widget.js";
      script.id = "zhixing-widget-loader";
      document.body.appendChild(script);
    }
  }, []);
  function open() {
    window.dispatchEvent(new Event("zhixing:open"));
  }
  return (
    <div className="demo-site">
      <div className="demo-topnotice">
        页面内容为示例，机构及课程信息不构成实际服务或报价。
      </div>
      <header className="demo-nav">
        <a href="/demo" className="demo-brand">
          <GraduationCap size={29} />
          知行公考<span>让备考更有方向</span>
        </a>
        <nav>
          <a href="#courses">课程方向</a>
          <a href="#approach">学习服务</a>
          <a href="/" className="demo-admin">
            管理后台 <ArrowUpRight size={14} />
          </a>
        </nav>
        <button className="button" onClick={open}>
          咨询学习顾问
        </button>
      </header>
      <main>
        <section className="demo-hero">
          <div>
            <div className="demo-start">
              <span />
              从了解自己，到找准方向
            </div>
            <h1>
              你的备考之路，
              <br />
              我们一起认真规划。
            </h1>
            <p>
              无论是第一次准备公职考试，还是希望突破某个薄弱环节，先把目标、基础和时间聊清楚，再选择适合的课程。
            </p>
            <div className="actions">
              <button className="button" onClick={open}>
                聊聊我的备考计划 <ArrowRight size={17} />
              </button>
              <a href="#courses" className="demo-text-link">
                了解课程方向
              </a>
            </div>
            <div className="demo-hero-notes">
              <span>
                <Check size={15} />
                先了解，再选择
              </span>
              <span>
                <Check size={15} />
                支持线上咨询
              </span>
              <span>
                <Check size={15} />
                不作保过承诺
              </span>
            </div>
          </div>
          <aside className="study-sheet">
            <div className="sheet-heading">
              <BookOpen size={21} />
              <strong>一份清晰的学习计划</strong>
            </div>
            <div className="sheet-step">
              <span>目标</span>
              <div>
                <strong>你想参加哪类考试？</strong>
                <p>先确认考试、年度、地区与岗位</p>
              </div>
              <Check size={17} />
            </div>
            <div className="sheet-step">
              <span>基础</span>
              <div>
                <strong>从现在的水平出发</strong>
                <p>梳理已学内容与需要突破的环节</p>
              </div>
              <Check size={17} />
            </div>
            <div className="sheet-step">
              <span>行动</span>
              <div>
                <strong>把学习放进实际生活</strong>
                <p>根据可投入的时间选择学习方式</p>
              </div>
              <ArrowRight size={17} />
            </div>
            <button onClick={open}>
              <MessagesSquare size={17} />
              问问学习顾问
              <ArrowRight size={15} />
            </button>
            <small>AI 资料咨询与人工顾问配合服务</small>
          </aside>
        </section>
        <section id="courses" className="demo-courses">
          <div className="demo-section-heading">
            <h2>先找到你的考试方向</h2>
            <p>
              不同考试，对象与要求不同。
              <br />
              具体资格和安排请核对官方公告。
            </p>
          </div>
          <div className="course-grid">
            {courses.map(([name, description, id], index) => (
              <button className="course-tile" key={id} onClick={open}>
                <span className="course-number">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <h3>{name}</h3>
                <p>{description}</p>
                <span className="course-link">
                  了解课程 <ArrowUpRight size={16} />
                </span>
              </button>
            ))}
          </div>
        </section>
        <section id="approach" className="demo-approach">
          <div>
            <h2>
              先回答你的问题，
              <br />
              再讨论合适的课程。
            </h2>
            <p>
              点击右下角的“在线咨询”，即可询问课程内容、价格和试听流程。回答会附上知识来源；资料不足时会提示核实，也可以随时申请人工服务。
            </p>
            <button className="button" onClick={open}>
              开始咨询 <ArrowRight size={16} />
            </button>
          </div>
          <div className="approach-list">
            <div>
              <strong>有据可循</strong>
              <p>课程信息来自已审核的企业知识，来源可以查看。</p>
            </div>
            <div>
              <strong>自主选择</strong>
              <p>无需留下手机号即可咨询公开信息。</p>
            </div>
            <div>
              <strong>边界清楚</strong>
              <p>培训不等于招录，不提供内部名额或录用保证。</p>
            </div>
          </div>
        </section>
      </main>
      <footer className="demo-footer">
        <a className="demo-brand" href="/demo">
          <GraduationCap size={24} />
          知行公考
        </a>
        <span>课程与服务以机构实际公布的信息为准，不提供录用承诺。</span>
        <a href="/">进入管理工作台</a>
      </footer>
    </div>
  );
}
