/**
 * Markdown 渲染组件
 * 支持 GFM (GitHub Flavored Markdown)、代码高亮、表格等
 */

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import type { Components } from 'react-markdown';

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export default function MarkdownRenderer({ content, className = '' }: MarkdownRendererProps) {
  const components: Components = {
    code({ className: codeClassName, children, ...props }) {
      const match = /language-(\w+)/.exec(codeClassName || '');
      const isInline = !match && !String(children).includes('\n');

      if (isInline) {
        return (
          <code
            className="px-1.5 py-0.5 bg-indigo-50 text-indigo-600 rounded-md text-[0.85em] font-mono font-medium border border-indigo-100/60"
            {...props}
          >
            {children}
          </code>
        );
      }

      return (
        <div className="relative group my-4 rounded-xl overflow-hidden shadow-sm">
          {match && (
            <div className="flex items-center justify-between px-4 py-2 bg-[#282a36] border-b border-white/5">
              <div className="flex items-center space-x-2">
                <div className="flex space-x-1.5">
                  <div className="w-3 h-3 rounded-full bg-red-500/80"></div>
                  <div className="w-3 h-3 rounded-full bg-yellow-500/80"></div>
                  <div className="w-3 h-3 rounded-full bg-green-500/80"></div>
                </div>
                <span className="text-xs text-slate-400 font-mono ml-2">{match[1]}</span>
              </div>
              <button
                onClick={() => navigator.clipboard.writeText(String(children).replace(/\n$/, ''))}
                className="text-xs text-slate-500 hover:text-slate-300 transition-colors opacity-0 group-hover:opacity-100"
              >
                📋 复制
              </button>
            </div>
          )}
          <SyntaxHighlighter
            style={oneDark}
            language={match ? match[1] : 'text'}
            PreTag="div"
            customStyle={{
              margin: 0,
              borderRadius: match ? '0' : '0.75rem',
              fontSize: '0.8rem',
              maxHeight: '500px',
              overflow: 'auto',
              padding: '1rem',
              lineHeight: '1.6',
            }}
          >
            {String(children).replace(/\n$/, '')}
          </SyntaxHighlighter>
        </div>
      );
    },

    table({ children }) {
      return (
        <div className="overflow-x-auto my-4 rounded-xl border border-slate-200 shadow-sm">
          <table className="min-w-full text-sm">
            {children}
          </table>
        </div>
      );
    },
    thead({ children }) {
      return <thead className="bg-slate-50 border-b border-slate-200">{children}</thead>;
    },
    th({ children }) {
      return (
        <th className="px-4 py-3 text-left font-semibold text-slate-700 text-xs uppercase tracking-wider">
          {children}
        </th>
      );
    },
    td({ children }) {
      return (
        <td className="px-4 py-3 text-slate-600 border-t border-slate-100">
          {children}
        </td>
      );
    },

    a({ href, children }) {
      return (
        <a href={href} target="_blank" rel="noopener noreferrer"
          className="text-blue-500 hover:text-blue-600 underline decoration-blue-300/50 hover:decoration-blue-400 underline-offset-2 transition-colors">
          {children}
        </a>
      );
    },

    p({ children }) {
      return <p className="my-2.5 leading-[1.8] text-slate-700">{children}</p>;
    },

    h1({ children }) {
      return <h1 className="text-xl font-black mt-6 mb-3 text-slate-800 pb-2 border-b border-slate-200">{children}</h1>;
    },
    h2({ children }) {
      return (
        <h2 className="text-lg font-bold mt-5 mb-2.5 text-slate-800 flex items-center">
          <span className="w-1 h-5 bg-gradient-to-b from-blue-500 to-indigo-500 rounded-full mr-2.5 shrink-0"></span>
          {children}
        </h2>
      );
    },
    h3({ children }) {
      return <h3 className="text-base font-bold mt-4 mb-2 text-slate-800">{children}</h3>;
    },

    ul({ children }) {
      return <ul className="my-3 space-y-1.5 pl-0">{children}</ul>;
    },
    ol({ children }) {
      return <ol className="list-decimal my-3 space-y-1.5 pl-5">{children}</ol>;
    },
    li({ children }) {
      return (
        <li className="text-slate-700 leading-relaxed flex items-start">
          <span className="mr-2 mt-2 w-1.5 h-1.5 rounded-full bg-slate-400 shrink-0"></span>
          <span className="flex-1">{children}</span>
        </li>
      );
    },

    blockquote({ children }) {
      return (
        <blockquote className="border-l-4 border-blue-400 bg-blue-50/50 pl-4 py-3 my-4 rounded-r-xl text-slate-600 italic">
          {children}
        </blockquote>
      );
    },

    hr() {
      return <hr className="my-6 border-0 h-px bg-gradient-to-r from-transparent via-slate-300 to-transparent" />;
    },

    img({ src, alt }) {
      return (
        <img src={src} alt={alt || ''} className="max-w-full rounded-xl my-3 shadow-md" />
      );
    },

    pre({ children }) {
      return <div className="overflow-hidden rounded-xl my-4">{children}</div>;
    },

    strong({ children }) {
      return <strong className="font-bold text-slate-800">{children}</strong>;
    },

    em({ children }) {
      return <em className="italic text-slate-600">{children}</em>;
    },
  };

  return (
    <div className={`markdown-body text-sm text-slate-700 break-words overflow-hidden ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
