import type { RuntimeActivityItem } from "../runtimeActivity";

export type RuntimeToastStackProps = {
  items: RuntimeActivityItem[];
  onOpenReview: () => void;
};

const MAX_VISIBLE_ITEMS = 2;

export function RuntimeToastStack({ items, onOpenReview }: RuntimeToastStackProps) {
  const visibleItems = items.slice(0, MAX_VISIBLE_ITEMS);
  if (!visibleItems.length) {
    return null;
  }
  const urgent = visibleItems.some((item) => item.status === "waiting" || item.status === "failed");
  const hiddenCount = Math.max(0, items.length - visibleItems.length);

  return (
    <div className="runtime-toast-layer" aria-live="polite">
      <button
        className={urgent ? "runtime-toast-stack urgent" : "runtime-toast-stack"}
        type="button"
        onClick={onOpenReview}
        aria-label="打开运行审查"
      >
        {visibleItems.map((item) => (
          <span className={`runtime-toast-row ${item.kind} ${item.status}`} key={item.id}>
            <span className="runtime-toast-main">
              <span className="runtime-toast-dot" aria-hidden="true" />
              <span className="runtime-toast-title">{item.title}</span>
            </span>
            {item.detail ? <span className="runtime-toast-detail">{item.detail}</span> : null}
          </span>
        ))}
        {hiddenCount > 0 ? <span className="runtime-toast-more">还有 {hiddenCount} 条</span> : null}
      </button>
    </div>
  );
}
