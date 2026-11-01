import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from Scripts import grasp_jobs


class GraspJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(grasp_jobs, 'RUNS', self.root)
        self.patch.start()
        self.job = self.root / 'grasp_test'
        self.job.mkdir()
        app = FastAPI()
        app.include_router(grasp_jobs.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.patch.stop()
        self.temp.cleanup()

    def progress(self, data):
        (self.job / 'progress.json').write_text(json.dumps(data), encoding='utf-8')

    def test_dead_worker_is_failure_and_artifacts_not_published(self):
        self.progress({'status':'running','pid':-1})
        data = self.client.get('/api/simple/grasp/latest').json()
        self.assertEqual(data['status'], 'failed')
        self.assertNotIn('downloads', data)
        self.assertEqual(self.client.get('/api/simple/grasp/grasp_test/files/trajectory.h5').status_code,404)

    def test_live_worker_prevents_second_job(self):
        self.progress({'status':'running','pid':os.getpid()})
        self.assertEqual(self.client.post('/api/simple/grasp/start',json={'prompt':'stack red on green'}).status_code,409)
        self.assertEqual(self.client.get('/api/simple/grasp/latest').json()['status'],'running')

    def test_complete_requires_all_artifacts_and_downloads_are_allowlisted(self):
        self.progress({'status':'complete'})
        self.assertEqual(self.client.get('/api/simple/grasp/latest').json()['status'],'failed')
        for name in grasp_jobs.FILES:
            (self.job / name).write_bytes(b'test fixture')
        data = self.client.get('/api/simple/grasp/latest').json()
        self.assertEqual(data['status'],'complete')
        response = self.client.get(data['downloads']['lerobot_v3.zip'])
        self.assertEqual(response.content,b'test fixture')
        self.assertEqual(self.client.get('/api/simple/grasp/grasp_test/files/trajectory.h5').status_code,404)
        self.assertEqual(self.client.get('/api/simple/grasp/grasp_test/files/progress.json').status_code,404)
        self.assertEqual(self.client.get('/api/simple/grasp/invalid/files/trajectory.h5').status_code,404)

    def test_job_log_and_request_files_do_not_hide_latest_job(self):
        self.progress({'status':'failed','message':'expected failure'})
        (self.root/'grasp_newer.log').write_text('log',encoding='utf-8')
        (self.root/'grasp_newer.request.json').write_text('{}',encoding='utf-8')
        result=self.client.get('/api/simple/grasp/latest').json()
        self.assertEqual(result['job_id'],'grasp_test')
        self.assertEqual(result['message'],'expected failure')

    def test_missing_or_empty_task_rejected(self):
        self.assertEqual(self.client.post('/api/simple/grasp/start').status_code,422)
        self.assertEqual(self.client.post('/api/simple/grasp/start',json={'prompt':''}).status_code,422)


if __name__ == '__main__':
    unittest.main()
