import { useEffect, useMemo, useRef } from "react";
import type { MutableRefObject } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import { EYE_HEIGHT_M } from "./types";
import type {
  InteriorScene,
  Pt3,
  SceneOpening,
  SceneRoom,
  SceneWall,
  ViewMode,
  WallSegment,
} from "./types";

type Props = {
  scene: InteriorScene;
  mode: "orbit" | "walk";
  roomId: string | null;
  buildProgress: number;
};

/** FROZEN axis mapping (contract §6.1). Scene +z is height -> three.js y;
 *  a plan point (x, y) becomes [x, 0, -y]. Determinant +1, handedness preserved. */
const toThree = (x: number, y: number, z: number): [number, number, number] => [x, z, -y];

const clamp = (v: number, lo: number, hi: number): number =>
  lo <= hi ? Math.min(Math.max(v, lo), hi) : (lo + hi) / 2;

const UP = new THREE.Vector3(0, 1, 0);
const MOVE_KEYS = new Set([
  "w",
  "a",
  "s",
  "d",
  "arrowup",
  "arrowdown",
  "arrowleft",
  "arrowright",
]);

const FLOOR_COLORS = ["#d4b896", "#c9ad8a", "#d8be9c", "#ceb294", "#d2b690", "#c4a888"];
const EXTERIOR = "#e8ddd0";
const INTERIOR = "#f5efe5";
const EXTERIOR_EDGE = "#c4b8a6";
const INTERIOR_EDGE = "#d4c9b8";
const ACCENT = "#2d8659"; // --accent
const BG = "#a8c8d8"; // sky blue
const BORDER = "#e2d9cb"; // --border

const OPENING_TINT: Record<SceneOpening["kind"], string> = {
  door: "#2d8659", // --accent
  window: "#5ba8d8", // warm sky blue
  passage: "#8a7f6f", // --muted
};

interface CameraSetup {
  position: [number, number, number];
  target: [number, number, number];
}

function eyeFor(scene: InteriorScene, roomId: string | null): Pt3 {
  const selected = roomId ? scene.rooms.find((r) => r.id === roomId) : undefined;
  const room = selected ?? scene.rooms[0];
  if (room) return room.eye;
  const [minX, minY, maxX, maxY] = scene.bbox;
  return { x: (minX + maxX) / 2, y: (minY + maxY) / 2, z: EYE_HEIGHT_M };
}

function cameraSetup(scene: InteriorScene, mode: ViewMode, roomId: string | null): CameraSetup {
  const [minX, minY, maxX, maxY] = scene.bbox;
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  const spanX = Math.max(maxX - minX, 1);
  const spanY = Math.max(maxY - minY, 1);

  if (mode === "orbit") {
    // Exterior diagonal derived from the bbox, looking at its centre.
    return {
      // ~0.65x the bbox span: at fov 60 this fills the viewer instead of
      // leaving the model as a small island in the middle.
      position: toThree(cx + spanX * 0.65, cy - spanX * 0.8, spanY * 0.75),
      target: toThree(cx, cy, 0),
    };
  }

  // Walk: eye of the selected room, looking toward the NEAREST OPENING so the
  // first-person view shows into the plan through a real door/window gap rather
  // than straight into the closest partition. Falls back to the bbox centre.
  const eye = eyeFor(scene, roomId);
  let nearest: SceneOpening | null = null;
  let nearestDist = Infinity;
  for (const op of scene.openings) {
    const d = Math.hypot(op.center.x - eye.x, op.center.y - eye.y);
    if (d < nearestDist) {
      nearestDist = d;
      nearest = op;
    }
  }
  const dx = (nearest ? nearest.center.x : cx) - eye.x;
  const dy = (nearest ? nearest.center.y : cy) - eye.y;
  const dist = Math.hypot(dx, dy);
  const ux = dist > 1e-6 ? dx / dist : 0;
  const uy = dist > 1e-6 ? dy / dist : 1;
  return {
    position: toThree(eye.x, eye.y, eye.z),
    target: toThree(eye.x + ux * 2, eye.y + uy * 2, eye.z),
  };
}

/** A box plus its outline, rebuilt when dimensions change and disposed on cleanup. */
function useBoxWithEdges(w: number, h: number, d: number) {
  const geometries = useMemo(() => {
    const box = new THREE.BoxGeometry(
      Math.max(w, 1e-3),
      Math.max(h, 1e-3),
      Math.max(d, 1e-3),
    );
    const edges = new THREE.EdgesGeometry(box);
    return { box, edges };
  }, [w, h, d]);

  useEffect(
    () => () => {
      geometries.box.dispose();
      geometries.edges.dispose();
    },
    [geometries],
  );

  return geometries;
}

function Floor({ room, index }: { room: SceneRoom; index: number }) {
  const { shapeGeom, outlineGeom } = useMemo(() => {
    // Build the shape in (x, -y); rotating +PI/2 about X puts it flat so that a
    // shape point (x, -y) lands at toThree(x, y, 0) === [x, 0, -y].
    const shape = new THREE.Shape();
    room.polygon.forEach((p, i) => {
      if (i === 0) shape.moveTo(p.x, -p.y);
      else shape.lineTo(p.x, -p.y);
    });
    shape.closePath();

    const outline = new THREE.BufferGeometry().setFromPoints(
      room.polygon.map((p) => {
        const v = toThree(p.x, p.y, 0);
        return new THREE.Vector3(v[0], 0.012, v[2]);
      }),
    );
    return { shapeGeom: new THREE.ShapeGeometry(shape), outlineGeom: outline };
  }, [room]);

  useEffect(
    () => () => {
      shapeGeom.dispose();
      outlineGeom.dispose();
    },
    [shapeGeom, outlineGeom],
  );

  return (
    <group>
      <mesh geometry={shapeGeom} rotation-x={Math.PI / 2}>
        <meshStandardMaterial
          color={FLOOR_COLORS[index % FLOOR_COLORS.length]}
          roughness={0.95}
          metalness={0.02}
          side={THREE.DoubleSide}
        />
      </mesh>
      <lineLoop geometry={outlineGeom}>
        <lineBasicMaterial color={ACCENT} transparent opacity={0.28} depthWrite={false} />
      </lineLoop>
    </group>
  );
}

function WallSegmentMesh({
  wall,
  seg,
  p,
}: {
  wall: SceneWall;
  seg: WallSegment;
  p: number;
}) {
  const len = Math.max(Math.hypot(seg.p2.x - seg.p1.x, seg.p2.y - seg.p1.y), 1e-3);
  // Vertical extent scales with buildProgress only; floors stay at y = 0.
  // Guard a minimum height so p === 0 never yields degenerate/NaN geometry.
  const height = Math.max((seg.z1 - seg.z0) * p, 0.001);
  const thickness = Math.max(wall.thickness, 1e-3);
  const midX = (seg.p1.x + seg.p2.x) / 2;
  const midY = (seg.p1.y + seg.p2.y) / 2;
  const centreZ = ((seg.z0 + seg.z1) / 2) * p;
  const [x, y, z] = toThree(midX, midY, centreZ);
  const { box, edges } = useBoxWithEdges(len, height, thickness);
  const color = wall.is_exterior ? EXTERIOR : INTERIOR;
  const edge = wall.is_exterior ? EXTERIOR_EDGE : INTERIOR_EDGE;

  // ONE mesh per segment: the segments already carry the real opening gaps,
  // so we never draw a full-height wall across an opening.
  return (
    <group position={[x, y, z]} rotation-y={wall.angle_rad}>
      <mesh geometry={box}>
        <meshStandardMaterial
          color={color}
          roughness={0.92}
          metalness={0.02}
        />
      </mesh>
      <lineSegments geometry={edges}>
        <lineBasicMaterial color={edge} transparent opacity={0.6} />
      </lineSegments>
    </group>
  );
}

function OpeningMarker({ opening, p }: { opening: SceneOpening; p: number }) {
  const w = Math.max(opening.width, 1e-3);
  // Markers must rise with the walls: same vertical scaling as WallSegmentMesh,
  // otherwise they float above shortened walls at buildProgress < 1.
  const h = Math.max((opening.head - opening.sill) * p, 1e-3);
  const [x, y, z] = toThree(opening.center.x, opening.center.y, opening.center.z * p);
  const { box, edges } = useBoxWithEdges(w, h, 0.03);
  const tint = OPENING_TINT[opening.kind];

  // MARKER only: translucent panel + thin frame. The physical gap already
  // exists in the wall segments; this must not fill the opening solidly.
  return (
    <group position={[x, y, z]} rotation-y={opening.angle_rad}>
      <mesh geometry={box}>
        <meshBasicMaterial
          color={tint}
          transparent
          opacity={0.12}
          depthWrite={false}
          side={THREE.DoubleSide}
        />
      </mesh>
      <lineSegments geometry={edges}>
        <lineBasicMaterial color={tint} transparent opacity={0.45} depthWrite={false} />
      </lineSegments>
    </group>
  );
}

/** WASD / arrow keys translate BOTH camera.position and controls.target by the
 *  same horizontal delta, at fixed eye height, clamped inside bbox (0.4 m inset). */
function WalkRig({
  controlsRef,
  scene,
}: {
  controlsRef: MutableRefObject<any>;
  scene: InteriorScene;
}) {
  const { camera } = useThree();
  const keys = useRef<Set<string>>(new Set());

  useEffect(() => {
    const isFormTarget = (t: EventTarget | null): boolean => {
      const el = t as HTMLElement | null;
      return (
        !!el &&
        (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT")
      );
    };
    const onDown = (e: KeyboardEvent) => {
      if (isFormTarget(e.target)) return;
      const k = e.key.toLowerCase();
      if (MOVE_KEYS.has(k)) {
        keys.current.add(k);
        if (k.startsWith("arrow")) e.preventDefault();
      }
    };
    const onUp = (e: KeyboardEvent) => keys.current.delete(e.key.toLowerCase());
    const onBlur = () => keys.current.clear();
    window.addEventListener("keydown", onDown);
    window.addEventListener("keyup", onUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onDown);
      window.removeEventListener("keyup", onUp);
      window.removeEventListener("blur", onBlur);
    };
  }, []);

  const [minX, minY, maxX, maxY] = scene.bbox;
  const inset = 0.4;

  useFrame((_, delta) => {
    const k = keys.current;
    let forward = 0;
    let strafe = 0;
    if (k.has("w") || k.has("arrowup")) forward += 1;
    if (k.has("s") || k.has("arrowdown")) forward -= 1;
    if (k.has("d") || k.has("arrowright")) strafe += 1;
    if (k.has("a") || k.has("arrowleft")) strafe -= 1;
    if (forward === 0 && strafe === 0) return;

    const controls = controlsRef.current;
    const target: THREE.Vector3 | null = controls
      ? (controls.target as THREE.Vector3)
      : null;

    const dir = new THREE.Vector3();
    if (target) dir.subVectors(target, camera.position);
    else camera.getWorldDirection(dir);
    dir.y = 0;
    if (dir.lengthSq() < 1e-8) dir.set(0, 0, -1);
    dir.normalize();
    const right = new THREE.Vector3().crossVectors(dir, UP).normalize();

    const move = new THREE.Vector3()
      .addScaledVector(dir, forward)
      .addScaledVector(right, strafe);
    if (move.lengthSq() > 1) move.normalize();
    move.multiplyScalar(2.6 * Math.min(delta, 0.05));

    const cam = camera.position;
    // plan (x, y) -> three (x, -y); 0.4 m inset on each side.
    const nx = clamp(cam.x + move.x, minX + inset, maxX - inset);
    const nz = clamp(cam.z + move.z, -maxY + inset, -minY - inset);
    const effX = nx - cam.x;
    const effZ = nz - cam.z;
    cam.set(nx, EYE_HEIGHT_M, nz);
    if (target) {
      target.set(target.x + effX, EYE_HEIGHT_M, target.z + effZ);
      controls.update();
    }
  });

  return null;
}

/** A headlamp that follows the camera in walk mode; without it the interior
 *  is too dark to read at eye level. Only mounted while walking. */
function Headlamp() {
  const ref = useRef<THREE.PointLight>(null);
  const { camera } = useThree();
  useFrame(() => {
    if (ref.current) ref.current.position.copy(camera.position);
  });
  return (
    <pointLight
      ref={ref}
      intensity={22}
      distance={14}
      decay={2}
      color="#fff8e8"
    />
  );
}

function SceneRig({
  scene,
  mode,
  roomId,
  p,
}: {
  scene: InteriorScene;
  mode: ViewMode;
  roomId: string | null;
  p: number;
}) {
  // `any` is unavoidable for a drei OrbitControls instance ref.
  const controlsRef = useRef<any>(null);
  const { camera } = useThree();

  // Reset camera + controls target whenever mode / roomId / scene changes.
  useEffect(() => {
    const setup = cameraSetup(scene, mode, roomId);
    camera.position.set(setup.position[0], setup.position[1], setup.position[2]);
    camera.lookAt(setup.target[0], setup.target[1], setup.target[2]);
    const controls = controlsRef.current;
    if (controls) {
      controls.target.set(setup.target[0], setup.target[1], setup.target[2]);
      controls.update();
    }
  }, [mode, roomId, scene, camera]);

  return (
    <>
      <OrbitControls
        ref={controlsRef}
        makeDefault
        enableDamping
        dampingFactor={0.08}
        enablePan={mode !== "walk"}
        minPolarAngle={mode === "walk" ? Math.PI / 2 : 0}
        maxPolarAngle={mode === "walk" ? Math.PI / 2 : Math.PI}
      />
      {mode === "walk" && <WalkRig controlsRef={controlsRef} scene={scene} />}
      {mode === "walk" && <Headlamp />}

      {scene.rooms.map((room, i) => (
        <Floor key={room.id} room={room} index={i} />
      ))}
      {scene.walls.map((wall) =>
        wall.segments.map((seg, i) => (
          <WallSegmentMesh key={`${wall.id}:${i}`} wall={wall} seg={seg} p={p} />
        )),
      )}
      {scene.openings.map((opening) => (
        <OpeningMarker key={opening.id} opening={opening} p={p} />
      ))}
    </>
  );
}

export default function Viewer3D({ scene, mode, roomId, buildProgress }: Props) {
  const p = Number.isFinite(buildProgress) ? clamp(buildProgress, 0, 1) : 0;
  const initial = cameraSetup(scene, mode, roomId);

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        minHeight: 480,
        background: BG,
        border: `1px solid ${BORDER}`,
        borderRadius: 10,
        overflow: "hidden",
      }}
    >
      <Canvas
        dpr={[1, 2]}
        camera={{
          fov: 60,
          near: 0.05,
          far: 2000,
          position: initial.position,
        }}
        style={{ width: "100%", height: "100%" }}
      >
        <color attach="background" args={[BG]} />
        <ambientLight intensity={1.4} />
        <directionalLight position={[8, 18, 6]} intensity={1.8} />
        <directionalLight position={[-6, 12, -8]} intensity={0.6} />
        <SceneRig scene={scene} mode={mode} roomId={roomId} p={p} />
      </Canvas>
    </div>
  );
}
