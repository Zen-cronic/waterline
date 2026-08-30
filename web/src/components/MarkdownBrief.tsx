import type { ReactNode } from "react";

function inlineMarkdown(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={index}>{part.slice(1, -1)}</code>;
    }
    return part;
  });
}

export function MarkdownBrief({ children }: { children: string }) {
  return (
    <div className="brief markdown-brief">
      {children.split("\n").map((raw, index) => {
        const line = raw.trim();
        if (!line) return <div className="md-space" key={index} aria-hidden="true" />;
        if (/^---+$/.test(line)) return <hr key={index} />;
        const heading = line.match(/^(#{1,3})\s+(.+)$/);
        if (heading) {
          const Heading = `h${heading[1].length + 2}` as "h3" | "h4" | "h5";
          return <Heading key={index}>{inlineMarkdown(heading[2])}</Heading>;
        }
        const ordered = line.match(/^(\d+)\.\s+(.+)$/);
        if (ordered) {
          return <div className="md-item" key={index}><span>{ordered[1]}.</span><p>{inlineMarkdown(ordered[2])}</p></div>;
        }
        const bullet = line.match(/^[-*]\s+(.+)$/);
        if (bullet) {
          return <div className="md-item" key={index}><span>•</span><p>{inlineMarkdown(bullet[1])}</p></div>;
        }
        return <p key={index}>{inlineMarkdown(line)}</p>;
      })}
    </div>
  );
}
