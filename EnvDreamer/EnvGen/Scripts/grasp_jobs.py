"""Durable local grasp jobs; publish artifacts only after successful verification."""
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
from datetime import datetime
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / 'Saved/GraspRuns'
PYTHON = Path(os.environ.get('MANISKILL_PYTHON', 'D:/APP/Anaconda/python.exe'))
router = APIRouter(prefix='/api/simple/grasp')
FILES = {'lerobot_v3.zip':'application/zip', 'trajectory.csv':'text/csv',
         'ue_white.mp4':'video/mp4', 'success.json':'application/json',
         'scene_spec.json':'application/json', 'result.json':'application/json',
         'ue_render.json':'application/json'}
OPTIONAL_FILES={'scene_initial.png':'image/png','scene_final.png':'image/png','request.json':'application/json'}
children = {}


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def directory_for(job_id):
    if not re.fullmatch(r'grasp_[A-Za-z0-9_]+', job_id):
        raise HTTPException(404, 'Unknown grasp job')
    directory = RUNS / job_id
    if not directory.is_dir():
        raise HTTPException(404, 'Unknown grasp job')
    return directory


def status(directory):
    try:
        data = json.loads((directory / 'progress.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {'status':'failed', 'message':'任务状态不可用，请查看 Saved/GraspRuns 下的任务日志'}
    data['job_id'] = directory.name
    if data['status'] == 'running' and not process_alive(data.get('pid')):
        data = {'job_id':directory.name, 'status':'failed', 'message':'任务进程已退出；请查看 Saved/GraspRuns 下的任务日志后重试。'}
    if data['status']=='failed' and (directory/'trajectory.h5').is_file() and (directory/'success.json').is_file():
        try:
            data['can_retry_render']=bool(json.loads((directory/'success.json').read_text(encoding='utf-8')).get('success'))
        except (OSError,ValueError):
            data['can_retry_render']=False
    if data['status'] == 'complete':
        if not (directory / 'result.json').is_file() or any(not (directory/name).is_file() for name in FILES):
            return {'job_id':directory.name, 'status':'failed', 'message':'任务产物不完整，禁止下载。'}
        data['downloads'] = {name:f'/api/simple/grasp/{directory.name}/files/{name}' for name in FILES}
        data['downloads'].update({name:f'/api/simple/grasp/{directory.name}/files/{name}'
                                 for name in OPTIONAL_FILES if (directory/name).is_file()})
    return data


def active_job():
    for job_id, process in list(children.items()):
        if process.poll() is None:
            return {'status':'running','job_id':job_id}
    for directory in sorted((p for p in RUNS.glob('grasp_*') if p.is_dir()), reverse=True):
        data = status(directory)
        if data['status'] == 'running':
            return data
    return None


@router.get('/latest')
def latest():
    active = active_job()
    if active:
        return get_job(active['job_id'])
    directories = sorted((p for p in RUNS.glob('grasp_*') if p.is_dir()), key=lambda p:p.stat().st_mtime, reverse=True)
    return status(directories[0]) if directories else {'status':'idle'}


class TaskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=6000)


@router.post('/start')
def start(body: TaskRequest):
    if active_job():
        raise HTTPException(409, '已有抓取任务正在运行')
    if not PYTHON.is_file():
        raise HTTPException(503, '找不到物理环境 Python，请设置 MANISKILL_PYTHON')
    RUNS.mkdir(parents=True, exist_ok=True)
    job_id = 'grasp_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    directory = RUNS / job_id
    # The worker creates its output directory. Keep its log outside until then.
    log_path = RUNS / (job_id + '.log')
    request_path = RUNS / (job_id + '.request.json')
    request_path.write_text(json.dumps({'prompt':body.prompt},ensure_ascii=False),encoding='utf-8')
    with log_path.open('wb') as log:
        process = subprocess.Popen([str(PYTHON), '-m', 'Scripts.grasp_pipeline', '--output',str(directory),
                                    '--request',str(request_path)],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    children[job_id] = process
    return {'status':'running', 'job_id':job_id, 'message':'正在启动物理抓取任务'}


@router.get('/{job_id}')
def get_job(job_id: str):
    if job_id in children and not (RUNS / job_id / 'progress.json').exists():
        process = children[job_id]
        return {'job_id':job_id, 'status':'running' if process.poll() is None else 'failed',
                'message':'正在启动任务' if process.poll() is None else '启动失败，请查看 Saved/GraspRuns 下的日志'}
    return status(directory_for(job_id))


class RenderRetry(BaseModel):
    job_id: str


@router.post('/retry-render')
def retry_render(body: RenderRetry):
    if active_job():
        raise HTTPException(409,'已有任务正在运行')
    directory=directory_for(body.job_id)
    if not status(directory).get('can_retry_render'):
        raise HTTPException(409,'该任务没有可重用的成功物理轨迹')
    with (RUNS/(body.job_id+'.log')).open('ab') as log:
        process=subprocess.Popen([str(PYTHON),'-m','Scripts.grasp_pipeline','--output',str(directory),'--resume'],
            cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    children[body.job_id]=process
    return {'status':'running','job_id':body.job_id,'message':'重用成功轨迹，正在重新渲染 UE 视频'}


@router.get('/{job_id}/files/{filename}')
def download(job_id: str, filename: str):
    directory = directory_for(job_id)
    allowed={**FILES,**OPTIONAL_FILES}
    if filename not in allowed:
        raise HTTPException(404, 'Unknown artifact')
    if status(directory)['status'] != 'complete':
        raise HTTPException(409, '任务尚未通过完整验证')
    if not (directory/filename).is_file():
        raise HTTPException(404,'Artifact not available for this job')
    return FileResponse(directory / filename, media_type=allowed[filename],
                        filename=None if filename.endswith(('.mp4','.png')) else filename,
                        headers={'Cache-Control':'no-cache'})
