"""Layer merge (M4 ORCHESTRATOR).

Five agents each commit one ``Layer``. ``merge_layers`` folds them into the
single ``dict[SystemKind, Layer]`` the frozen ``RunResult.layers`` expects,
preserving enum keys. ``all_elements`` is the generator the clash detector
walks over; it yields ``(system, element)`` for every ``Segment`` and
``Device`` so no caller has to know how a Layer stores its geometry.
"""

from __future__ import annotations

from typing import Iterable, Iterator

from app.contract import Device, Layer, Segment, SystemKind

Element = Segment | Device


def _as_key(system) -> SystemKind:
    return system if isinstance(system, SystemKind) else SystemKind(system)


def merge_layers(layers: Iterable[Layer]) -> dict[SystemKind, Layer]:
    """Merge the collected agent Layers into one enum-keyed mapping.

    Keys are ``SystemKind`` members (JSON serialises them to their values).
    When two Layers claim the same system the last one wins, which is
    deterministic given the collected order.
    """
    out: dict[SystemKind, Layer] = {}
    for layer in layers:
        out[_as_key(layer.system)] = layer
    return out


def _iter_layers(layers) -> Iterator[tuple[SystemKind, Layer]]:
    """Accept either a ``dict[SystemKind, Layer]`` or an iterable of Layers."""
    if isinstance(layers, dict):
        for system, layer in layers.items():
            yield _as_key(system), layer
    else:
        for layer in layers:
            yield _as_key(layer.system), layer


def all_elements(layers) -> Iterator[tuple[SystemKind, Element]]:
    """Yield ``(system, element)`` for every Segment and Device.

    The ``system`` is taken from the mapping key / Layer, not the element's
    own ``.system`` field, so a mislabelled element can never hide a clash.
    """
    for system, layer in _iter_layers(layers):
        for seg in layer.segments:
            yield system, seg
        for dev in layer.devices:
            yield system, dev


def element_count(layers) -> dict[str, int]:
    """Real element/segment/device counts (used for merge.done metrics)."""
    segments = devices = 0
    for _system, el in all_elements(layers):
        if isinstance(el, Segment):
            segments += 1
        else:
            devices += 1
    return {"segments": segments, "devices": devices,
            "elements": segments + devices}


__all__ = ["merge_layers", "all_elements", "element_count"]
