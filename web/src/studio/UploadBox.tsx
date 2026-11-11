// UploadBox — bring your own floor plan.
//
// The upload path is where a wrong answer is most expensive: a plan the user
// has to correct is worse than no plan. So this box does the honest thing —
// it says which formats work, it shows the actual error from the server when
// a file cannot be read, and it never pretends a parse succeeded.
//
// DXF is exact geometry. PDF is vectorised from its content stream. PNG/JPG
// are edge-detected. DWG needs a conversion service and is refused.

import { useRef, useState } from "react";
import { api } from "../lib/api";
import type { BuildingModel } from "../contract";

interface Props {
  onUploaded: (model: BuildingModel) => void;
}

const ACCEPT = ".dxf,.pdf,.png,.jpg,.jpeg,.webp";

export function UploadBox({ onUploaded }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      // One-step: the server parses the file into a new model and returns it.
      const parsed = await api.uploadCreate(file);
      onUploaded(parsed);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={`upload-box ${dragging ? "dragging" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const file = e.dataTransfer.files?.[0];
        if (file) void handleFile(file);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void handleFile(file);
        }}
      />
      <button
        type="button"
        className="btn"
        disabled={busy}
        onClick={() => inputRef.current?.click()}
      >
        {busy ? "Reading the plan…" : "Choose a file"}
      </button>
      <span className="muted small">or drop it here · DXF, PDF, PNG, JPG</span>
      {error && (
        <p className="lead-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export default UploadBox;
