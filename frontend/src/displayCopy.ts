// Only translate known system-generated defaults. User-authored records stay intact.
const seedWelcome =
  "您好，我是知行学习顾问。您正在准备哪一类考试？我可以帮您了解课程、学习方式或预约试听。当前机构与课程资料均为演示设定。";
const seedMessage =
  "这是合成演示对话，用于展示咨询管理。请在官网演示页测试真实知识检索。";

export function welcomeCopy(value: string | undefined): string {
  if (value === seedWelcome)
    return "您好，我是知行学习顾问。您正在准备哪一类考试？我可以帮您了解课程、学习方式或预约试听。课程与价格以机构公布的信息为准。";
  return value || "正在连接企业知识库…";
}

export function sourcePageCopy(value: string): string {
  return value === "官网演示" ? "官网" : value;
}

export function messageCopy(content: string, mode?: string): string {
  return mode === "sample" && content === seedMessage
    ? "顾问可在此继续查看和跟进访客咨询。"
    : content;
}

export function batchDisplayName(name: string): string {
  return name === "演示基础班" ? "基础班" : name;
}

export function systemMessageCopy(value: string): string {
  const messages: Record<string, string> = {
    "模型鉴权失败，请管理员检查本地 API Key。":
      "模型鉴权失败，请管理员检查服务端 API Key。",
    演示记录与真实业务记录不能关联: "不同数据范围的记录不能关联",
    演示档案与真实业务档案不能合并: "不同数据范围的档案不能合并",
  };
  return messages[value] || value;
}
