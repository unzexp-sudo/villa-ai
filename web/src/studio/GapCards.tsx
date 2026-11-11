import { useState } from "react";
import type { MissingItem } from "../contract";

interface Props {
  items: MissingItem[];
  busy: boolean;
  warnings?: string[];
  onAction: (item: MissingItem, action: "place" | "suggest" | "skip", at?: { x: number; y: number }) => void;
  onDone: () => void;
}

/**
 * The id of the card currently being dragged, published at module scope.
 *
 * The drop handler lives in a different subtree (the plan canvas), and a
 * <button> inside the card can swallow the native drag in some browsers. When
 * that happens DataTransfer arrives empty, and this is what keeps the drop
 * working instead of silently doing nothing.
 */
let draggedId: string | null = null;
export function currentDragId(): string | null {
  return draggedId;
}

/**
 * The three verbs from the build plan, per missing item: drag it on, suggest a
 * spot, or skip and assume. Skip is never silent — the server writes an
 * assumption, and the results screen lists it.
 *
 * Dragging sets a dataTransfer payload AND remembers the id: a drop can arrive
 * with an empty DataTransfer in some browsers (and always when the drag starts
 * from a nested element), so relying on the payload alone leaves the user
 * dragging a card onto the plan and watching nothing happen.
 */
export function GapCards({ items, busy, warnings, onAction, onDone }: Props) {
  const [dragging, setDragging] = useState<string | null>(null);

  return (
    <aside className="gaps">
      <header className="row spread">
        <div>
          <p className="eyebrow">Step 3 · fill the gaps</p>
          <h3>{items.length ? `${items.length} item${items.length === 1 ? "" : "s"} missing` : "Nothing missing"}</h3>
        </div>
      </header>

      {warnings && warnings.length > 0 && (
        <div className="parse-warnings" role="alert">
          <p className="eyebrow">Read from your file</p>
          <ul>
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {items.length === 0 && (
        <p className="muted small">
          Every required item is present in this plan. You can still design; anything the
          engine has to assume will show up in the assumptions list.
        </p>
      )}

      <div className="gap-list">
        {items.map((item) => (
          <article
            key={item.id}
            className="gap-card"
            draggable={!busy}
            title="Drag this onto the plan"
            onDragStart={(e) => {
              e.dataTransfer.setData("text/villa-item", item.id);
              e.dataTransfer.effectAllowed = "copy";
              setDragging(item.id);
              draggedId = item.id;
            }}
            onDragEnd={() => {
              setDragging(null);
              draggedId = null;
            }}
            style={dragging === item.id ? { opacity: 0.5 } : undefined}
          >
            <div className="row spread">
              <strong>{item.label}</strong>
              <span
                className="tag"
                style={{
                  color: item.severity === "error" ? "#c0392b" : "#9c8b3a",
                  borderColor: item.severity === "error" ? "#e8c4c0" : "#e0d5b8",
                }}
              >
                {item.severity}
              </span>
            </div>
            <p className="muted small">{item.prompt}</p>
            <div className="gap-actions">
              <button disabled={busy} onClick={() => onAction(item, "suggest")}>
                Suggest a spot
              </button>
              <button disabled={busy} onClick={() => onAction(item, "skip")}>
                Skip &amp; assume
              </button>
            </div>
            <p className="muted x-small">
              {dragging === item.id
                ? `Now click where the ${item.label.toLowerCase()} goes.`
                : "Or drag the card onto the plan."}
            </p>
          </article>
        ))}
      </div>

      <button className="btn-primary wide" disabled={busy} onClick={onDone}>
        {items.length ? "Design with what we have" : "Run the five agents"}
      </button>
    </aside>
  );
}

/** The item id in a drop, from the payload or from the gesture in progress. */
export function draggedItemId(dt: DataTransfer | null, fallback: string | null): string | null {
  const fromPayload = dt?.getData("text/villa-item");
  return fromPayload || fallback;
}
