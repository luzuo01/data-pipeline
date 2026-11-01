"""Explicit access to lossless source extensions, including the final observation."""
from pathlib import Path

import h5py


class SourceGroundTruth:
    def __init__(self, dataset_root: str | Path):
        self.file = h5py.File(Path(dataset_root) / "extras" / "source_episode.h5", "r")

    def observation(self, index: int, camera: str = "front") -> dict:
        if not 0 <= index < len(self.file["timestamps"]):
            raise IndexError(index)
        base = self.file[f"cameras/{camera}"]
        return {"timestamp": self.file["timestamps"][index],
                "camera": {key: value[index] for key, value in base.items()},
                "robot": {key: value[index] for key, value in self.file["observations"].items()},
                "objects": {key: group["state"][index] for key, group in self.file["objects"].items()}}

    def close(self) -> None:
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
