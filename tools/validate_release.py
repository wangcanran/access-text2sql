"""Offline release checks; no external model calls or core code changes."""
import ast, argparse, gzip, hashlib, json, os, re, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
EXCLUDED={'__pycache__','.pytest_cache','.git','.venv','runs'}

def public_files():
    return sorted(p for p in ROOT.rglob('*') if p.is_file() and not any(x in EXCLUDED for x in p.relative_to(ROOT).parts) and p.name!='RELEASE_MANIFEST.json')

def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--write-manifest',action='store_true')
    args=parser.parse_args();errors=[];checks=[];py_count=0
    patterns=[r'sk'+r'-[A-Za-z0-9_-]{24,}',r'gh'+r'p_[A-Za-z0-9]{25,}',r'-----BEGIN '+r'(?:RSA |OPENSSH |EC )?PRIVATE KEY-----']
    for p in public_files():
        rel=p.relative_to(ROOT).as_posix()
        if p.suffix in {'.pem','.key','.sqlite','.sqlite3','.db','.safetensors','.pt','.log'} or (p.name.startswith('.env') and p.name!='.env.example'):
            errors.append(dict(path=rel,issue='private/runtime asset'))
        if p.suffix=='.py':
            py_count+=1
            try:ast.parse(p.read_text(encoding='utf-8-sig'),filename=rel)
            except (SyntaxError,UnicodeError) as exc:errors.append(dict(path=rel,issue='syntax',line=getattr(exc,'lineno',None)))
        if p.suffix in {'.py','.json','.md','.txt','.jsonl','.example','.gz'}:
            try:text=gzip.decompress(p.read_bytes()).decode('utf8') if p.suffix=='.gz' else p.read_text(encoding='utf-8-sig')
            except (UnicodeError,OSError):continue
            for line_no,line in enumerate(text.splitlines(),1):
                if any(re.search(pattern,line) for pattern in patterns):errors.append(dict(path=rel,issue='credential pattern',line=line_no))
    from scripts.run_clarification_v10 import verify_sources
    verify_sources();checks.append(dict(check='main_and_underlying_source_locks',passed=True))
    commands=[[sys.executable,'tools/run_frontend.py','--method',m,'--fixture','examples/synthetic_sales','--out','runs/check_'+m,'--check-only'] for m in ['access','serial','policy','no_clarification','no_pruning','clarify_then_prune']]
    commands += [[sys.executable,'tools/reproduce_tables.py','--out','runs/tables'],[sys.executable,'scripts/predict_rbac_cloud_only.py','--help'],[sys.executable,'data_generation/scripts/generate_flat_cross_dataset.py','--help']]
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
    for cmd in commands:
        proc=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=120)
        checks.append(dict(command=cmd[1:],returncode=proc.returncode))
        if proc.returncode:errors.append(dict(command=cmd[1:],issue=proc.stderr[-1600:]))
    report=dict(python_files_parsed=py_count,checks=checks,errors=errors,passed=not errors,model_API_calls=0)
    (ROOT/'supplementary/provenance/release_validation.json').write_text(json.dumps(report,indent=2)+chr(10),encoding='utf8')
    if args.write_manifest and not errors:
        manifest={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in public_files()}
        (ROOT/'RELEASE_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+chr(10),encoding='utf8')
    print(json.dumps(dict(passed=not errors,python_files=py_count,checks=len(checks),errors=errors)))
    if errors:raise SystemExit(1)

if __name__=='__main__':main()
