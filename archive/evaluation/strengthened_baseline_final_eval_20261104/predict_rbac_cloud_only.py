"""Use original cloud predictor without importing unrelated trainer configs."""
import runpy,sys,types
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if Path('/path/to/access').is_dir():ROOT=Path('/path/to/access')
pkg=types.ModuleType('configs');pkg.__path__=[str(ROOT/'rbac-exp/configs')];pkg.__package__='configs'
sys.modules['configs']=pkg
runpy.run_path(str(ROOT/'scripts/predict_rbac_endpoint.py'),run_name='__main__')
