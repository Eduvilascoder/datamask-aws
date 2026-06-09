import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Box } from '@cloudscape-design/components';
import './markdown.css';

interface MarkdownViewerProps {
  /** Contenido Markdown a renderizar. */
  content: string;
}

/**
 * Renderiza Markdown (GFM) como HTML seguro usando react-markdown.
 *
 * react-markdown no usa dangerouslySetInnerHTML, por lo que es seguro frente a
 * XSS por defecto. Se mapean algunos elementos a tipografía de Cloudscape para
 * mantener la coherencia visual con el resto de la app.
 */
const MarkdownViewer: React.FC<MarkdownViewerProps> = ({ content }) => {
  return (
    <div className="markdown-preview">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <Box variant="h1" padding={{ top: 'm', bottom: 'xs' }}>
              {children}
            </Box>
          ),
          h2: ({ children }) => (
            <Box variant="h2" padding={{ top: 'm', bottom: 'xxs' }}>
              {children}
            </Box>
          ),
          h3: ({ children }) => (
            <Box variant="h3" padding={{ top: 's', bottom: 'xxs' }}>
              {children}
            </Box>
          ),
          h4: ({ children }) => (
            <Box variant="h4" padding={{ top: 'xs' }}>
              {children}
            </Box>
          ),
          p: ({ children }) => <Box variant="p">{children}</Box>,
          ul: ({ children }) => (
            <Box variant="p">
              <ul style={{ marginTop: 0, marginBottom: 0 }}>{children}</ul>
            </Box>
          ),
          ol: ({ children }) => (
            <Box variant="p">
              <ol style={{ marginTop: 0, marginBottom: 0 }}>{children}</ol>
            </Box>
          ),
          code: ({ children, className }) => {
            const isBlock = (className ?? '').includes('language-');
            if (isBlock) {
              return (
                <pre
                  style={{
                    background: '#f4f4f4',
                    border: '1px solid #e9ebed',
                    borderRadius: 4,
                    padding: 12,
                    overflowX: 'auto',
                    fontSize: 12,
                  }}
                >
                  <code>{children}</code>
                </pre>
              );
            }
            return (
              <code
                style={{
                  background: '#f4f4f4',
                  borderRadius: 3,
                  padding: '1px 4px',
                  fontSize: 13,
                }}
              >
                {children}
              </code>
            );
          },
          table: ({ children }) => (
            <div style={{ overflowX: 'auto' }}>
              <table className="markdown-preview-table">{children}</table>
            </div>
          ),
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote
              style={{
                borderLeft: '3px solid #d1d5db',
                margin: 0,
                padding: '4px 12px',
                color: '#5f6b7a',
              }}
            >
              {children}
            </blockquote>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
};

export default MarkdownViewer;
