"""Detached server supervisor; frozen frontends, original prompts and SQL scorer."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path('/path/to/access')
SOURCE = ROOT/'output/strengthened_baseline_final_eval_20261104'
JOB = ROOT/'output/strengthened_multibackend_20261105'
MODELS = {'qwen25_7b':'Qwen2.5-7B-Instruct',
          'qwen25_14b':'Qwen2.5-14B-Instruct',
          'arctic_r1_7b':'Arctic-Text2SQL-R1-7B',
          'qwen38_27b':'Qwen3.8-27B'}
VLLM = '/path/to/backend-environment/vllm-venv/bin/vllm'


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2))
    temp.replace(path)


def prepare(name, model):
    folder = JOB/name
    folder.mkdir(exist_ok=True)
    manifest = json.loads((SOURCE/'MANIFEST.json').read_text())
    assert manifest['cohort'] == 2800
    assert [manifest['arms'][a]['backend_mode'] for a in ('serial','joint')] == ['rbac','baseline']
    for arm in ('serial', 'joint'):
        dest = folder/arm
        dest.mkdir(exist_ok=True)
        for leaf in ('backend_inputs', 'evaluation_rows.jsonl'):
            target = dest/leaf
            if not target.exists():
                target.symlink_to(SOURCE/arm/leaf, target_is_directory=leaf=='backend_inputs')
    for leaf in ('MANIFEST.json', 'score_strong_backend_remote_20261104.py'):
        target = folder/leaf
        if not target.exists(): target.symlink_to(SOURCE/leaf)
    original = (SOURCE/'run_strong_backend_server_20261104.py').read_text()
    # Explicit mechanical adaptation keeps the existing generation/retry/scoring path.
    replacements = {
        "JOB=ROOT/'output/strengthened_baseline_final_eval_20261104'": 'JOB=Path('+repr(str(folder))+')\nsys.path.insert(0,'+repr(str(SOURCE))+')',
        "            from run_clarification_deepseek import configure\n            configure()": '            # Local endpoint; no API secret configuration required.',
        "env=dict(os.environ,RBAC_API_BASE=os.environ['OPENAI_BASE_URL'],RBAC_API_KEY=os.environ['OPENAI_API_KEY'],RBAC_DEEPSEEK_THINKING='disabled',RBAC_REQUEST_TIMEOUT='180',PYTHONIOENCODING='utf-8')": "env=dict(os.environ,RBAC_API_BASE='http://127.0.0.1:8000/v1',RBAC_API_KEY='local',RBAC_REQUEST_TIMEOUT='1800',PYTHONIOENCODING='utf-8')",
        "str(JOB/'predict_rbac_cloud_only.py'),'--provider','openai'": "str(ROOT/'scripts'/"+repr('predict_rbac_gold_qwen.py' if name=='qwen38_27b' else 'predict_rbac_endpoint.py')+") ,'--provider','openai'",
        "'--model','deepseek-flash'": "'--model',"+repr(model),
        "'--workers','4'": "'--workers','8'",
        "model='deepseek_flash'": 'model='+repr(name),
        "                    if bad:raise RuntimeError('Generation failures remain; see failed_attempt artifacts')": "                    terminal=('Error: Model emitted no parseable answer','Error: Generation truncated at max_tokens; see response audit')\n                    technical=[i for i in bad if predictions[i]['pred_sql'] not in terminal or not predictions[i].get('raw_response','').strip()]\n                    if technical:raise RuntimeError('Technical generation failures remain; see failed_attempt artifacts')\n                    if bad:save(dest/f'level{lv}_terminal_generation_failures.json',[dict(index=i,prediction=predictions[i],evaluation='generation_error_zero_credit') for i in bad])",
    }
    derived = original
    for old, new in replacements.items():
        assert old in derived, ('Upstream source changed', old)
        derived = derived.replace(old, new)
    compile(derived, '<adapted worker>', 'exec')
    worker = folder/'run_worker_v3.py'
    if worker.exists(): assert worker.read_text() == derived
    else: worker.write_text(derived)
    save(folder/'PROVENANCE.json', dict(model=model, frontend_source=str(SOURCE),
         upstream_runner_sha256=hashlib.sha256(original.encode()).hexdigest(),
         worker_sha256=hashlib.sha256(derived.encode()).hexdigest(),
         prompt_modes={'serial':'rbac','joint':'baseline'}, cohort=2800,
         retention_metric='result_output_completion_permissions_v2_20261105',
         retention_scoring='pending after SQL generation; never use old retention figures'))
    return folder, worker


def run_model(name, model):
    folder, worker = prepare(name, model)
    def complete(arm):
        path = folder/arm/'PIPELINE_STATUS.json'
        return path.exists() and json.loads(path.read_text()).get('state') == 'complete'
    if all(complete(a) for a in ('serial','joint')): return
    modelpath = Path('/path/to/models')/model
    assert (modelpath/'config.json').is_file() and list(modelpath.glob('*.safetensors'))
    index = modelpath/'model.safetensors.index.json'
    if index.exists():
        assert all((modelpath/n).is_file() for n in set(json.loads(index.read_text())['weight_map'].values()))
    with socket.socket() as sock:
        assert sock.connect_ex(('127.0.0.1',8000)) != 0, 'Port 8000 already occupied'
    env = dict(os.environ,VLLM_USE_FLASHINFER_SAMPLER='0')
    if name == 'qwen38_27b': env['RBAC_QWEN_CLEAN']='1'
    else: env.pop('RBAC_QWEN_CLEAN',None)
    cmd = [VLLM,'serve',str(modelpath),'--served-model-name',model,
           '--host','127.0.0.1','--port','8000','--dtype','bfloat16',
           '--tensor-parallel-size','1','--max-model-len','32768',
           '--gpu-memory-utilization','0.85','--max-num-seqs','8',
           '--max-num-batched-tokens','8192']
    save(JOB/'STATUS.json',dict(state='loading_model',model=name,pid=os.getpid()))
    with (folder/'vllm.log').open('a') as log:
        server = subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        save(folder/'SERVER.json',dict(pid=server.pid,command=cmd))
        try:
            for _ in range(180):
                if server.poll() is not None: raise RuntimeError('vLLM exited; see '+str(folder/'vllm.log'))
                try:
                    data=json.load(urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=3))
                    if any(x['id']==model for x in data['data']): break
                except (OSError,ValueError): pass
                time.sleep(5)
            else: raise RuntimeError('Model readiness timeout')
            for arm in ('serial','joint'):
                if complete(arm): continue
                save(JOB/'STATUS.json',dict(state='generating_and_sql_scoring',model=name,arm=arm,pid=os.getpid()))
                with (folder/(arm+'.pipeline.log')).open('a') as out:
                    subprocess.run([sys.executable,'-u',str(worker),'--arm',arm],cwd=ROOT,
                                   env=env,stdout=out,stderr=subprocess.STDOUT,check=True)
        finally:
            server.terminate()
            try: server.wait(timeout=60)
            except subprocess.TimeoutExpired:
                server.kill(); server.wait()


def main():
    JOB.mkdir(parents=True,exist_ok=True)
    with (JOB/'supervisor.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            for name,model in MODELS.items(): run_model(name,model)
            save(JOB/'STATUS.json',dict(state='sql_evaluation_complete_retention_pending',models=list(MODELS),
                 retention_metric='result_output_completion_permissions_v2_20261105',finished_at=time.time()))
        except BaseException as exc:
            save(JOB/'STATUS.json',dict(state='failed',error=repr(exc),at=time.time()))
            raise


if __name__ == '__main__': main()
