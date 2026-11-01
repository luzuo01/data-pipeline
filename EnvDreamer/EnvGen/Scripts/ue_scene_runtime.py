"""Remote-editor resources belong to the running UE project, not this checkout."""
from pathlib import Path
import json
import time
import urllib.request

from PIL import Image

from .panda_demo import call


def clear_pipeline_actors(base, builder):
    """Remove only our generated preview actors, leaving the user's map intact."""
    actors = call('GetAllLevelActors', {}, base,
                  '/Script/UnrealEd.Default__EditorActorSubsystem',
                  generate_transaction=False)['ReturnValue']
    removed = []
    for actor in actors:
        request = urllib.request.Request(base + '/remote/object/property',
            data=json.dumps({'objectPath': actor, 'propertyName': 'Tags',
                             'access': 'READ_ACCESS'}).encode(), method='PUT',
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            tags = json.load(response).get('Tags', [])
        if 'JsonGenerated' not in tags:
            continue
        for tag in tags:
            if tag.startswith(('RCID:panda_demo_', 'RCID:ue5_demo_')):
                actor_id = tag[len('RCID:'):]
                call('RC_Remove', {'Id': actor_id}, base, builder, generate_transaction=False)
                removed.append(actor_id)
                break
    return removed


def capture_snapshot(rc, name, destination, *, timeout=90):
    """Wait for the unique file returned by UE; never substitute a stale latest.png."""
    response = rc('RC_Snapshot', Name=name, ResX=960, ResY=540)
    if response.get('ReturnValue') is not True or not response.get('OutPath'):
        raise RuntimeError('UE screenshot response must contain a successful OutPath')
    shot = Path(response['OutPath'])
    if not shot.is_absolute() or shot.stem != name:
        raise RuntimeError(f'UE returned an unexpected screenshot path: {shot}')
    deadline = time.monotonic() + timeout
    while True:
        try:
            with Image.open(shot) as im:
                im.load()
                im.convert('RGB').save(destination)
            return shot
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                raise TimeoutError(f'UE screenshot not readable after {timeout:g}s: {shot}')
            time.sleep(.06)
