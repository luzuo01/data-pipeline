"""Check actual generated source states against each prompt's semantic requirements."""
import json,sys
from pathlib import Path
import h5py,numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'EnvDreamer'))
from ManiSkillGen.local_intent import COLORS
n=int(sys.argv[1]);out=ROOT/'_validation/five-cases'/str(n)
r=json.loads((out/'result.json').read_text(encoding='utf-8'))
assert r['delivery_status']=='complete'
e=r['episodes'][0];spec=e['scene_spec'];objs={o['id']:o for o in spec['scene']['objects']};task=spec['task'];moving=objs[task['object_id']];target=objs[task['target_id']]
def color(o):return min(COLORS,key=lambda c:np.linalg.norm(np.array(o['color'])-COLORS[c]))
with h5py.File(Path(e['lerobot_path'])/'extras/source_episode.h5') as f:
 states={g.attrs['id']:g['state'][:] for g in f['objects'].values()};m=states[moving['id']];t=states[target['id']]
 assert bool(f.attrs['complete']) and not f['observations/success'][0] and f['observations/success'][-10:].all()
 assert len(m)==len(f['actions'])+1
 assert not f['observations/grasped'][-1]
 report={'case':n,'run_id':r['run_id'],'task':task,'moving_color':color(moving),'target_color':color(target),'frames':len(m),'final_success':True}
 if n==1:
  assert color(moving)=='red' and color(target)=='blue' and t[0,0]>m[0,0]
 elif n==2:
  assert task['template']=='push' and moving['shape']=='cylinder' and color(moving)=='yellow' and color(target)=='green'
  assert not f['observations/grasped'][:].any()
  report['max_center_height_change_m']=float(np.max(np.abs(m[:,2]-m[0,2])))
  assert report['max_center_height_change_m']<.008
  assert np.linalg.norm(m[-1,:2]-m[0,:2])>.05
 elif n==3:
  assert task['template']=='stack' and color(moving)=='red' and color(target)=='blue' and moving['size'][0]<target['size'][0]
  assert abs(m[-1,2]-t[-1,2]-(moving['size'][2]+target['size'][2])/2)<.004
 elif n==4:
  assert color(moving)=='green' and color(target)=='blue'
  assert {color(o) for o in objs.values() if o['dynamic']}=={'red','green','yellow'}
  report['distractor_displacement_m']={oid:float(np.linalg.norm(st[:,:3]-st[0,:3],axis=1).max()) for oid,st in states.items() if objs[oid]['dynamic'] and oid!=moving['id']}
  assert all(d<.003 for d in report['distractor_displacement_m'].values())
 meta=json.loads((Path(e['episode_metadata_path']).parent/'ue_render.json').read_text())
 assert meta['frames']==len(m) and meta['reused_frame_count']==0
 report['video_frames']=meta['frames'];report['video_seconds']=meta['duration_s']
(out/'semantic-audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))
