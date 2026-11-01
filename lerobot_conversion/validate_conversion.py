"""Verify all mapped values, all video frames, source preservation and official reader."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import av
import h5py
import numpy as np
import pyarrow.parquet as pq

from convert_hdf5 import dump, sha256, text
from source_ground_truth import SourceGroundTruth


def validate(root: Path) -> dict:
    # Dense GT can expand into multi-GB Arrow caches. Keep the default on the
    # dataset volume, while honoring an explicitly configured cache location.
    os.environ.setdefault("HF_DATASETS_CACHE", str(root.parent / ".validation-cache"))
    from lerobot.datasets import LeRobotDataset
    manifest = json.loads((root / "meta/semantic_manifest.json").read_text(encoding="utf-8"))
    info = json.loads((root / "meta/info.json").read_text(encoding="utf-8"))
    n = manifest["training_rows"]
    archive = root / manifest["lossless_archive"]
    assert sha256(archive) == manifest["source_sha256"]
    assert info["codebase_version"] == "v3.0" and info["total_episodes"] == 1
    assert info["total_frames"] == n
    rows = []
    for file in sorted((root / "data").glob("*/*.parquet")):
        rows.extend(pq.read_table(file).to_pylist())
    assert len(rows) == n
    exact_checks, cast_error, video_metrics = 0, 0.0, {}
    with h5py.File(archive, "r") as h5:
        depth_shape = tuple(h5["cameras"]["front"]["depth_m"].shape[1:]) if "depth_m" in h5["cameras"]["front"] else None
        for mapping in manifest["mappings"]:
            key = mapping["target"]
            if mapping["dtype"] == "video":
                file = root / "videos" / key / "chunk-000/file-000.mp4"
                errors, times, maxima = [], [], []
                with av.open(str(file)) as container:
                    for i, frame in enumerate(container.decode(video=0)):
                        decoded = frame.to_ndarray(format="rgb24")
                        original = h5[mapping["source"]][i]
                        assert decoded.shape == original.shape
                        delta = decoded.astype(np.float32) - original.astype(np.float32)
                        errors.append(float(np.mean(delta ** 2)))
                        maxima.append(float(np.max(np.abs(delta))))
                        times.append(float(frame.time))
                assert len(times) == n
                np.testing.assert_allclose(times, np.arange(n) / info["fps"], atol=1e-5, rtol=0)
                mse = float(np.mean(errors))
                video_metrics[key] = {"decoded_frames": len(times), "mean_mse": mse,
                                      "psnr_db": float(10 * np.log10(255 ** 2 / mse)),
                                      "maximum_absolute_channel_error": max(maxima),
                                      "encoding_is_lossy": True}
                continue
            for i, row in enumerate(rows):
                original = h5[mapping["source"]][i + mapping["offset"]]
                if mapping["dtype"] == "string":
                    assert row[key] == text(original)
                else:
                    expected = np.asarray(original, dtype=mapping["dtype"]).reshape(mapping["shape"])
                    actual = np.asarray(row[key], dtype=mapping["dtype"]).reshape(mapping["shape"])
                    np.testing.assert_array_equal(actual, expected)
                    if key == "observation.state":
                        cast_error = max(cast_error, float(np.max(np.abs(actual.astype(np.float64) - original))))
                exact_checks += 1
        np.testing.assert_allclose([r["timestamp"] for r in rows], np.arange(n) / info["fps"], atol=1e-6, rtol=0)
        assert [r["frame_index"] for r in rows] == list(range(n))
        assert [r["index"] for r in rows] == list(range(n))
        assert all(r["episode_index"] == 0 and r["task_index"] == 0 for r in rows)

    dataset = LeRobotDataset(manifest["repo_id"], root=root, video_backend="pyav")
    assert len(dataset) == n
    image_shapes = {key: tuple(value["shape"]) for key, value in info["features"].items()
                    if value.get("dtype") == "video"}
    sampled = sorted({0, 38, 93, n // 2, 223, n - 1} & set(range(n)))
    for index in sampled:
        sample = dataset[index]
        np.testing.assert_array_equal(sample["action"].numpy(), np.asarray(rows[index]["action"], np.float32))
        assert sample["observation.state"].shape == (9,)
        assert sample["task"] == manifest["task"]
        for key in video_metrics:
            height, width, channels = image_shapes[key]
            assert tuple(sample[key].shape) == (channels, height, width)
    windowed = LeRobotDataset(manifest["repo_id"], root=root, video_backend="pyav",
                              delta_timestamps={"action": [0, 1 / info["fps"], 2 / info["fps"]]})
    assert tuple(windowed[0]["action"].shape) == (3, 8)
    assert windowed[n - 1]["action_is_pad"].tolist() == [False, True, True]
    with SourceGroundTruth(root) as gt:
        final = gt.observation(n)
        if depth_shape is not None:
            assert final["camera"]["depth_m"].shape == depth_shape
        np.testing.assert_array_equal(final["robot"]["qpos"], np.asarray(rows[-1]["next.qpos"]))
    stats = json.loads((root / "meta/stats.json").read_text())
    for key in ("action", "observation.state"):
        expected = np.asarray([r[key] for r in rows], np.float64)
        np.testing.assert_allclose(stats[key]["mean"], expected.mean(axis=0), atol=1e-6, rtol=1e-6)
        np.testing.assert_allclose(stats[key]["std"], expected.std(axis=0), atol=1e-6, rtol=1e-6)
    report = {"status": "passed", "rows": n, "fps": info["fps"], "native_feature_count": len(info["features"]),
              "mapped_value_checks": exact_checks, "source_sha256_verified": True,
              "state_float64_to_float32_max_absolute_error": cast_error,
              "videos": video_metrics, "official_reader_sample_indices": sampled,
              "action_window_and_padding_verified": True, "terminal_observation_verified": True,
              "action_state_mean_std_verified": True,
              "scope": "LeRobot v3 training view with native dense GT features plus a byte-identical HDF5 archive for exact source-layout access."}
    dump(root / "validation_report.json", report)
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    validate(parser.parse_args().root.resolve())
