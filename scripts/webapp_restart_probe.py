"""Run after restarting ManiSkillGen, using IDs from webapp_audit.py."""
import json
import os
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / '_validation/webapp-fixes-20261101')
OUT = parser.parse_args().output
audit = json.loads((OUT / 'browser-results.json').read_text(encoding='utf-8'))
results = {}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel=os.getenv('PLAYWRIGHT_CHANNEL', 'msedge'))
    try:
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        page.goto(audit['base_url'], wait_until='networkidle')
        for kind in ('physical_run_id', 'early_failed_run_id'):
            response = context.request.get(audit['base_url'] + '/api/maniskill/pipeline/' + audit[kind])
            results[kind] = {'run_id': audit[kind], 'http': response.status, 'status': response.json().get('status'), 'error': response.json().get('error')}
        page.evaluate('(id) => localStorage.setItem("pipelineRun", id)', audit['early_failed_run_id'])
        page.reload(wait_until='networkidle')
        page.wait_for_function("document.getElementById('status').textContent.includes('数据生成失败')")
        results['restored_failure_ui'] = page.locator('#status').inner_text()
        page.screenshot(path=str(OUT / '08-restart-failure-restored.png'), full_page=True)
        assert results['physical_run_id']['http'] == 200 and results['physical_run_id']['status'] == 'complete'
        assert results['early_failed_run_id']['http'] == 200 and results['early_failed_run_id']['status'] == 'failed'
        page.evaluate('localStorage.setItem("pipelineRun", "000000000000")')
        page.reload(wait_until='networkidle')
        page.wait_for_function("document.getElementById('status').textContent.includes('任务记录不存在')")
        assert page.evaluate('localStorage.getItem("pipelineRun")') is None
        results['unknown_job_cleared'] = True
        # Finish on the completed dataset.
        page.evaluate('(id) => localStorage.setItem("pipelineRun", id)', audit['physical_run_id'])
        page.reload(wait_until='networkidle')
        page.get_by_role('link', name='下载 LeRobot v3').wait_for()
        results['completed_download_restored'] = True
        (OUT / 'restart-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(results, ensure_ascii=False))
    finally:
        browser.close()
