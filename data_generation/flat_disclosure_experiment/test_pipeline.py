import unittest
from clause_frame_experiment.model import parse_query_frame
from .live_smoke import select_flat, validate_directions, validate_obligations
from .pipeline import apply_roles, counted_items, normalize_shared_operators, plan_levels, source_ledger, protected_ledger, protected_text_issues, replacement_semantic_issues, apply_replacements, requires_clause_rewrite, source_span_diagnostics, validate_review


class ContractTests(unittest.TestCase):
    def test_direction_source_is_bound_by_code(self):
        frame=parse_query_frame({'original_text':'List football player.',
            'outputs':[{'item_id':'o1','span':'football player'}],'modifiers':[]})
        row={'item_id':'o1','action':'hide','direction':'generalize category','retain':'list results'}
        normalized=validate_directions({'directions':[row]},['o1'],frame)
        self.assertEqual(normalized['directions'][0]['source_specification'],'football player')
        self.assertNotIn('source_specification',row)
        preserved=validate_directions({'directions':[{**row,'action':'context_only'}]},['o1'],frame)
        self.assertEqual(preserved['directions'][0]['source_specification'],'')

    def test_conflict_diagnostics_do_not_rewrite_or_guess_repeated_positions(self):
        frame=parse_query_frame({'original_text':'Show names sorted by salary.',
            'outputs':[{'item_id':'o1','span':'names'},{'item_id':'o2','span':'salary'}],
            'modifiers':[{'item_id':'m1','span':'sorted by salary'}]})
        self.assertEqual(source_span_diagnostics(frame),[{
            'item_ids':['o2','m1'],'problem':'overlapping source positions','shared_text':'salary'}])
        repeated=parse_query_frame({'original_text':'Show names and names.',
            'outputs':[{'item_id':'o1','span':'names'},{'item_id':'o2','span':'names'}],
            'modifiers':[]})
        self.assertEqual(source_span_diagnostics(repeated),[])

    def test_replacements_preserve_all_other_characters(self):
        frame=parse_query_frame({'original_text':'Show names and ages in Paris.',
            'outputs':[{'item_id':'o1','span':'names'},{'item_id':'o2','span':'ages'}],
            'modifiers':[{'item_id':'m1','span':'in Paris'}]})
        directions={'directions':[{'item_id':'o1','action':'hide'},
                                  {'item_id':'m1','action':'context_only'}]}
        raw={'replacements':[{'item_id':'o1','replacement':'contact details'}]}
        result=apply_replacements(frame,directions,raw)
        self.assertEqual(result['rewritten_request'],'Show contact details and ages in Paris.')
        self.assertEqual(result['edits'][0]['start'],5)
        for rows in ([],raw['replacements']*2,[{'item_id':'o2','replacement':'x'}],
                     [{'item_id':'o1','replacement':''}]):
            with self.assertRaises(ValueError):
                apply_replacements(frame,directions,{'replacements':rows})

    def test_replacement_order_and_exact_recovery(self):
        frame=parse_query_frame({'original_text':'Return names before 2020.',
            'outputs':[{'item_id':'o1','span':'names'}],
            'modifiers':[{'item_id':'m1','span':'before 2020'}]})
        directions={'directions':[{'item_id':'o1','action':'hide'},{'item_id':'m1','action':'hide'}]}
        result=apply_replacements(frame,directions,{'replacements':[
            {'item_id':'m1','replacement':'during a relevant period'},
            {'item_id':'o1','replacement':'personal information'}]})
        self.assertEqual(result['rewritten_request'],'Return personal information during a relevant period.')
        self.assertEqual(apply_replacements(frame,{'directions':[]},{'replacements':[]})['rewritten_request'],frame.original_text)

    def test_protected_wording_and_duplicate_occurrences(self):
        protected=[{'item_id':'o1','span':'names'},{'item_id':'m1','span':'names'}]
        self.assertEqual(protected_text_issues('Return names, order by names.',protected),[])
        self.assertTrue(protected_text_issues('Return names.',protected))
        self.assertTrue(protected_text_issues('Return identifiers.',protected))
        self.assertEqual(protected_text_issues('Anything.',[]),[])

    def test_protection_covers_every_unselected_fragment(self):
        frame=parse_query_frame({'original_text':'Show names and ages in Paris.',
            'outputs':[{'item_id':'o1','span':'names'},{'item_id':'o2','span':'ages'}],
            'modifiers':[{'item_id':'m1','span':'in Paris'}]})
        self.assertEqual(protected_ledger(frame,['o1']),[
            {'item_id':'o2','span':'ages'},{'item_id':'m1','span':'in Paris'}])
        self.assertEqual(protected_ledger(frame,[]),list(frame.outputs+frame.modifiers))
        with self.assertRaises(ValueError): protected_ledger(frame,['missing'])

    def test_redundant_context_excluded_from_budget(self):
        frame=parse_query_frame({'original_text':'Show details including names and ages.',
            'outputs':[{'item_id':'o1','span':'details'},{'item_id':'o2','span':'names'},
                       {'item_id':'o3','span':'ages'}],'modifiers':[]})
        inventory={'context_items':[{'item_id':'o1','source_span':'details','covered_by':['o2','o3'],
                                     'reason':'Introduction to the two named attributes'}]}
        self.assertEqual([i['item_id'] for i in counted_items(frame,inventory)],['o2','o3'])
        plan=plan_levels(frame,[0,0.5,1],inventory=inventory)
        self.assertEqual([l['denominator'] for l in plan['levels']],[2,2,2])
        self.assertTrue(all('o1' not in l['selected_ids'] for l in plan['levels']))
        with self.assertRaises(ValueError):
            counted_items(frame,{'context_items':[{**inventory['context_items'][0],'source_span':'Show'}]})
        inventory['context_items'].append({'item_id':'o2','source_span':'names','covered_by':['o3'],'reason':'bad coverage chain'})
        with self.assertRaises(ValueError): counted_items(frame,inventory)

    def test_nested_reproducible_budgets_and_source_specifications(self):
        frame=parse_query_frame({'original_text':'Show names in Paris before 2020.',
            'outputs':[{'item_id':'o1','span':'names'}],
            'modifiers':[{'item_id':'m1','span':'in Paris'},{'item_id':'m2','span':'before 2020'}]})
        plan=plan_levels(frame,[0,1/3,2/3,1])
        self.assertEqual(plan,plan_levels(frame,[0,1/3,2/3,1]))
        self.assertEqual([l['numerator'] for l in plan['levels']],[0,1,2,3])
        for left,right in zip(plan['levels'],plan['levels'][1:]):
            self.assertTrue(set(right['selected_ids']) <= set(left['selected_ids']))
        self.assertEqual(source_ledger(frame,['m1']),[{'item_id':'m1','source_specification':'in Paris'}])
        row={'item_id':'m1','action':'hide','source_specification':'Paris','direction':'unspecified location','retain':'location constraint'}
        validate_directions({'directions':[row]},['m1'],frame)
        with self.assertRaises(ValueError):
            validate_directions({'directions':[{**row,'source_specification':'London'}]},['m1'],frame)

    def test_review_coverage_and_uncertainty(self):
        directions={'directions':[{'item_id':'m1','action':'hide'}]}
        raw={'checks':[{'item_id':'m1','result':'hidden','evidence':'Location unspecified'},
                       {'item_id':'o1','result':'preserved','evidence':'Name still requested'}],'issues':[]}
        self.assertTrue(validate_review(raw,directions,['o1']))
        raw['checks'][0]['result']='uncertain'
        self.assertFalse(validate_review(raw,directions,['o1']))
        raw['checks'].pop()
        with self.assertRaises(ValueError): validate_review(raw,directions,['o1'])

    def test_sorting_does_not_make_a_returned_attribute_redundant(self):
        frame=parse_query_frame({'original_text':'Return count, sort by count.',
            'outputs':[{'item_id':'o1','span':'count'}],
            'modifiers':[{'item_id':'m1','span':'sort by count'}]})
        with self.assertRaises(ValueError):
            counted_items(frame,{'context_items':[{'item_id':'o1','source_span':'count','covered_by':['m1'],
                          'reason':'same measure used in ordering'}]})

    def test_overlapping_fragments_can_be_selected(self):
        frame=parse_query_frame({'original_text':'Show the school with high scores.', 'outputs':[{'item_id':'o1','span':'the school'}], 'modifiers':[{'item_id':'m1','span':'the school with high scores'}]})
        self.assertEqual(select_flat(frame,1,42),['o1','m1'])
        self.assertEqual(select_flat(frame,.5,42),select_flat(frame,.5,42))
        with self.assertRaises(ValueError):
            plan_levels(frame,[0,1/3,2/3,1])

    def test_model_cannot_change_selected_set(self):
        row={'item_id':'o1','action':'hide','source_specification':'name','direction':'generalize field','retain':'customer'}
        validate_directions({'directions':[row]},['o1'])
        for rows in ([],[row,row],[{**row,'item_id':'o2'}]):
            with self.assertRaises(ValueError): validate_directions({'directions':rows},['o1'])
        validate_directions({'directions':[{**row,'action':'context_only','source_specification':''}]},['o1'])

    def test_source_obligations_cover_every_fragment_once(self):
        frame=parse_query_frame({'original_text':'Return names in Paris.',
            'outputs':[{'item_id':'o1','span':'names'}],
            'modifiers':[{'item_id':'m1','span':'in Paris'}]})
        valid={'obligations':[{'item_id':'o1','requirement':'Return names'},
                              {'item_id':'m1','requirement':'Restrict records to Paris'}]}
        self.assertEqual(validate_obligations(valid,frame),valid)
        for rows in (valid['obligations'][:1],valid['obligations']*2,
                     [{**valid['obligations'][0],'requirement':''},valid['obligations'][1]],
                     [{**valid['obligations'][0],'item_id':'missing'},valid['obligations'][1]]):
            with self.assertRaises(ValueError):
                validate_obligations({'obligations':rows},frame)

    def test_shared_operator_stays_outside_enumerated_operands(self):
        frame=parse_query_frame({'original_text':'Return the number of orders, refunds, and shipments.',
            'outputs':[{'item_id':'o1','span':'the number of orders'},
                       {'item_id':'o2','span':'refunds'},
                       {'item_id':'o3','span':'shipments'}], 'modifiers':[]})
        obligations={'obligations':[{'item_id':'o1','requirement':'Return the number of orders'},
                                   {'item_id':'o2','requirement':'Return the number of refunds'},
                                   {'item_id':'o3','requirement':'Return the number of shipments'}]}
        updated, changes=normalize_shared_operators(frame,obligations)
        self.assertEqual(updated.outputs[0]['span'],'orders')
        self.assertEqual(changes[0]['shared_operator'],'the number of')
        self.assertEqual(normalize_shared_operators(updated,obligations)[1],[])
        result=apply_replacements(updated,{'directions':[{'item_id':'o1','action':'hide'}]},
                                  {'replacements':[{'item_id':'o1','replacement':'related transactions'}]})
        self.assertEqual(result['rewritten_request'],
                         'Return the number of related transactions, refunds, and shipments.')
        independent=parse_query_frame({'original_text':'Return the number of orders and total sales.',
            'outputs':[{'item_id':'o1','span':'the number of orders'},
                       {'item_id':'o2','span':'total sales'}], 'modifiers':[]})
        independent_obligations={'obligations':[obligations['obligations'][0],
            {'item_id':'o2','requirement':'Return total sales'}]}
        self.assertEqual(normalize_shared_operators(independent,independent_obligations)[1],[])

    def test_bare_interrogative_paraphrase_does_not_count_as_hiding(self):
        edit={'item_id':'o1','kind':'output','span':'which customer',
              'replacement':'what single customer'}
        self.assertTrue(replacement_semantic_issues([edit]))
        self.assertEqual(replacement_semantic_issues([{**edit,'replacement':'which account holder'}]),[])

    def test_wh_output_cannot_become_declarative_question(self):
        edit={'item_id':'o1','kind':'output','span':'how many schools',
              'replacement':'the schools'}
        self.assertTrue(replacement_semantic_issues([edit],
            'Among eligible schools, the schools are funded?'))
        self.assertEqual(replacement_semantic_issues([edit],
            'Among eligible schools, what information is available?'),[])

    def test_hidden_count_and_predicate_across_copula_need_clause_rewrite(self):
        frame=parse_query_frame({'original_text':'How many orders are delayed?',
            'outputs':[{'item_id':'o1','span':'How many orders'}],
            'modifiers':[{'item_id':'m1','span':'delayed'}]})
        both={'directions':[{'item_id':'o1','action':'hide'},
                            {'item_id':'m1','action':'hide'}]}
        self.assertTrue(requires_clause_rewrite(frame,both))
        self.assertFalse(requires_clause_rewrite(frame,{'directions':[
            {'item_id':'o1','action':'hide'},{'item_id':'m1','action':'context_only'}]}))

    def test_role_review_reclassifies_selector_without_losing_spans(self):
        frame=parse_query_frame({'original_text':'Which customer has the largest balance?',
            'outputs':[{'item_id':'o1','span':'Which customer'},
                       {'item_id':'o2','span':'the largest balance'}],
            'modifiers':[]})
        roles={'roles':[{'item_id':'o1','role':'output'},
                        {'item_id':'o2','role':'modifier'}]}
        checked, changes=apply_roles(frame,roles)
        self.assertEqual([i['span'] for i in checked.outputs],['Which customer'])
        self.assertEqual([i['span'] for i in checked.modifiers],['the largest balance'])
        self.assertEqual(changes[-1]['new_id'],'m1')
        with self.assertRaises(ValueError):
            apply_roles(frame,{'roles':roles['roles'][:1]})


if __name__=='__main__': unittest.main()
