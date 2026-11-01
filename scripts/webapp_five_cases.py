import argparse,json,time
from pathlib import Path
from playwright.sync_api import sync_playwright
parser=argparse.ArgumentParser();parser.add_argument('case',type=int);a=parser.parse_args()
prompt=json.loads(Path('scripts/five_case_prompts.json').read_text(encoding='utf-8-sig'))[a.case-1]
out=Path('_validation/five-cases')/str(a.case);out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,channel='msedge')
 try:
  page=b.new_page(viewport={'width':1440,'height':1000});page.goto('http://127.0.0.1:8765',wait_until='networkidle')
  if a.case==5:
   previous=json.loads(Path('_validation/five-cases/4/result.json').read_text(encoding='utf-8'))['run_id']
   page.evaluate('(id)=>localStorage.setItem("pipelineRun",id)',previous)
   page.reload(wait_until='networkidle')
   page.wait_for_function("document.getElementById('video-preview').readyState>=2")
  page.locator('#prompt').fill(prompt)
  with page.expect_response('**/api/maniskill/pipeline/start') as response:page.locator('#pipeline').click()
  run=response.value.json()['run_id'];print(run,flush=True)
  assert page.locator('#video-preview').is_hidden()
  last=None;deadline=time.monotonic()+900
  while time.monotonic()<deadline:
   data=page.request.get('http://127.0.0.1:8765/api/maniskill/pipeline/'+run).json();phase=data.get('progress',{}).get('phase')
   if phase!=last:print(phase,flush=True);last=phase
   if data['status']!='running':break
   page.wait_for_timeout(1000)
  (out/'result.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
  page.wait_for_function("!document.getElementById('pipeline').disabled")
  page.screenshot(path=str(out/'result.png'),full_page=True)
  if a.case==5:
   assert data['status']=='failed' and '不支持' in data['error'] and not data['episodes'],data
   assert page.locator('#video-preview').is_hidden()
   page.reload(wait_until='networkidle')
   page.wait_for_function("document.getElementById('status').textContent.includes('数据生成失败')")
   assert page.locator('#video-preview').is_hidden()
   assert not page.locator('#preview').get_attribute('src')
  else:
   assert data['delivery_status']=='complete',data.get('failed_episodes') or data
   assert data['episodes'][0]['white_model_video_url'] and not data['episodes'][0]['ue_render_error']
   page.wait_for_function("document.getElementById('video-preview').readyState>=2")
   page.screenshot(path=str(out/'video.png'),full_page=True)
   for key in ['download_url','metadata_url','white_model_video_url']:
    r=page.request.get('http://127.0.0.1:8765'+data['episodes'][0][key]);assert r.ok,key
  print('PASS',a.case,flush=True)
 finally:b.close()
