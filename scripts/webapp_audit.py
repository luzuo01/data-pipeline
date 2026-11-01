"""Headless Playwright audit of a running application; records actual evidence.

The physical pipeline test explicitly sets render_ue=false. UE is not mocked
or reported as successful. Evidence is written beneath _validation.
"""
import argparse
import copy
import json
import os
import time
import zipfile
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--output', type=Path, default=ROOT / '_validation/webapp-fixes-20261101')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cases, errors, console, responses = [], [], [], []
    summary = {'base_url': args.url, 'browser_channel': os.getenv('PLAYWRIGHT_CHANNEL', 'msedge'),
               'cases': cases, 'page_errors': errors, 'console': console, 'http_errors': responses,
               'ue_render_requested': False}
    def record(name, passed, **details):
        cases.append({'name': name, 'passed': bool(passed), **details})
        print(json.dumps(cases[-1], ensure_ascii=False), flush=True)
        (args.output / 'browser-results.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel=summary['browser_channel'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('console', lambda msg: console.append({'type': msg.type, 'text': msg.text}) if msg.type in {'error', 'warning'} else None)
        page.on('response', lambda r: responses.append({'url': r.url, 'status': r.status}) if r.status >= 400 else None)
        try:
            page.goto(args.url, wait_until='networkidle')
            record('home_controls', page.get_by_role('button', name='生成成功轨迹与真值数据').is_visible(), title=page.title())
            page.locator('summary').filter(has_text='直接使用 SceneSpec JSON').click()
            page.locator('#json-input').fill('{broken')
            page.locator('#generate').click()
            page.wait_for_function("document.getElementById('status').textContent.includes('SyntaxError')")
            record('client_invalid_json_recovers', page.locator('#generate').is_enabled() and page.locator('#pipeline').is_enabled(), status=page.locator('#status').inner_text())
            page.locator('#json-input').fill('')
            with page.expect_response('**/api/maniskill/scene/generate', timeout=90000) as pending:
                page.locator('#generate').click()
            result = pending.value.json()
            page.wait_for_function("!document.getElementById('generate').disabled")
            page.screenshot(path=str(args.output / '02-default-franka.png'), full_page=True)
            record('default_prompt_creates_real_preview', bool(result.get('preview_url')), http=pending.value.status, backend=result.get('scene_spec', {}).get('backend'), validation_ok=result.get('validation', {}).get('ok'), status=page.locator('#status').inner_text(), screenshot='02-default-franka.png')
            spec = json.loads((ROOT / 'EnvDreamer/ManiSkillGen/examples/pipeline_pick_place.json').read_text(encoding='utf-8'))
            spec['randomization'] = {}
            invalid = copy.deepcopy(spec)
            invalid['backend'] = 'ue5'
            invalid['robot']['control_mode'] = 'ue5_joint_pos'
            invalid['scene']['objects'][0]['size'][0] = -1
            page.locator('#json-input').fill(json.dumps(invalid))
            with page.expect_response('**/api/maniskill/scene/generate') as pending:
                page.locator('#generate').click()
            result = pending.value.json()
            page.wait_for_function("!document.getElementById('generate').disabled")
            page.screenshot(path=str(args.output / '03-invalid-ue-rejected.png'), full_page=True)
            record('invalid_ue_scene_rejected', pending.value.status in {400, 422}, http=pending.value.status, validation_ok=result.get('validation', {}).get('ok'), api_status=result.get('status'), status=page.locator('#status').inner_text(), screenshot='03-invalid-ue-rejected.png')
            valid_ue = copy.deepcopy(spec)
            valid_ue['backend'] = 'ue5'
            valid_ue['robot']['control_mode'] = 'ue5_joint_pos'
            page.locator('#json-input').fill(json.dumps(valid_ue))
            with page.expect_response('**/api/maniskill/scene/generate') as pending:
                page.locator('#generate').click()
            page.wait_for_function("!document.getElementById('generate').disabled")
            record('ue_generation_not_falsely_reported', pending.value.status == 422 and pending.value.json()['status'] == 'unsupported', status=page.locator('#status').inner_text())
            page.locator('#json-input').fill(json.dumps(spec))
            page.locator('#seed').fill('3')
            with page.expect_response('**/api/maniskill/scene/generate', timeout=90000) as pending:
                page.locator('#generate').click()
            result = pending.value.json()
            page.wait_for_function("!document.getElementById('generate').disabled")
            if result.get('preview_url'):
                page.wait_for_function("document.getElementById('preview').naturalWidth > 0")
            page.screenshot(path=str(args.output / '04-real-panda-preview.png'), full_page=True)
            record('real_panda_scene_preview', pending.value.status == 200 and bool(result.get('preview_url')) and result.get('manifest', {}).get('runtime_backend') == 'maniskill3', http=pending.value.status, backend=result.get('manifest', {}).get('runtime_backend'), screenshot='04-real-panda-preview.png')
            for name, payload in [('episodes_zero', {'num_episodes': 0}), ('episodes_over_limit', {'num_episodes': 11}), ('fractional_seed', {'seed': 1.5}), ('invalid_render_flag', {'render_ue': 'false'})]:
                r = context.request.post(args.url + '/api/maniskill/pipeline/start', data={'spec': spec, **payload})
                record('pipeline_rejects_' + name, r.status == 400, http=r.status)
            r = context.request.post(args.url + '/api/maniskill/scene/validate', data=b'{broken', headers={'Content-Type': 'application/json'})
            record('live_invalid_json_is_4xx', r.status in {400, 422}, http=r.status, body=r.text()[:200])
            r = context.request.post(args.url + '/api/maniskill/pipeline/start', data={'spec': spec, 'seed': 3, 'num_episodes': 1, 'render_ue': False})
            assert r.status == 202, r.text()
            run_id = r.json()['run_id']
            summary['physical_run_id'] = run_id
            page.evaluate('(id) => localStorage.setItem("pipelineRun", id)', run_id)
            page.reload(wait_until='networkidle')
            page.wait_for_function("document.getElementById('pipeline').disabled")
            record('running_job_restores_after_page_reload', page.locator('#pipeline').is_disabled(), run_id=run_id)
            start = time.perf_counter()
            busy = context.request.post(args.url + '/api/maniskill/pipeline/start', data={'spec': spec})
            state = context.request.get(args.url + '/api/maniskill/scene/state')
            record('parallel_start_rejected_api_remains_responsive', busy.status == 409 and state.status == 200 and time.perf_counter() - start < 3, second_start=busy.status, state_http=state.status, duration_s=round(time.perf_counter() - start, 3))
            deadline = time.monotonic() + 180
            result = {}
            while time.monotonic() < deadline:
                result = context.request.get(args.url + '/api/maniskill/pipeline/' + run_id).json()
                if result['status'] != 'running':
                    break
                page.wait_for_timeout(500)
            (args.output / 'physical-run.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            record('physical_pipeline_completes', result.get('status') == 'complete' and result.get('successful_episodes') == 1, status=result.get('status'), failures=result.get('failed_episodes'), elapsed_s=result.get('duration_s'))
            assert result.get('episodes'), result
            episode = result['episodes'][0]
            page.wait_for_function("!document.getElementById('pipeline').disabled", timeout=15000)
            page.get_by_role('button', name='轨迹与数据', exact=True).click()
            link = page.get_by_role('link', name='下载 LeRobot v3')
            link.wait_for()
            download = context.request.get(args.url + link.get_attribute('href'))
            archive = args.output / 'browser_download_lerobot_v3.zip'
            archive.write_bytes(download.body())
            with zipfile.ZipFile(archive) as z:
                info_path = next(n for n in z.namelist() if n.endswith('/meta/info.json'))
                info = json.loads(z.read(info_path))
            metadata = context.request.get(args.url + episode['metadata_url'])
            preview = context.request.get(args.url + episode['preview_url'])
            record('downloads_are_real_v3_metadata_and_preview', download.status == metadata.status == preview.status == 200 and info['codebase_version'] == 'v3.0' and preview.body().startswith(b'GIF'), rows=info.get('total_frames'), actions=episode.get('num_actions'), observations=episode.get('num_observations'), bytes=len(download.body()))
            restricted = context.request.get(args.url + f'/api/maniskill/pipeline/{run_id}/files/episode_000/trajectory.h5')
            record('internal_source_not_publicly_downloadable', restricted.status == 404, http=restricted.status)
            page.reload(wait_until='networkidle')
            page.get_by_role('link', name='下载 LeRobot v3').wait_for()
            page.screenshot(path=str(args.output / '05-completed-real-pipeline.png'), full_page=True)
            record('completed_job_restores_after_reload', page.get_by_role('link', name='下载 LeRobot v3').is_visible(), screenshot='05-completed-real-pipeline.png')
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(args.output / '06-mobile.png'), full_page=True)
            dims = page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
            record('mobile_no_horizontal_overflow', dims['scroll'] <= dims['width'] + 1, **dims)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            r = context.request.post(args.url + '/api/maniskill/pipeline/start', data={'spec': {'backend': 'portal_trace'}, 'render_ue': False})
            failed_id = r.json()['run_id']
            summary['early_failed_run_id'] = failed_id
            page.evaluate('(id) => localStorage.setItem("pipelineRun", id)', failed_id)
            page.reload(wait_until='networkidle')
            page.wait_for_function("document.getElementById('dataset').textContent.includes('ValueError')")
            page.screenshot(path=str(args.output / '07-failed-job-status.png'), full_page=True)
            status = page.locator('#status').inner_text()
            record('failed_job_status_counts_failure', '0 条失败' not in status, status=status, error_panel=page.locator('#dataset').inner_text()[:450], screenshot='07-failed-job-status.png')
            record('no_uncaught_browser_exceptions', not errors, errors=errors)
        except Exception as exc:
            summary['fatal'] = repr(exc)
            page.screenshot(path=str(args.output / 'fatal.png'), full_page=True)
            raise
        finally:
            (args.output / 'browser-results.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
            context.tracing.stop(path=str(args.output / 'browser-trace.zip'))
            context.close()
            browser.close()
    print(json.dumps({'passed': sum(c['passed'] for c in cases), 'failed': sum(not c['passed'] for c in cases)}, ensure_ascii=False))
    return 0 if all(c['passed'] for c in cases) else 1


if __name__ == '__main__':
    raise SystemExit(main())
