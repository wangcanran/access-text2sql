"""Run flat disclosure on LiveSQL/Spider with an isolated frame prompt."""
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flat_disclosure_experiment import batch_generate, live_smoke


def main():
    original = live_smoke.DISCLOSURE_FRAME_PROMPT
    marker = '# Original Request\n[[user_request]]\n'
    if original.count(marker) != 1:
        raise ValueError('Unexpected source prompt layout')
    tailored = original.replace(
        marker,
        'Copy original_text exactly from the request below. Do not append '
        'a newline or other whitespace.\n\n# Original Request\n[[user_request]]')
    live_smoke.DISCLOSURE_FRAME_PROMPT = tailored
    batch_generate.PROMPTS['frame'] = tailored
    batch_generate.main()


if __name__ == '__main__':
    main()
