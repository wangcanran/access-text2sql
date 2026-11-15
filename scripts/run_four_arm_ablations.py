"""Execute the preregistered four-arm variants with the locked main runner."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_module_ablations as adapter
from clarification_ablation_v1.four_arm import FourArmMethod, VARIANTS, VERSION

original_configuration = adapter.configuration
original_sources = adapter.sources

def configuration(args, cases):
    cfg = original_configuration(args, cases)
    cfg.update(no_clarification_semantics='no_user_calls_original_internal_permission_routing',
               serial_semantics='clarify_with_authorized_menus_then_prune_no_return_to_user',
               construction='actual_lifecycle_not_historical_prefix_reconstruction')
    return cfg

def sources():
    result = original_sources()
    result['scripts/run_four_arm_ablations.py'] = adapter.shared.sha(Path(__file__))
    return result

if __name__ == '__main__':
    adapter.ModuleAblationMethod = FourArmMethod
    adapter.VARIANTS = VARIANTS
    adapter.VERSION = VERSION
    adapter.configuration = configuration
    adapter.sources = sources
    adapter.main()
