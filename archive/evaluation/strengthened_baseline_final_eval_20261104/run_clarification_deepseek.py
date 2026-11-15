"""Run the frozen v6 clarification mechanism through DeepSeek's API."""
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PROFILE = ROOT / 'clarification_main/.env.deepseek'
BASE_URL = 'https://api.deepseek.com/v1'
MODELS = {'deepseek-chat', 'deepseek-reasoner', 'deepseek-flash', 'deepseek-v4-pro'}


def configure():
    if PROFILE.is_file():
        for line in PROFILE.read_text(encoding='utf8').splitlines():
            if line.startswith('DEEPSEEK_API_KEY='):
                os.environ.setdefault('DEEPSEEK_API_KEY', line.split('=', 1)[1].strip())
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        raise SystemExit('Set DEEPSEEK_API_KEY or provide clarification_main/.env.deepseek')
    os.environ['OPENAI_API_KEY'] = key
    os.environ['OPENAI_BASE_URL'] = BASE_URL
    os.environ['CLARIFICATION_DEEPSEEK_THINKING'] = 'disabled'


def install_v4_non_thinking(recorder_class):
    """Patch only transport options in the frozen runtime; preserve its logic."""
    original = recorder_class.request_options

    def request_options(self, stage):
        options = original(self, stage)
        if self.model in ('deepseek-v4-pro', 'deepseek-flash'):
            options['extra_body'] = {'thinking': {'type': 'disabled'}}
        return options

    recorder_class.request_options = request_options


def main():
    configure()
    args = sys.argv[1:]
    if '--model' in args:
        index = args.index('--model')
        if index + 1 >= len(args) or args[index + 1] not in MODELS:
            raise SystemExit('Choose --model from: ' + ', '.join(sorted(MODELS)))
        model = args[index + 1]
    else:
        model = 'deepseek-v4-pro'
        args.extend(['--model', model])
    if '--probe' in args:
        args.remove('--probe')
        if args != ['--model', model]:
            raise SystemExit('--probe only accepts --model')
        from openai import OpenAI
        response = OpenAI(api_key=os.environ['OPENAI_API_KEY'], base_url=BASE_URL,
                          timeout=30, max_retries=0).chat.completions.create(
            model=model, temperature=0, response_format={'type': 'json_object'},
            **({'extra_body': {'thinking': {'type': 'disabled'}}}
               if model in ('deepseek-v4-pro', 'deepseek-flash') else {}),
            messages=[{'role': 'system', 'content': 'Return only JSON.'},
                      {'role': 'user', 'content': 'Return {"ready":true} as JSON.'}])
        content = json.loads(response.choices[0].message.content)
        if content.get('ready') is not True:
            raise SystemExit('DeepSeek JSON response did not satisfy probe')
        message = response.choices[0].message
        usage = response.usage.model_dump() if response.usage else {}
        print(json.dumps({'provider': 'deepseek', 'requested_model': model,
                          'response_model': response.model, 'json_mode': 'ok',
                          'has_reasoning_content': bool(getattr(message, 'reasoning_content', None)),
                          'usage': usage}))
        return
    from clarification_useful_residual_v6.live import main as live_main
    if model == 'deepseek-v4-pro':
        from clarification_deferred_deletion.baseline import module
        install_v4_non_thinking(module('live').Recorder)
    sys.argv = [sys.argv[0], *args]
    live_main()


if __name__ == '__main__':
    main()
