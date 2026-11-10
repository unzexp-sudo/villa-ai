import { useMemo, useState } from "react";
import type { BuildingModel, Layer, SystemKind } from "../contract";
import { CLASH_COLOR, SYSTEM_COLORS, SYSTEM_ORDER, level0 } from "../contract";

interface Props {
  model: BuildingModel;
  layers: Partial<Record<SystemKind, Layer>>;
  showLabels?: boolean;
  height?: number;
  onPick?: (p: { x: number; y: number }) => void;
  markers?: { x: number; y: number; label: string; color?: string; pulsing?: boolean }[];
  /** Called with plan-space metres when a gap card is dropped on the drawing. */
  onDropAt?: (p: { x: number; y: number }, dataTransfer: DataTransfer | null) => void;
}

/**
 * SVG plan view. Model space is metres with +y up; SVG is +y down, so everything
 * is flipped through `y = maxY - y`. One transform, applied once — mixing the
 * two conventions per-element is how plans end up mirrored.
 */
export function PlanCanvas({
  model,
  layers,
  showLabels = false,
  height = 420,
  onPick,
  markers,
  onDropAt,
}: Props) {
  const [dragOver, setDragOver] = useState(false);
  const geom = useMemo(() => {
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    for (const lvl of model.levels) {
      for (const w of lvl.walls) {
        minx = Math.min(minx, w.start.x, w.end.x);
        maxx = Math.max(maxx, w.start.x, w.end.x);
        miny = Math.min(miny, w.start.y, w.end.y);
        maxy = Math.max(maxy, w.start.y, w.end.y);
      }
    }
    if (!Number.isFinite(minx)) { minx = 0; miny = 0; maxx = 10; maxy = 8; }
    const pad = Math.max(1, Math.max(maxx - minx, maxy - miny) * 0.08);
    return { minx: minx - pad, miny: miny - pad, maxx: maxx + pad, maxy: maxy + pad };
  }, [model]);

  const w = geom.maxx - geom.minx;
  const h = geom.maxy - geom.miny;
  const vb = `${geom.minx} ${geom.miny} ${w} ${h}`;
  const stroke = Math.max(w, h) / 320;

  const level = level0(model);

  // The SVG uses preserveAspectRatio="xMidYMid meet", so the drawing is
  // letterboxed inside the element. Mapping against the element's full width
  // would offset every drop whenever the plan is not the same aspect ratio as
  // its box — which is most of the time.
  const toPlanInsideBox = (clientX: number, clientY: number, el: SVGSVGElement) => {
    const rect = el.getBoundingClientRect();
    const scale = Math.min(rect.width / w, rect.height / h);
    const drawnW = w * scale;
    const drawnH = h * scale;
    const padX = (rect.width - drawnW) / 2;
    const padY = (rect.height - drawnH) / 2;
    return {
      x: geom.minx + (clientX - rect.left - padX) / scale,
      y: geom.maxy - (clientY - rect.top - padY) / scale,
    };
  };

  const inside = (p: { x: number; y: number }) =>
    p.x >= geom.minx && p.x <= geom.maxx && p.y >= geom.miny && p.y <= geom.maxy;

  return (
    <svg
      className={`plan-canvas${dragOver ? " drop-target" : ""}`}
      viewBox={vb}
      style={{ height, width: "100%" }}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="Floor plan"
      onClick={(e) => {
        if (!onPick) return;
        const p = toPlanInsideBox(e.clientX, e.clientY, e.currentTarget);
        if (inside(p)) onPick(p);
      }}
      onDragOver={(e) => {
        if (!onDropAt) return;
        e.preventDefault(); // required, or the browser refuses the drop
        e.dataTransfer.dropEffect = "copy";
        if (!dragOver) setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        if (!onDropAt) return;
        e.preventDefault();
        setDragOver(false);
        const p = toPlanInsideBox(e.clientX, e.clientY, e.currentTarget);
        if (inside(p)) onDropAt(p, e.dataTransfer);
      }}
    >
      {/* flip: model +y up -> svg +y down */}
      <g transform={`translate(0 ${geom.miny + geom.maxy}) scale(1 -1)`}>
        {level.rooms.map((r) => {
          const pts = r.polygon.map((p) => `${p.x},${p.y}`).join(" ");
          return (
            <polygon
              key={r.id}
              points={pts}
              fill={r.wet ? "rgba(34,211,238,0.07)" : "rgba(255,255,255,0.025)"}
              stroke="none"
            />
          );
        })}

        {level.walls.map((wall) => (
          <line
            key={wall.id}
            x1={wall.start.x}
            y1={wall.start.y}
            x2={wall.end.x}
            y2={wall.end.y}
            stroke={wall.is_exterior ? "#e8eef5" : "#8f9bab"}
            strokeWidth={Math.max(wall.thickness, stroke * 1.6)}
            strokeLinecap="square"
          />
        ))}

        {level.openings.map((o) => {
          const wall = level.walls.find((wl) => wl.id === o.wall_id);
          if (!wall) return null;
          const dx = wall.end.x - wall.start.x;
          const dy = wall.end.y - wall.start.y;
          const len = Math.hypot(dx, dy) || 1;
          const ux = dx / len, uy = dy / len;
          const a = { x: wall.start.x + ux * o.at, y: wall.start.y + uy * o.at };
          const b = { x: a.x + ux * o.width, y: a.y + uy * o.width };
          return (
            <line
              key={o.id}
              x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              stroke="#0b0f14" strokeWidth={wall.thickness * 1.15} strokeLinecap="butt"
            />
          );
        })}

        {level.fixtures.map((f) => (
          <circle key={f.id} cx={f.at.x} cy={f.at.y} r={stroke * 2.2} fill="#5b6676" />
        ))}

        {level.equipment.map((e) => (
          <rect
            key={e.id}
            x={e.at.x - stroke * 3}
            y={e.at.y - stroke * 3}
            width={stroke * 6}
            height={stroke * 6}
            fill="none"
            stroke="#c3ccd8"
            strokeWidth={stroke}
          />
        ))}

        {SYSTEM_ORDER.map((sys) => {
          const layer = layers[sys];
          if (!layer) return null;
          const color = SYSTEM_COLORS[sys];
          return (
            <g key={sys} style={{ opacity: layer.status === "done" ? 1 : 0.65 }}>
              {layer.segments.map((seg) => (
                <polyline
                  key={seg.id}
                  points={seg.polyline.map((p) => `${p.x},${p.y}`).join(" ")}
                  fill="none"
                  stroke={color}
                  strokeWidth={stroke * 1.15}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              ))}
              {layer.segments.map((seg) => {
                const end = seg.polyline[seg.polyline.length - 1];
                return end ? (
                  <circle key={`${seg.id}-end`} cx={end.x} cy={end.y} r={stroke * 1.1} fill={color} />
                ) : null;
              })}
              {layer.devices.map((d) => (
                <rect
                  key={d.id}
                  x={d.at.x - stroke * 1.6}
                  y={d.at.y - stroke * 1.6}
                  width={stroke * 3.2}
                  height={stroke * 3.2}
                  fill={color}
                />
              ))}
            </g>
          );
        })}

        {markers?.map((m, i) => (
          <g key={i}>
            {m.pulsing !== false && (
              <circle cx={m.x} cy={m.y} r={stroke * 6} fill="none" stroke={m.color ?? CLASH_COLOR} strokeWidth={stroke * 0.9} opacity={0.45}>
                <animate attributeName="r" values={`${stroke * 4};${stroke * 8};${stroke * 4}`} dur="1.8s" repeatCount="indefinite" />
                <animate attributeName="opacity" values="0.55;0.08;0.55" dur="1.8s" repeatCount="indefinite" />
              </circle>
            )}
            <circle cx={m.x} cy={m.y} r={stroke * 2.4} fill={m.color ?? CLASH_COLOR} />
          </g>
        ))}

        {showLabels &&
          level.rooms.map((r) => {
            // Inside the flipped group, so the centroid is used as-is.
            const cx = r.polygon.reduce((s, p) => s + p.x, 0) / (r.polygon.length || 1);
            const cy = r.polygon.reduce((s, p) => s + p.y, 0) / (r.polygon.length || 1);
            const fs = Math.max(w, h) / 62;
            return (
              // The group flips y, which would render the glyphs upside down.
              // Rotating each label by 180° about its own anchor undoes that
              // without disturbing its position.
              <g key={r.id} transform={`rotate(180 ${cx} ${cy})`}>
                <text
                  x={cx}
                  y={cy}
                  fill="#8b98a8"
                  fontSize={fs}
                  textAnchor="middle"
                  dominantBaseline="middle"
                >
                  {r.name}
                </text>
              </g>
            );
          })}
      </g>
    </svg>
  );
}
