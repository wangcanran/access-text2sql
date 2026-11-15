"""Run the direct-input RBAC baseline with DeepSeek Pro or Flash.

Use the same structured, zero-shot prompt and input rows as the local baselines.
The API key is loaded locally and is never written into the run manifest.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import argparse


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def configure() -> None:
    profile = ROOT / 'clarification_main' / '.env.deepseek'
    if profile.is_file():
        for line in profile.read_text(encoding='utf8').splitlines():
            if line.startswith('DEEPSEEK_API_KEY='):
                os.environ.setdefault('DEEPSEEK_API_KEY', line.split('=', 1)[1].strip())
    if not os.environ.get('DEEPSEEK_API_KEY'):
        raise SystemExit('Set DEEPSEEK_API_KEY or provide clarification_main/.env.deepseek')
    os.environ['RBAC_API_KEY'] = os.environ['DEEPSEEK_API_KEY']
    os.environ['RBAC_API_BASE'] = 'https://api.deepseek.com/v1'


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--deepseek-model', choices=('deepseek-v4-pro', 'deepseek-flash'),
                        default='deepseek-v4-pro')
    profile, remaining = parser.parse_known_args(sys.argv[1:])
    if '--model' in remaining or '--client' in remaining:
        parser.error('Use --deepseek-model; model/client are pinned by this wrapper')
    configure()
    from scripts.run_manual_flat_gold_qwen import main as run
    sys.argv = [sys.argv[0], *remaining, '--model', profile.deepseek_model,
                '--client', 'deepseek']
    run()


if __name__ == '__main__':
    main()
