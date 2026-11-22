import { memo } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowUpRight, BookOpen } from "lucide-react";

const markdownComponents: Components = {
  a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">
    {children}<ArrowUpRight size={13} aria-hidden="true" />
  </a>,
  table: ({ children }) => <div className="ws-table-scroll" tabIndex={0} role="region" aria-label="回答中的表格"><table>{children}</table></div>,
  img: ({ alt }) => <span className="ws-muted">[图片：{alt || "未加载的外部图片"}]</span>,
};

export const Markdown = memo(function Markdown({ content, onSource }: { content: string; onSource?: (id: string) => void }) {
  const components: Components = { ...markdownComponents, a: ({ children, href }) => {
    const match = href?.match(/^(?:\/api)?\/workspace\/sources\/(paper-[A-Za-z0-9._-]+)$/);
    return match && onSource
      ? <button type="button" className="ws-citation-link" onClick={() => onSource(match[1])}>{children}<BookOpen size={13} aria-hidden="true" /></button>
      : <a href={href} target="_blank" rel="noopener noreferrer">{children}<ArrowUpRight size={13} aria-hidden="true" /></a>;
  } };
  return <div className="ws-prose"><ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{content}</ReactMarkdown></div>;
});
