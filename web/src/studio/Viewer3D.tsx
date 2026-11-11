import { useMemo, useRef, useState } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import type { BuildingModel, Layer, Point3D, Segment, SystemKind } from "../contract";
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
  const [dimensionScale, setDimensionScale] = useState<1 | 6>(6);
  const [selectedSegment, setSelectedSegment] = useState<Segment | null>(null);
  // Camera commands are issued from the legend card, which lives outside the
  // <Canvas>. The ref is filled in by <CameraRig /> once the scene is mounted.
  const cameraCmd = useRef<((cmd: "in" | "out" | "reset") => void) | null>(null);
  const centre = useMemo(() => {
    const [minx, miny, maxx, maxy] = modelBBox(model);
    if (maxx > minx && maxy > miny) {
      return { x: (minx + maxx) / 2, y: (miny + maxy) / 2, r: Math.max(maxx - minx, maxy - miny) };
    }
    return { x: 5, y: 4, r: 10 };
  }, [model]);

  // The orbit limit is derived from the model, not fixed. A hard 200 m cap
  // traps the camera inside any model bigger than that, and the symptom is a
  // viewer that refuses to zoom out no matter how far you pull.
  const orbit = useMemo(() => {
    const r = Math.max(centre.r, 1);
    return {
      min: Math.max(0.5, r * 0.02),
      max: Math.max(50, r * 12),
    };
  }, [centre.r]);

  return (
    <div className="viewer3d">
      <Canvas
        camera={{
          // The model is centred at the origin by the group's offset, so the
          // camera orbits that point rather than a plan coordinate.
          position: [centre.r * 0.75, centre.r * 0.85, centre.r * 0.95],
          fov: 42,
          // The near/far planes scale with the model. Fixed planes either clip
          // a large model away or z-fight a small one.
          near: Math.max(0.05, centre.r / 2000),
          far: Math.max(500, centre.r * 60),
        }}
        style={{ background: "#a8c8d8" }}
        dpr={[1, 1.6]}
      >
        <ambientLight intensity={1.2} />
        <directionalLight position={[8, 14, 6]} intensity={1.1} />
        <directionalLight position={[-6, 8, -8]} intensity={0.4} />

        <group position={[-centre.x, 0, centre.y]}>
          {showShell && <Shell model={model} />}
          {SYSTEM_ORDER.filter((s) => !hidden.has(s)).map((s) => {
            const layer = layers[s];
            if (!layer) return null;
            return (
              <SystemLines
                key={s}
                layer={layer}
                color={SYSTEM_COLORS[s]}
                dimensionScale={dimensionScale}
                onSelect={setSelectedSegment}
              />
            );
          })}
        </group>

        <CameraControls
          target={[0, 1.2, 0]}
          minDistance={orbit.min}
          maxDistance={orbit.max}
          cmdRef={cameraCmd}
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
        <div className="v3d-zoom" role="group" aria-label="Camera zoom">
          <button
            type="button"
            className="btn btn-icon"
            onClick={() => cameraCmd.current?.("in")}
            aria-label="Zoom in"
            title="Zoom in"
          >
            +
          </button>
          <button
            type="button"
            className="btn btn-icon"
            onClick={() => cameraCmd.current?.("out")}
            aria-label="Zoom out"
            title="Zoom out"
          >
            &minus;
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => cameraCmd.current?.("reset")}
          >
            Reset view
          </button>
        </div>
        <p className="muted x-small">Scroll to zoom · drag to orbit</p>
        <hr className="divider" />
        <button
          type="button"
          className="btn"
          aria-pressed={dimensionScale === 1}
          onClick={() => setDimensionScale((v) => (v === 1 ? 6 : 1))}
        >
          {dimensionScale === 1 ? "Show pipes enlarged ×6" : "Show true pipe sizes"}
        </button>
        <p className="muted x-small">
          {dimensionScale === 1
            ? "True physical dimensions"
            : "Pipe and duct bodies enlarged ×6; labels remain true size"}
        </p>
        <label className="v3d-row">
          <input type="checkbox" checked={showShell} onChange={() => setShowShell((v) => !v)} />
          <span>Show slab & walls</span>
        </label>
      </div>

      {selectedSegment && (
        <div
          className="card"
          role="status"
          style={{
            position: "absolute",
            left: 12,
            bottom: 12,
            maxWidth: 280,
            padding: "10px 12px",
            background: "rgba(255,255,255,0.94)",
            backdropFilter: "blur(6px)",
          }}
        >
          <div className="row spread">
            <strong>{selectedSegment.system.toUpperCase()} · {selectedSegment.id}</strong>
            <button type="button" className="link" onClick={() => setSelectedSegment(null)} aria-label="Close size details">×</button>
          </div>
          <p className="muted small" style={{ margin: "5px 0 0" }}>
            {selectedSegment.kind === "duct"
              ? `Duct ${selectedSegment.width_mm ?? "—"} × ${selectedSegment.height_mm ?? "—"} mm`
              : selectedSegment.diameter_mm
                ? `${selectedSegment.kind === "cable" ? "Conduit" : "Pipe"} Ø${selectedSegment.diameter_mm} mm`
                : "No calculated section size"}
            {selectedSegment.material ? ` · ${selectedSegment.material}` : ""}
          </p>
          <p className="muted x-small" style={{ margin: "3px 0 0" }}>
            {Number(selectedSegment.meta.length_m ?? 0).toFixed(2)} m segment
            {selectedSegment.slope_pct > 0 ? ` · slope ${selectedSegment.slope_pct}%` : ""}
            {selectedSegment.meta.cable_csa_mm2 ? ` · ${selectedSegment.meta.cable_csa_mm2} mm² cable` : ""}
          </p>
        </div>
      )}

      <div className="v3d-clash card">
        <p className="eyebrow">Coordinator · clash check</p>
        <p className="v3d-clash-num" style={{ color: clash && clash.now > 0 ? CLASH_COLOR : "#2d8659" }}>
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

/**
 * Orbit controls bound to the REAL canvas element.
 *
 * drei's `OrbitControls` picks its element as `events.connected || gl.domElement`.
 * Under R3F 8, `events.connected` is R3F's own internal event container — a
 * sibling div of the canvas, not the canvas and not an ancestor of it. Wheel
 * and drag events land on the canvas and bubble to `.viewer3d`; they never
 * reach that sibling, so the controls never see them and the viewer appears
 * frozen. Passing `domElement={gl.domElement}` binds the controls to the
 * element the user is actually pointing at.
 *
 * The buttons in the legend card drive the same camera through `cmdRef`, so
 * the view is reachable even where a wheel gesture is not available.
 */
function CameraControls({
  target,
  minDistance,
  maxDistance,
  cmdRef,
}: {
  target: [number, number, number];
  minDistance: number;
  maxDistance: number;
  cmdRef: React.MutableRefObject<((cmd: "in" | "out" | "reset") => void) | null>;
}) {
  const gl = useThree((s) => s.gl);
  const camera = useThree((s) => s.camera);
  const controls = useThree((s) => s.controls);
  const home = useMemo<[number, number, number]>(
    () => [camera.position.x, camera.position.y, camera.position.z],
    // Captured once on mount: this is the framing the viewer opens with.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  useMemo(() => {
    if (!controls) return;
    cmdRef.current = (cmd) => {
      const c = controls as unknown as {
        target: THREE.Vector3;
        object: THREE.PerspectiveCamera;
        update: () => void;
        minDistance: number;
        maxDistance: number;
      };
      const t = c.target;
      const offset = c.object.position.clone().sub(t);
      const dist = offset.length();
      let next = dist;
      if (cmd === "in") next = dist * 0.8;
      else if (cmd === "out") next = dist * 1.25;
      else next = new THREE.Vector3(...home).distanceTo(t);
      const clamped = Math.min(c.maxDistance, Math.max(c.minDistance, next));
      offset.setLength(clamped);
      c.object.position.copy(t).add(offset);
      c.object.updateProjectionMatrix();
      c.update();
    };
  }, [controls, cmdRef, home]);

  return (
    <OrbitControls
      makeDefault
      domElement={gl.domElement}
      target={target}
      enablePan
      enableZoom
      zoomSpeed={1.2}
      minDistance={minDistance}
      maxDistance={maxDistance}
      maxPolarAngle={Math.PI / 2.05}
    />
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
            <meshBasicMaterial color="#faf6ef" transparent opacity={0.7} side={THREE.DoubleSide} />
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
              color="#f0ebe2"
              transparent
              opacity={w.is_exterior ? 0.5 : 0.35}
              roughness={0.9}
              depthWrite={false}
            />
          </mesh>
        );
      })}
    </group>
  );
}

function SystemLines({
  layer,
  color,
  dimensionScale,
  onSelect,
}: {
  layer: Layer;
  color: string;
  dimensionScale: number;
  onSelect: (segment: Segment) => void;
}) {
  const { lineGeometry, roundRuns, ductRuns } = useMemo(() => {
    const positions: number[] = [];
    const round: Array<{
      key: string;
      segment: Segment;
      midpoint: THREE.Vector3;
      quaternion: THREE.Quaternion;
      length: number;
      diameterM: number;
    }> = [];
    const ducts: Array<{
      key: string;
      segment: Segment;
      midpoint: THREE.Vector3;
      quaternion: THREE.Quaternion;
      length: number;
    }> = [];

    for (const seg of layer.segments) {
      const pts: Point3D[] = seg.polyline;
      const diameterM = Number(seg.diameter_mm ?? 0) / 1000;
      const widthM = Number(seg.width_mm ?? 0) / 1000;
      const heightM = Number(seg.height_mm ?? 0) / 1000;
      const isDuct = seg.kind === "duct" && widthM > 0 && heightM > 0;
      const isRound = !isDuct && diameterM > 0 &&
        ["pipe", "sprinkler_pipe", "refrigerant", "conduit", "cable"].includes(seg.kind);

      for (let i = 0; i < pts.length - 1; i++) {
        // Same mapping as the shell: plan (x, y, z) -> three (x, z, -y).
        const [ax, ay, az] = toThree(pts[i].x, pts[i].y, pts[i].z);
        const [bx, by, bz] = toThree(pts[i + 1].x, pts[i + 1].y, pts[i + 1].z);
        const start = new THREE.Vector3(ax, ay, az);
        const end = new THREE.Vector3(bx, by, bz);
        const direction = end.clone().sub(start);
        const length = direction.length();
        if (length < 1e-6) continue;

        if (isDuct) {
          ducts.push({
            key: `${seg.id}:${i}`,
            segment: seg,
            midpoint: start.clone().add(end).multiplyScalar(0.5),
            quaternion: new THREE.Quaternion().setFromUnitVectors(
              new THREE.Vector3(0, 1, 0), direction.normalize(),
            ),
            length,
          });
        } else if (isRound) {
          round.push({
            key: `${seg.id}:${i}`,
            segment: seg,
            midpoint: start.clone().add(end).multiplyScalar(0.5),
            quaternion: new THREE.Quaternion().setFromUnitVectors(
              new THREE.Vector3(0, 1, 0), direction.normalize(),
            ),
            length,
            diameterM,
          });
        } else {
          positions.push(ax, ay, az, bx, by, bz);
        }
      }
    }

    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    return { lineGeometry: g, roundRuns: round, ductRuns: ducts };
  }, [layer]);

  const dotGeo = useMemo(() => new THREE.SphereGeometry(0.035, 8, 8), []);

  return (
    <group>
      {roundRuns.map((run) => (
        <mesh
          key={run.key}
          position={run.midpoint}
          quaternion={run.quaternion}
          onClick={(event) => {
            event.stopPropagation();
            onSelect(run.segment);
          }}
        >
          <cylinderGeometry args={[
            (run.diameterM * dimensionScale) / 2,
            (run.diameterM * dimensionScale) / 2,
            run.length,
            10,
          ]} />
          <meshStandardMaterial color={color} roughness={0.65} metalness={0.05} />
        </mesh>
      ))}
      {ductRuns.map((run) => (
        <mesh
          key={run.key}
          position={run.midpoint}
          quaternion={run.quaternion}
          onClick={(event) => {
            event.stopPropagation();
            onSelect(run.segment);
          }}
        >
          <boxGeometry args={[
            (Number(run.segment.width_mm) / 1000) * dimensionScale,
            run.length,
            (Number(run.segment.height_mm) / 1000) * dimensionScale,
          ]} />
          <meshStandardMaterial color={color} roughness={0.65} metalness={0.05} />
        </mesh>
      ))}
      {lineGeometry.getAttribute("position").count > 0 && (
        <lineSegments geometry={lineGeometry}>
          <lineBasicMaterial color={color} linewidth={2} />
        </lineSegments>
      )}
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
