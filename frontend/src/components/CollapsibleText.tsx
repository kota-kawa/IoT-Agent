import { useEffect, useMemo, useState } from 'react';

type CollapsibleTextProps = {
  text: string;
  maxLength?: number;
};

export default function CollapsibleText({ text, maxLength = 180 }: CollapsibleTextProps): JSX.Element {
  const safeText = text == null ? '' : String(text);
  const shouldCollapse = safeText.length > maxLength;
  const [collapsed, setCollapsed] = useState(shouldCollapse);
  const truncated = useMemo(() => `${safeText.slice(0, maxLength).trimEnd()}…`, [safeText, maxLength]);

  useEffect(() => {
    setCollapsed(shouldCollapse);
  }, [shouldCollapse, safeText]);

  const displayText = collapsed ? truncated : safeText;

  return (
    <div
      className="collapsible-text"
      data-state={collapsed ? 'collapsed' : 'expanded'}
    >
      <div className="collapsible-text__content" title={safeText}>
        {displayText}
      </div>
      {shouldCollapse && (
        <button
          type="button"
          className="collapsible-text__toggle"
          onClick={() => setCollapsed((prev) => !prev)}
          aria-expanded={!collapsed}
          aria-label={collapsed ? '全文を表示' : '折りたたむ'}
        >
          {collapsed ? 'もっと見る' : '閉じる'}
        </button>
      )}
    </div>
  );
}
