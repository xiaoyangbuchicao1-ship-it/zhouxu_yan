const untitledNames = new Set(["", "新对话", "新建对话", "未命名对话"]);

export function isUntitledConversation(title: string | undefined) {
  return untitledNames.has((title || "").trim());
}

export function conversationTitle(question: string) {
  const title = question
    .trim()
    .replace(/^(?:#{1,6}\s+|>\s+|[-*+]\s+)/u, "")
    .replace(/\s+/gu, " ")
    .trim();
  return Array.from(title).slice(0, 80).join("") || "未命名对话";
}
