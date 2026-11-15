import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from .batch_generate import execute, load_cases, RATIOS, read_completed
from .storage import ResponseCache, RunLock, atomic_json


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.args = SimpleNamespace(output=self.root / 'run', model='fixture', review_model='fixture',
                                    seed=42, repair_attempts=1, workers=1, max_cases=None, resume=False)
        self.cases = [dict(question_id=i, db_id='db', question=f'Show item {i}.') for i in (1, 2)]

    def tearDown(self):
        self.temp.cleanup()

    def generator(self, case, args, schemas, out, ratios, fixed, cache):
        trace = {**case, 'status': 'review_passed', 'levels': [
            {'level': i, 'target_retention_ratio': r,
             'status': 'source_identity' if r == 1 else 'review_passed',
             'rewritten_request': case['question'] if r == 1 else 'Show related information.'}
            for i, r in enumerate(ratios)]}
        atomic_json(out / f"{case['question_id']}.json", trace)
        return trace

    def test_three_levels_resume_skip_and_rebuild_partial_export(self):
        self.args.max_cases = 1
        first = execute(self.args, self.cases, {}, self.generator)
        self.assertEqual((first['completed'], first['levels']), (1, 3))
        (self.args.output / 'levels.jsonl').write_text('{truncated', encoding='utf8')
        self.args.resume = True
        self.args.max_cases = None
        seen = []
        def gen(*args):
            seen.append(args[0]['question_id'])
            return self.generator(*args)
        final = execute(self.args, self.cases, {}, gen)
        self.assertEqual(seen, [2])
        self.assertEqual((final['completed'], final['levels']), (2, 6))
        rows = [json.loads(x) for x in (self.args.output / 'levels.jsonl').read_text().splitlines()]
        self.assertEqual(len({(r['source_id'], r['level']) for r in rows}), 6)
        self.assertEqual([r['target_retention_ratio'] for r in rows[:3]], RATIOS)
        execute(self.args, self.cases, {}, lambda *a: self.fail('completed case rerun'))

    def test_reject_changed_contract_and_accidental_overwrite(self):
        execute(self.args, self.cases, {}, self.generator)
        with self.assertRaisesRegex(ValueError, '--resume'):
            execute(self.args, self.cases, {}, self.generator)
        self.args.resume = True
        self.args.model = 'changed'
        with self.assertRaisesRegex(ValueError, 'changed'):
            execute(self.args, self.cases, {}, self.generator)
        self.args.model = 'fixture'
        with self.assertRaisesRegex(ValueError, 'changed'):
            execute(self.args, self.cases, {'changed': True}, self.generator)
        self.cases[0]['question'] = 'Changed input'
        with self.assertRaisesRegex(ValueError, 'changed'):
            execute(self.args, self.cases, {}, self.generator)

    def test_interruption_reuses_paid_response_and_does_not_commit_partial_case(self):
        self.cases = self.cases[:1]
        paid = []
        response = {'choices': [{'message': {'content': '{"answer":1}'}}]}
        def paid_call():
            paid.append(1)
            return response
        def interrupted(*args):
            args[-1]('stage', {'question': 'q'}, paid_call)
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            execute(self.args, self.cases, {}, interrupted)
        self.assertEqual(read_completed(self.args.output, self.cases), {})
        self.args.resume = True
        def resumed(*args):
            if args[0]['question_id'] == 1:
                self.assertEqual(args[-1]('stage', {'question': 'q'}, paid_call), response)
            return self.generator(*args)
        execute(self.args, self.cases, {}, resumed)
        self.assertEqual(paid, [1])

    def test_needs_review_is_complete_and_errors_retry(self):
        def gen(*args):
            trace = self.generator(*args)
            if trace['question_id'] == 1:
                trace['status'] = 'needs_review'
                trace['levels'][0]['status'] = 'needs_review'
            else:
                trace.update(status='error', error='network unavailable')
            atomic_json(args[3] / f"{trace['question_id']}.json", trace)
            return trace
        first = execute(self.args, self.cases, {}, gen)
        self.assertEqual((first['needs_review'], first['errors']), (1, 1))
        self.args.resume = True
        seen = []
        def resumed(*args):
            seen.append(args[0]['question_id'])
            return self.generator(*args)
        execute(self.args, self.cases, {}, resumed)
        self.assertEqual(seen, [2])

    def test_incomplete_three_level_result_cannot_commit(self):
        def gen(*args):
            trace = self.generator(*args)
            trace['levels'].pop()
            return trace
        with self.assertRaisesRegex(ValueError, 'three levels'):
            execute(self.args, self.cases, {}, gen)
        self.assertEqual(read_completed(self.args.output, self.cases), {})

    def test_corrupt_committed_record_refused(self):
        execute(self.args, self.cases, {}, self.generator)
        path = self.args.output / 'completed/1.json'
        row = json.loads(path.read_text())
        row['trace']['question'] = 'changed'
        atomic_json(path, row)
        self.args.resume = True
        with self.assertRaisesRegex(ValueError, 'checksum'):
            execute(self.args, self.cases, {}, self.generator)

    def test_input_projection_and_duplicate_validation(self):
        path = self.root / 'input.json'
        atomic_json(path, [{**self.cases[0], 'SQL': 'SECRET SQL', 'evidence': 'SECRET'}])
        self.assertEqual(load_cases(path), self.cases[:1])
        atomic_json(path, [self.cases[0], self.cases[0]])
        with self.assertRaises(ValueError):
            load_cases(path)

    def test_cache_invalid_json_retries_and_request_changes_miss(self):
        cache = ResponseCache(self.root / 'cache')
        bad = {'choices': [{'message': {'content': 'not JSON'}}]}
        good = {'choices': [{'message': {'content': '{}'}}]}
        with self.assertRaises(ValueError):
            cache('x', {'model': 'a'}, lambda: bad)
        self.assertEqual(cache('x', {'model': 'a'}, lambda: good), good)
        self.assertEqual(cache('x', {'model': 'a'}, lambda: self.fail('cache miss')), good)
        self.assertEqual(cache('x', {'model': 'b'}, lambda: good), good)
        self.assertEqual(cache.calls, 2)

    def test_lock_excludes_second_writer_and_releases(self):
        path = self.root / 'lock'
        with RunLock(path):
            with self.assertRaises(ValueError):
                with RunLock(path):
                    self.fail('lock shared')
        with RunLock(path):
            pass


if __name__ == '__main__':
    unittest.main()
