"""Re-review a saved attempt without regenerating or changing its source audit."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from openai import OpenAI
from intent_refinement_bird_v2.llm_client import load_dotenv
from .pipeline import validate_review
from .prompts import REVIEW_PROMPT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('trace', type=Path)
    parser.add_argument('--stage', required=True, help='Exact saved review call stage')
    parser.add_argument('--model', default='gpt-5.5')
    args = parser.parse_args()
    source = json.loads(args.trace.read_text(encoding='utf8'))
    calls = [c for c in source['calls'] if c['stage'] == args.stage]
    if len(calls) != 1:
        parser.error('stage must identify exactly one saved call')
    payload = json.loads(calls[0]['request']['messages'][1]['content'])
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / 'clause_frame_experiment/.env')
    out = root / 'artifacts/flat_disclosure_review' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True)
    request = {'model':args.model,'temperature':0,'response_format':{'type':'json_object'},
               'messages':[{'role':'system','content':REVIEW_PROMPT},
                           {'role':'user','content':json.dumps(payload,ensure_ascii=False)}]}
    audit = {'source_trace':str(args.trace.resolve()),'source_stage':args.stage,'request':request}
    path = out / 'review.json'
    def save():
        path.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8')
    save()
    try:
        client = OpenAI(api_key=os.environ['OPENAI_API_KEY'],base_url=os.getenv('OPENAI_BASE_URL'),timeout=120,max_retries=1)
        response = client.chat.completions.create(**request)
        audit['response'] = response.model_dump(mode='json'); save()
        review = json.loads(response.choices[0].message.content)
        directions = {'directions':[{'item_id':iid,'action':'hide'} for iid in payload['hide_ids']]}
        audit['review_passed'] = validate_review(review,directions,payload['protected_ids'])
        audit['review'] = review
    except Exception as exc:
        audit['error'] = str(exc).replace(os.environ.get('OPENAI_API_KEY','__unset__'),'<redacted>')
        save()
        raise
    save()
    print(json.dumps({'path':str(path),'review_passed':audit['review_passed'],'issues':review['issues']},ensure_ascii=False))


if __name__ == '__main__':
    main()
