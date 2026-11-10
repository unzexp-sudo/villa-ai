import { useMemo, useRef, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import type { BuildingModel, Layer, Point3D, SystemKind } from "../contract";
import { CLASH_COLOR, SYSTEM_COLORS, SYSTEM_ORDER, level0, modelBBox } from "../contract";

interface Props {
  model: BuildingModel;
  layers: Partial<Record<SystemKind, Layer>>;
  clash?: { before: number; now: number } | null;
}

/**
 * 3D view: translucent shell, real service routes, clash markers.
 *
 * Lines are built from the SERVER's polylines, one BufferGeometry per system.
 * Nothing is smoothed, bent or interpolated here — a viewer that prettifies the
 * geometry would hide exactly the clashes the run is meant to expose.
 */
/**
 * FROZEN axis mapping, identical to the interior viewer
 * (`web/src/interior/Viewer3D.tsx`): plan (x, y, z) -> three (x, z, -y).
 *
 * Height becomes the viewer's up axis and the plan's y becomes -z, which has
 * determinant +1 — the model is not mirrored, just rotated into a y-up frame.
 * The earlier version rotated a group about X instead, which stood the whole
 * building on its edge and extruded the walls along the wrong axis.
 */
const toThree = (x: number, y: number, z: number): [number, number, number] => [x, z, -y];

export function Viewer3D({ model, layers, clash }: Props) {
  const [hidden, setHidden] = useState<Set<SystemKind>>(new Set());
  const [showShell, setShowShell] = useState(true);
  const centre = useMemo(() => {
    const [minx, miny, maxx, maxy] = modelBBox(model);
    if (maxx > minx && maxy > miny) {
      return { x: (minx + maxx) / 2, y: (miny + maxy) / 2, r: Math.max(maxx - minx, maxy - miny) };
    }
    return { x: 5, y: 4, r: 10 };
  }, [model]);

  return (
    <div className="viewer3d">
      <Canvas
        camera={{
          // The model is centred at the origin by the group's offset, so the
          // camera orbits that point rather than a plan coordinate.
          position: [centre.r * 0.75, centre.r * 0.85, centre.r * 0.95],
          fov: 42,
        }}
        style={{ background: "#c9ced4" }}
        dpr={[1, 1.6]}
      >
        <ambientLight intensity={0.85} />
        <directionalLight position={[8, 14, 6]} intensity={0.7} />
        <directionalLight position={[-6, 8, -8]} intensity={0.25} />

        <group position={[-centre.x, 0, centre.y]}>
          {showShell && <Shell model={model} />}
          {SYSTEM_ORDER.filter((s) => !hidden.has(s)).map((s) => {
            const layer = layers[s];
            if (!layer) return null;
            return <SystemLines key={s} layer={layer} color={SYSTEM_COLORS[s]} />;
          })}
        </group>

        <OrbitControls
          makeDefault
          target={[0, 1.2, 0]}
          enablePan
          minDistance={2}
          maxDistance={60}
          maxPolarAngle={Math.PI / 2.05}
        />
      </Canvas>

      <div className="v3d-legend card">
        <p className="eyebrow">Merged model</p>
        {SYSTEM_ORDER.map((s) => (
          <label key={s} className="v3d-row">
            <input
              type="checkbox"
              checked={!hidden.has(s)}
              onChange={() =>
                setHidden((prev) => {
                  const next = new Set(prev);
                  if (next.has(s)) next.delete(s);
                  else next.add(s);
                  return next;
                })
              }
            />
            <i style={{ background: SYSTEM_COLORS[s] }} />
            <span>{s[0].toUpperCase() + s.slice(1)}</span>
            <em className="muted small">Agent {SYSTEM_ORDER.indexOf(s) + 1}</em>
          </label>
        ))}
        <hr className="divider" />
        <label className="v3d-row">
          <input type="checkbox" checked={showShell} onChange={() => setShowShell((v) => !v)} />
          <span>Show slab & walls</span>
        </label>
      </div>

      <div className="v3d-clash card">
        <p className="eyebrow">Coordinator · clash check</p>
        <p className="v3d-clash-num" style={{ color: clash && clash.now > 0 ? CLASH_COLOR : "#4ade80" }}>
          {clash ? clash.now : "—"}
        </p>
        <p className="muted small">
          {clash
            ? `${clash.now === 1 ? "clash" : "clashes"} remaining of ${clash.before} found`
            : "waiting for the merge"}
        </p>
      </div>
    </div>
  );
}

function Shell({ model }: { model: BuildingModel }) {
  const lvl = level0(model);
  const h = model.ceiling_height || 2.7;
  return (
    <group>
      {lvl.rooms.map((r) => {
        // The floor is a horizontal polygon in the viewer's XZ plane. A
        // THREE.Shape lives in XY, so the shape is built in plan coordinates
        // and laid flat with a -90° X rotation.
        const pts = r.polygon.map((p) => new THREE.Vector2(p.x, p.y));
        const shape = new THREE.Shape(pts);
        return (
          <mesh key={r.id} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.005, 0]}>
            <shapeGeometry args={[shape]} />
            <meshBasicMaterial color="#ffffff" transparent opacity={0.5} side={THREE.DoubleSide} />
          </mesh>
        );
      })}
      {lvl.walls.map((w) => {
        const dx = w.end.x - w.start.x;
        const dy = w.end.y - w.start.y;
        const len = Math.hypot(dx, dy);
        if (len < 1e-6) return null;
        // The wall runs along the plan's +x/+y direction; in the viewer that
        // direction is +x/-z, so the box is turned by -angle about Y.
        const angle = Math.atan2(dy, dx);
        const [cx, cy, cz] = toThree((w.start.x + w.end.x) / 2, (w.start.y + w.end.y) / 2, 0);
        return (
          <mesh key={w.id} position={[cx, (w.height || h) / 2, cz]} rotation={[0, angle, 0]}>
            <boxGeometry args={[len, w.height || h, w.thickness]} />
            <meshStandardMaterial
              color="#dfe4ea"
              transparent
              opacity={w.is_exterior ? 0.42 : 0.26}
              roughness={0.9}
              depthWrite={false}
            />
          </mesh>
        );
      })}
    </group>
  );
}

function SystemLines({ layer, color }: { layer: Layer; color: string }) {
  const geometry = useMemo(() => {
    const positions: number[] = [];
    for (const seg of layer.segments) {
      const pts: Point3D[] = seg.polyline;
      for (let i = 0; i < pts.length - 1; i++) {
        // Same mapping as the shell: plan (x, y, z) -> three (x, z, -y).
        // Using +y here mirrored every route in Z, which put the whole MEP
        // network outside the building it belonged to.
        const [ax, ay, az] = toThree(pts[i].x, pts[i].y, pts[i].z);
        const [bx, by, bz] = toThree(pts[i + 1].x, pts[i + 1].y, pts[i + 1].z);
        positions.push(ax, ay, az, bx, by, bz);
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    return g;
  }, [layer]);

  const dotGeo = useMemo(() => new THREE.SphereGeometry(0.035, 8, 8), []);

  return (
    <group>
      <lineSegments geometry={geometry}>
        <lineBasicMaterial color={color} linewidth={2} />
      </lineSegments>
      {layer.devices.map((d) => {
        const [dx, dy, dz] = toThree(d.at.x, d.at.y, d.at.z);
        return (
          <mesh key={d.id} geometry={dotGeo} position={[dx, dy, dz]}>
            <meshBasicMaterial color={color} />
          </mesh>
        );
      })}
    </group>
  );
}
