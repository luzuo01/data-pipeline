"""Regression cases for ZIP ingestion, isolated in pytest tmp_path."""
import zipfile
import pytest
from fastapi.testclient import TestClient
from data_engine.api import create_app
from data_engine.zip_pipeline import inspect_zip, extract_zip



def test_corrupt_zip_is_a_client_error(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/v1/imports', files={'file': ('invalid.zip', b'not a zip', 'application/zip')}, data={'dataset_name': 'audit'})
    assert response.status_code in {400, 422}, (response.status_code, response.text)
    assert not list((tmp_path / 'uploads').iterdir())
    assert not app.state.service._imports


@pytest.mark.parametrize('prefix', ['', 'sample/'])
@pytest.mark.parametrize('kind', ['dexmal_jsonl_video_v1', 'robochallenge_table30_v2_arx5'])
def test_other_sources_accept_zip_root(tmp_path, prefix, kind):
    files = (['episode_00000.jsonl', 'top.mp4', 'wrist.mp4'] if kind == 'dexmal_jsonl_video_v1'
             else ['states/states.jsonl', 'meta/episode_meta.json', 'videos/cam_global_rgb.mp4',
                   'videos/cam_side_rgb.mp4', 'videos/cam_arm_rgb.mp4'])
    archive = tmp_path / 'sample.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        for name in files:
            z.writestr(prefix + name, '{}')
    assert [s['kind'] for s in inspect_zip(archive)['sources']] == [kind]


@pytest.mark.parametrize('prefix', ['', 'sample/'])
def test_pipeline_zip_discovery_independent_of_wrapper_folder(tmp_path, prefix):
    archive = tmp_path / 'sample.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr(prefix + 'data/control_records.jsonl', '{}\n')
        z.writestr(prefix + 'scene/task.json', '{}')
    sources = inspect_zip(archive)['sources']
    assert [s['kind'] for s in sources] == ['pipeline_confined_reach_v1'], sources


def test_windows_zip_separator_roundtrip(tmp_path):
    archive = tmp_path / 'windows.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('sample\\data\\control_records.jsonl', '{}\n')
        z.writestr('sample\\scene\\task.json', '{}')
    assert inspect_zip(archive)['sources']
    destination = extract_zip(archive, tmp_path / 'extracted')
    assert (destination / 'sample/data/control_records.jsonl').exists()
