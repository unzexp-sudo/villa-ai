"""Five specialist agents (MODULE_CONTRACT.md §4, plan §5-6).

Each agent is an LLM *planner* sitting on a DETERMINISTIC geometry toolkit:
the planner chooses strategy + parameters, M2 generates and scores candidate
layouts, and the agent commits the best one to a Layer while emitting real
SSE events (never invented metrics).

``ctx`` (M4's RunContext) is duck-typed via ``getattr`` with safe defaults --
M4 is NEVER imported at module scope. The M2 geometry functions are imported
LAZILY inside :meth:`plan` with a clear ``RuntimeError`` if M2 is temporarily
absent, so this module imports fine before the whole tree lands.
"""

from __future__ import annotations

from typing import Any

from app.contract import (
    AgentEvent,
    AgentPhase,
    Layer,
    SystemKind,
)
from app.planner import get_planner


def _load_geometry():
    """Lazy M2 import with a clear RuntimeError (siblings may be mid-flight).

    Returns ``(build_candidates, score_layer, segment_length_m, count_bends)``.
    """
    try:
        from app.geometry import (
            build_candidates,
            count_bends,
            score_layer,
            segment_length_m,
        )
    except ImportError as exc:
        raise RuntimeError(
            "M3 agent: M2 geometry toolkit is unavailable while the tree is "
            f"being built ({exc})"
        ) from exc
    return build_candidates, score_layer, segment_length_m, count_bends


class _FallbackLimits:
    max_candidates_per_agent = 16


def _default_limits():
    try:
        from app.core.config import get_limits
        return get_limits()
    except Exception:
        return _FallbackLimits()


class SpecialistAgent:
    """Base specialist. Subclasses set ``system`` / ``name`` / ``code_ref`` and
    override ``_agent_notes`` / ``_accept_candidate`` / ``is_enabled``.
    """

    system: SystemKind = SystemKind.DRAINAGE
    name: str = "specialist"
    code_ref: str = ""              # ACTIVE rule-pack reference (UI header)
    code_ref_verified: bool = False  # frozen: never claim compliance

    # ---- extension hooks -------------------------------------------------

    def is_enabled(self, model: Any, ctx: Any = None) -> bool:
        return True

    def _accept_candidate(self, layer: Layer, strategy: Any, model: Any) -> bool:
        """System-specific candidate filter (a REAL constraint check)."""
        return True

    def _agent_notes(
        self, stats: dict, strategy: Any, model: Any, country: Any
    ) -> list[str]:
        """System-specific summary + constraint + SE-Asia notes, in METRIC
        units, built from real numbers."""
        return [
            f"{int(stats['n_segments'])} segments · "
            f"{stats['total_length_m']:.1f} m · {stats['bends']:.0f} bends"
        ]

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _mx(stats: dict, key: str, default: float = 0.0) -> float:
        e = stats.get("meta", {}).get(key)
        return e["max"] if e else default

    @staticmethod
    def _sm(stats: dict, key: str, default: float = 0.0) -> float:
        e = stats.get("meta", {}).get(key)
        return e["sum"] if e else default

    def _event(
        self,
        run_id: str,
        name: str,
        phase: AgentPhase,
        message: str,
        layouts_tested: int = 0,
        best_score: float | None = None,
        metrics: dict | None = None,
        layer: Layer | None = None,
    ) -> AgentEvent:
        # seq=0 / ts=0: the EventBus assigns the real values.
        return AgentEvent(
            run_id=run_id,
            seq=0,
            ts=0,
            name=name,
            agent=self.name,
            phase=phase,
            layouts_tested=layouts_tested,
            best_score=best_score,
            message=message,
            metrics=dict(metrics or {}),
            layer=layer,
        )

    def _observations(self, model: Any, country: Any) -> dict[str, Any]:
        lvl = model.level0
        minx, miny, maxx, maxy = model.bbox()
        return {
            "system": self.system.value,
            "country": getattr(country, "value", str(country or "ID")),
            "building_type": getattr(
                model.building_type, "value", str(model.building_type)
            ),
            "wet_room_count": len(model.wet_rooms()),
            "fixture_count": sum(len(l.fixtures) for l in model.levels),
            "equipment_kinds": sorted(
                {
                    getattr(e.kind, "value", str(e.kind))
                    for l in model.levels
                    for e in l.equipment
                }
            ),
            "shaft_count": sum(len(l.shafts) for l in model.levels),
            "room_count": len(lvl.rooms),
            "bbox": [minx, miny, maxx, maxy],
        }

    def _stats(self, layer: Layer, segment_length_m, count_bends) -> dict:
        """Aggregate REAL numbers from the committed layer + its segment meta
        (the honest sizing values M2 computed)."""
        segs = layer.segments
        mats = [s.material for s in segs if s.material]
        slopes = [float(s.slope_pct) for s in segs if s.slope_pct]
        dias = [float(s.diameter_mm) for s in segs if s.diameter_mm]
        trunk = [s for s in segs if s.meta.get("role") == "trunk"]
        agg: dict[str, dict[str, float]] = {}
        for s in segs:
            for k, v in s.meta.items():
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                a = agg.setdefault(k, {"max": float(v), "sum": 0.0, "n": 0.0})
                a["max"] = max(a["max"], float(v))
                a["sum"] += float(v)
                a["n"] += 1.0
        for a in agg.values():
            a["mean"] = a["sum"] / a["n"] if a["n"] else 0.0
        return {
            "n_segments": float(len(segs)),
            "n_devices": float(len(layer.devices)),
            "n_trunk": float(len(trunk)),
            "total_length_m": sum(segment_length_m(s.polyline) for s in segs),
            "bends": float(sum(count_bends(s.polyline) for s in segs)),
            "material": max(set(mats), key=mats.count) if mats else "n/a",
            "mean_slope_pct": (sum(slopes) / len(slopes)) if slopes else 0.0,
            "max_diameter_mm": max(dias) if dias else 0.0,
            "meta": agg,
        }

    # ---- main ------------------------------------------------------------

    def plan(self, model: Any, ctx: Any, emitter: Any) -> Layer:
        run_id = "run"
        try:
            run_id = str(getattr(ctx, "run_id", "run") or "run")
            seed = int(getattr(ctx, "seed", 42) or 42)
            country = getattr(ctx, "country", None)
            limits = getattr(ctx, "limits", None)
            if limits is None:
                limits = _default_limits()
            max_candidates = max(
                1, int(getattr(limits, "max_candidates_per_agent", 16) or 16)
            )

            work = model.clone()  # §7: never mutate the caller's model
            (
                build_candidates,
                score_layer,
                segment_length_m,
                count_bends,
            ) = _load_geometry()

            planner = get_planner()
            observations = self._observations(work, country)
            strategy = planner.choose_strategy(
                self.system,
                work,
                observations,
                run_id=getattr(ctx, "run_id", None),
            )
            if strategy is None:  # defensive: no strategy, no run
                raise RuntimeError("planner returned no strategy")

            emitter.emit(
                self._event(
                    run_id,
                    "agent.status",
                    AgentPhase.EXPLORING,
                    f"{self.name}: exploring {strategy.trunk}/{strategy.stack_location} strategy",
                )
            )
            emitter.emit(
                self._event(
                    run_id,
                    "agent.status",
                    AgentPhase.TESTING,
                    f"{self.name}: testing candidates (cap {max_candidates})",
                )
            )

            layouts_tested = 0
            running_best = 0.0
            scored: list[tuple[float, Layer]] = []
            for cand in build_candidates(self.system, work, strategy, limits, seed):
                if layouts_tested >= max_candidates:
                    break
                layouts_tested += 1
                score = float(score_layer(cand, work, strategy))
                running_best = max(running_best, score)
                scored.append((score, cand))

                real = {
                    "layouts_tested": float(layouts_tested),
                    "best_score": float(running_best),
                }
                emitter.emit(
                    self._event(
                        run_id,
                        "agent.progress",
                        AgentPhase.TESTING,
                        f"{self.name}: {layouts_tested} layouts · best {running_best:.3f}",
                        layouts_tested=layouts_tested,
                        best_score=running_best,
                        metrics=real,
                    )
                )
                emitter.emit(
                    self._event(
                        run_id,
                        "candidate.flicker",
                        AgentPhase.TESTING,
                        f"{self.name}: candidate {layouts_tested} scored {score:.3f}",
                        layouts_tested=layouts_tested,
                        best_score=running_best,
                        metrics=real,
                    )
                )

            if not scored:
                raise RuntimeError("toolkit produced zero candidates")

            accepted = [
                (s, l)
                for (s, l) in scored
                if self._accept_candidate(l, strategy, work)
            ]
            pool = accepted or scored  # never dead-end on an over-strict filter
            best_score, best = max(pool, key=lambda t: t[0])
            best_score = float(best_score)

            emitter.emit(
                self._event(
                    run_id,
                    "agent.status",
                    AgentPhase.COMMITTING,
                    f"{self.name}: committing best layout (score {best_score:.3f})",
                    layouts_tested=layouts_tested,
                    best_score=best_score,
                )
            )

            stats = self._stats(best, segment_length_m, count_bends)
            metrics: dict[str, float] = {}
            for k, v in best.metrics.items():
                try:
                    metrics[k] = float(v)
                except (TypeError, ValueError):
                    pass
            metrics.update(
                {
                    "layouts_tested": float(layouts_tested),
                    "best_score": best_score,
                    "total_length_m": round(stats["total_length_m"], 4),
                    "bends": float(stats["bends"]),
                    "devices": float(stats["n_devices"]),
                }
            )
            notes = [
                f"Preliminary design · {self.code_ref} (unverified reference) · "
                "licensed engineer review required"
            ]
            notes.extend(self._agent_notes(stats, strategy, work, country))

            best.agent = self.name
            best.status = "done"
            best.strategy = strategy.model_dump(mode="json")
            best.metrics = metrics
            best.notes = list(notes)

            emitter.emit(
                self._event(
                    run_id,
                    "agent.status",
                    AgentPhase.DONE,
                    f"{self.name}: done · {layouts_tested} layouts · best {best_score:.3f}",
                    layouts_tested=layouts_tested,
                    best_score=best_score,
                    metrics=metrics,
                )
            )
            # layer.committed is the LAST emitted (non-feed) event.
            emitter.emit(
                self._event(
                    run_id,
                    "layer.committed",
                    AgentPhase.DONE,
                    f"{self.name}: layer committed",
                    layouts_tested=layouts_tested,
                    best_score=best_score,
                    metrics=metrics,
                    layer=best,
                )
            )

            feed_line = (
                f"{self.name}: {layouts_tested} layouts · "
                f"{stats['total_length_m']:.1f} m · best {best_score:.3f}"
            )
            emitter.feed(run_id, self.name, feed_line)
            return best

        except Exception as exc:
            # Never swallow silently: emit FAILED + return a failed Layer.
            try:
                emitter.emit(
                    self._event(
                        run_id,
                        "agent.status",
                        AgentPhase.FAILED,
                        f"{self.name}: failed · {exc}",
                    )
                )
            except Exception:
                pass
            return Layer(
                system=self.system,
                agent=self.name,
                status="failed",
                notes=[
                    f"{self.code_ref} (unverified reference)",
                    f"error: {exc}",
                ],
            )


__all__ = ["SpecialistAgent"]
