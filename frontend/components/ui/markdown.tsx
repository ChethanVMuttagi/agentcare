import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

/**
 * Renders AI Assistant reply text as GitHub-flavored Markdown.
 * `react-markdown` never uses `dangerouslySetInnerHTML` — raw HTML in the
 * source is escaped, not executed — so this is safe for model-generated
 * text with no extra sanitization step.
 */
export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn("space-y-2 text-sm leading-relaxed", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children: nodeChildren }) => <p className="mb-2 last:mb-0">{nodeChildren}</p>,
          ul: ({ children: nodeChildren }) => (
            <ul className="ml-4 list-disc space-y-1">{nodeChildren}</ul>
          ),
          ol: ({ children: nodeChildren }) => (
            <ol className="ml-4 list-decimal space-y-1">{nodeChildren}</ol>
          ),
          a: ({ children: nodeChildren, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-2"
            >
              {nodeChildren}
            </a>
          ),
          code: ({ children: nodeChildren, className: codeClassName }) => {
            const isBlock = /language-/.test(codeClassName ?? "");
            if (isBlock) {
              return (
                <code className="block overflow-x-auto rounded-md bg-slate-900/5 p-2 font-mono text-xs dark:bg-white/10">
                  {nodeChildren}
                </code>
              );
            }
            return (
              <code className="rounded bg-slate-900/10 px-1 py-0.5 font-mono text-xs dark:bg-white/10">
                {nodeChildren}
              </code>
            );
          },
          pre: ({ children: nodeChildren }) => (
            <pre className="overflow-x-auto">{nodeChildren}</pre>
          ),
          table: ({ children: nodeChildren }) => (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-xs">{nodeChildren}</table>
            </div>
          ),
          th: ({ children: nodeChildren }) => (
            <th className="border border-slate-200 px-2 py-1 text-left font-medium dark:border-slate-700">
              {nodeChildren}
            </th>
          ),
          td: ({ children: nodeChildren }) => (
            <td className="border border-slate-200 px-2 py-1 dark:border-slate-700">
              {nodeChildren}
            </td>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
