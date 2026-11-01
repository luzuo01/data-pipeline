"""
Asset Registry - Maps short names to full Unreal Engine asset paths.
Allows prompts to reference simple names like 'bp_table' or 'chair' instead of
typing full `/Game/...` references.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional


class AssetRegistry:
    """Manages asset name to path mappings with lightweight blueprint discovery."""

    def __init__(self, registry_path: Optional[str] = None):
        """
        Initialize the registry and load Config/asset_registry.json (if present).
        Also prepares a fallback scanner that walks Content/Control for BP assets.
        """
        project_root: Optional[Path] = None
        if registry_path is None:
            project_root = Path(__file__).parent.parent.parent
            registry_path = project_root / "Config" / "asset_registry.json"
        else:
            registry_path = Path(registry_path)
            if registry_path.parent.name.lower() == "config":
                project_root = registry_path.parent.parent

        self.registry_path = Path(registry_path)
        self.project_root = project_root or Path(__file__).parent.parent.parent
        self.content_dir = self.project_root / "Content"
        self.control_root_dir = self.content_dir / "Control"

        self.data: Dict[str, Dict] = {}
        self.flat_map: Dict[str, str] = {}
        self._last_mtime: Optional[float] = None

        # Control blueprint cache
        self._control_assets: Dict[str, str] = {}
        self._control_assets_loaded = False
        self._control_assets_mtime = -1.0

        self.load()

    # ------------------------------------------------------------------
    # Loading / flat map helpers
    # ------------------------------------------------------------------
    def load(self) -> None:
        """Load registry JSON and rebuild flat map."""
        try:
            # The checked-in registry includes a UTF-8 BOM on Windows.
            with open(self.registry_path, "r", encoding="utf-8-sig") as f:
                self.data = json.load(f)
            self._build_flat_map()
            self._last_mtime = self._get_file_mtime()
        except FileNotFoundError:
            self._create_default_registry()
        except json.JSONDecodeError:
            self.data = {}
            self.flat_map = {}

    def _build_flat_map(self) -> None:
        self.flat_map = {}
        for assets in self.data.get("categories", {}).values():
            for name, path in assets.items():
                self.flat_map[name.lower()] = path
        for alias, target in self.data.get("aliases", {}).items():
            target_path = self.flat_map.get(target.lower())
            if target_path:
                self.flat_map[alias.lower()] = target_path

    def _get_file_mtime(self) -> float:
        try:
            return os.path.getmtime(self.registry_path)
        except OSError:
            return -1.0

    def _maybe_reload(self) -> None:
        current_mtime = self._get_file_mtime()
        if self._last_mtime is None or current_mtime > self._last_mtime:
            self.load()

    # ------------------------------------------------------------------
    # Control blueprint discovery
    # ------------------------------------------------------------------
    def _ensure_control_asset_cache(self) -> None:
        """Scan Content/Control (and sub-folders) for BP_* assets."""
        scan_dir = self.control_root_dir
        if not scan_dir.exists():
            scan_dir = self.project_root / "Content" / "Control" / "Objects"
        try:
            dir_mtime = os.path.getmtime(scan_dir)
        except OSError:
            dir_mtime = -1.0

        if self._control_assets_loaded and dir_mtime <= self._control_assets_mtime:
            return

        mapping: Dict[str, str] = {}
        def _path_priority(path: str) -> tuple[int, int]:
            lowered = path.lower()
            has_objects = "/objects/" in lowered
            depth = path.count("/")
            return (0 if has_objects else 1, -depth)

        def _store_variant(key: str, path: str) -> None:
            existing = mapping.get(key)
            if not existing:
                mapping[key] = path
                return
            if _path_priority(path) < _path_priority(existing):
                mapping[key] = path

        if scan_dir.exists():
            for asset_path in sorted(scan_dir.rglob("*.uasset")):
                stem = asset_path.stem
                if not stem.lower().startswith("bp_"):
                    continue
                try:
                    rel = asset_path.relative_to(self.content_dir)
                except ValueError:
                    continue
                rel_no_ext = rel.with_suffix("")
                game_path = "/Game/" + rel_no_ext.as_posix()
                class_name = f"{stem}_C"
                full_path = f"{game_path}.{class_name}"

                base = stem.lower()
                variants = {base}
                suffix = base[3:] if base.startswith("bp_") else ""
                if suffix:
                    variants.add(suffix)
                    variants.add(f"bp_{suffix}")
                for key in variants:
                    _store_variant(key, full_path)

        self._control_assets = mapping
        self._control_assets_loaded = True
        self._control_assets_mtime = dir_mtime

    def _resolve_control_blueprint(self, name: str) -> Optional[str]:
        if not name:
            return None
        normalized = name.strip().lower()
        if not normalized:
            return None
        self._ensure_control_asset_cache()
        return self._control_assets.get(normalized)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def resolve(self, name_or_path: str) -> str:
        """Resolve a friendly name or fallback to raw input."""
        self._maybe_reload()

        if name_or_path.startswith("/Game/") or name_or_path.startswith("/"):
            return name_or_path

        key = name_or_path.lower()
        resolved = self.flat_map.get(key)
        if resolved:
            return resolved

        control_resolved = self._resolve_control_blueprint(name_or_path)
        if control_resolved:
            return control_resolved

        return name_or_path

    def get_by_category(self, category: str) -> Dict[str, str]:
        self._maybe_reload()
        return self.data.get("categories", {}).get(category, {})

    def get_by_tag(self, tag: str) -> List[str]:
        self._maybe_reload()
        return self.data.get("tags", {}).get(tag, [])

    def search(self, query: str) -> List[tuple]:
        self._maybe_reload()
        query = query.lower()
        return [
            (name, path)
            for name, path in self.flat_map.items()
            if query in name or query in path.lower()
        ]

    def list_all(self) -> Dict[str, str]:
        self._maybe_reload()
        return self.flat_map.copy()

    def list_categories(self) -> List[str]:
        self._maybe_reload()
        return list(self.data.get("categories", {}).keys())

    def add_asset(
        self,
        name: str,
        path: str,
        category: str = "custom",
        save: bool = True,
    ) -> None:
        self._maybe_reload()
        self.data.setdefault("categories", {}).setdefault(category, {})[name] = path
        self.flat_map[name.lower()] = path
        if save:
            self.save()

    def save(self) -> None:
        try:
            os.makedirs(self.registry_path.parent, exist_ok=True)
            with open(self.registry_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            pass

    def _create_default_registry(self) -> None:
        self.data = {
            "version": "1.0",
            "description": "Asset registry for simplified asset spawning",
            "categories": {
                "shapes": {
                    "cube": "/Game/StarterContent/Shapes/Shape_Cube",
                    "sphere": "/Game/StarterContent/Shapes/Shape_Sphere",
                    "cylinder": "/Game/StarterContent/Shapes/Shape_Cylinder",
                }
            },
            "aliases": {},
            "tags": {},
        }
        self._build_flat_map()
        self.save()


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------
_registry: Optional[AssetRegistry] = None


def get_registry() -> AssetRegistry:
    global _registry
    if _registry is None:
        _registry = AssetRegistry()
    return _registry


def resolve_asset_path(name_or_path: str) -> str:
    return get_registry().resolve(name_or_path)


def list_assets(category: Optional[str] = None) -> Dict[str, str]:
    registry = get_registry()
    if category:
        return registry.get_by_category(category)
    return registry.list_all()


def search_assets(query: str) -> List[tuple]:
    return get_registry().search(query)
