import { useEffect, useState, type ChangeEvent } from "react";
import { fetchInteriorScene } from "./api";
import { session } from "../lib/session";
import type { InteriorScene, ViewMode } from "./types";
import Viewer3D from "./Viewer3D";
import "./interior.css";

interface LoadState {
  loading: boolean;
  error: string | null;
  scene: InteriorScene | null;
}

const MODES: ReadonlyArray<{ label: string; value: ViewMode }> = [
  { label: "3D model", value: "orbit" },
  { label: "Walk-through", value: "walk" },
];

export default function Interior() {
  const [load, setLoad] = useState<LoadState>({
    loading: true,
    error: null,
    scene: null,
  });
  // Bumping this key re-runs the load effect (retry). Cleanup cancels the
  // previous in-flight request so a late response can never clobber new state.
  const [reloadKey, setReloadKey] = useState(0);
  const [mode, setMode] = useState<ViewMode>("orbit");
  const [roomId, setRoomId] = useState<string | null>(null);
  const [buildValue, setBuildValue] = useState<number>(100);

  useEffect(() => {
    let cancelled = false;
    setLoad({ loading: true, error: null, scene: null });
    // Use the active uploaded/sampled model if one exists in session;
    // otherwise fall back to the bundled sample fixture.
    const activeModelId = session.getActiveModelId();
    fetchInteriorScene(activeModelId ?? undefined)
      .then((scene) => {
        if (cancelled) return;
        setLoad({ loading: false, error: null, scene });
        // Preselect the first room once a scene is available; in walk mode
        // this room drives the camera inside Viewer3D.
        setRoomId(scene.rooms.length > 0 ? scene.rooms[0].id : null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoad({
          loading: false,
          error:
            err instanceof Error
              ? err.message
              : "Failed to load the interior scene.",
          scene: null,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const retry = () => setReloadKey((key) => key + 1);

  const onSliderChange = (e: ChangeEvent<HTMLInputElement>) => {
    setBuildValue(Number(e.target.value));
  };

  const buildProgress = buildValue / 100;
  const scene = load.scene;

  return (
    <div className="int-page">
      <header className="int-head">
        <div className="int-head-row">
          <div>
            <h1 className="int-title">Interior 3D walkthrough</h1>
            <p className="int-subtitle">
              A deterministic 3D model and walk-through generated from the plan
              geometry — no AI rendering.
            </p>
          </div>
          <a href="/app" className="int-back">
            &larr; Back to MEP studio
          </a>
        </div>
      </header>

      {load.loading && (
        <div className="int-card int-status" role="status">
          <span className="int-spinner" aria-hidden="true" />
          <p className="int-status-title">Building interior scene&hellip;</p>
          <p className="int-muted">
            Loading walls, rooms and openings from the plan.
          </p>
        </div>
      )}

      {!load.loading && load.error && (
        <div className="int-card int-status int-error" role="alert">
          <p className="int-status-title">Couldn&apos;t load the interior scene</p>
          <p className="int-muted">{load.error}</p>
          <button type="button" className="int-retry" onClick={retry}>
            Retry
          </button>
        </div>
      )}

      {/* Defensive empty state: never leave the user at a dead end. */}
      {!load.loading && !load.error && !scene && (
        <div className="int-card int-status" role="status">
          <p className="int-status-title">No scene available</p>
          <p className="int-muted">Nothing was returned for this model.</p>
          <button type="button" className="int-retry" onClick={retry}>
            Retry
          </button>
        </div>
      )}

      {scene && (
        <main className="int-content">
          <div className="int-disclaimer" role="note">
            <span className="int-disclaimer-mark" aria-hidden="true">
              !
            </span>
            <span>{scene.disclaimer}</span>
          </div>

          <div className="int-toolbar">
            <div className="int-toolgroup">
              <span className="int-label">View</span>
              <div className="int-segmented" role="group" aria-label="View mode">
                {MODES.map((m) => (
                  <button
                    key={m.value}
                    type="button"
                    className={
                      "int-seg" + (mode === m.value ? " int-seg-active" : "")
                    }
                    aria-pressed={mode === m.value}
                    onClick={() => setMode(m.value)}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="int-toolgroup int-rooms">
              <span className="int-label">Room</span>
              <div className="int-tabs" role="group" aria-label="Select room">
                {scene.rooms.length === 0 && (
                  <span className="int-muted small">No rooms</span>
                )}
                {scene.rooms.map((room) => (
                  <button
                    key={room.id}
                    type="button"
                    className={
                      "int-tab" + (roomId === room.id ? " int-tab-active" : "")
                    }
                    aria-pressed={roomId === room.id}
                    onClick={() => setRoomId(room.id)}
                  >
                    {room.name}
                  </button>
                ))}
              </div>
            </div>

            <div className="int-toolgroup int-build">
              <label className="int-label" htmlFor="int-build-slider">
                Build &middot; {buildValue}%
              </label>
              <input
                id="int-build-slider"
                type="range"
                min={0}
                max={100}
                value={buildValue}
                onChange={onSliderChange}
                className="int-slider"
              />
            </div>
          </div>

          <div className="int-viewer">
            <Viewer3D
              scene={scene}
              mode={mode}
              roomId={roomId}
              buildProgress={buildProgress}
            />
          </div>

          <div className="int-stats">
            <span className="int-stat">
              <strong>{scene.rooms.length}</strong> rooms
            </span>
            <span className="int-stat">
              <strong>{scene.walls.length}</strong> walls
            </span>
            <span className="int-stat">
              <strong>{scene.openings.length}</strong> openings
            </span>
            <span className="int-stat int-stat-id mono">
              {scene.name} &middot; {scene.model_id}
            </span>
          </div>
          {session.getActiveModelId() && (
            <p className="int-muted small" style={{ marginTop: 4 }}>
              Showing your uploaded plan.{" "}
              <a href="/app" className="int-back">&larr; Back to MEP studio</a>
            </p>
          )}
        </main>
      )}
    </div>
  );
}
