"""Exact user prompt through the web UI with the background UE runtime."""
import json,time
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path('_validation/retest/background');out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,channel='msedge')
 try:
  page=browser.new_page(viewport={'width':1440,'height':1000});page.goto('http://127.0.0.1:8765',wait_until='networkidle')
  page.evaluate("localStorage.setItem('pipelineRun','bbbb21eabd10')")
  page.reload(wait_until='networkidle');page.wait_for_function("document.getElementById('status').textContent.includes('交付未完成')")
  page.screenshot(path=str(out/'previous-incomplete.png'),full_page=True)
  page.locator('#prompt').fill('让 Panda 机械臂抓起桌面上的红色方块，放到右侧蓝色目标区域中，然后松开夹爪并撤离。')
  with page.expect_response('**/api/maniskill/pipeline/start') as pending:
   page.locator('#pipeline').click()
  run=pending.value.json()['run_id'];print(run,flush=True)
  deadline=time.monotonic()+600;last=None
  while time.monotonic()<deadline:
   data=page.request.get('http://127.0.0.1:8765/api/maniskill/pipeline/'+run).json()
   phase=data.get('progress',{})
   if phase!=last:print(phase,flush=True);last=phase
   if data['status']!='running':break
   page.wait_for_timeout(1000)
  (out/'result.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
  assert data['status']=='complete' and data['delivery_status']=='complete',data
  assert data['episodes'][0]['white_model_video_url'] and not data['episodes'][0]['ue_render_error']
  page.wait_for_function("document.getElementById('video-preview').readyState >= 2",timeout=30000)
  page.get_by_role('button',name='刷新截图',exact=True).click()
  page.wait_for_function("document.getElementById('video-preview').readyState >= 2")
  assert page.locator('#video-preview').is_visible()
  page.screenshot(path=str(out/'complete.png'),full_page=True)
  print('PASS: complete data and video',flush=True)
 finally:browser.close()
