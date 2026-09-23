import json
import unittest
from unittest.mock import patch
from gameplan.tactics.personal_plan import analyze_personal_plan, text_model_config
from gameplan.tactics.loading_plan import SAMPLE


class ExternalPlans(unittest.TestCase):
    def test_qwen_does_not_reuse_deepseek_secret(self):
        with patch('dotenv.dotenv_values', return_value={'COACH_PROVIDER':'qwen'}), patch.dict('os.environ', {}, clear=True):
            cfg = text_model_config()
        self.assertEqual(cfg['model'], 'qwen-plus')
        self.assertEqual(cfg['key'], '')

    def test_generated_summary_and_wrong_team_fallback(self):
        raw = {'summary':'鲁班七号面对韩信需要跟随赵云保护，确认侧翼安全后再推进。',
               'sections':[{'title':t, 'text':'确认队友能够跟进后再行动，避免独自深入。'} for t in ['开局','支援','团战']],
               'enemy_risks':[{'hero':'韩信','text':'韩信位置不明时不要独自向前推进。'}],
               'ally_coordination':[{'hero':'赵云','text':'赵云能够跟进时再衔接输出并推塔。'}],
               'evidence_ids':['F1','F2','F3']}
        cfg = {'key':'test', 'base':'https://example.invalid', 'model':'test', 'provider':'deepseek'}
        with patch('gameplan.tactics.personal_plan.text_model_config', return_value=cfg), patch('gameplan.ai.integrations.post_json') as post:
            post.return_value = {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(raw)}}]}
            result = analyze_personal_plan(SAMPLE['group_a'], SAMPLE['group_b'], '鲁班七号', '发育路')
            self.assertEqual(result['source'], 'external_model')
            payload = post.call_args.args[1]
            self.assertEqual(payload['thinking'], {'type':'disabled'})
            self.assertNotIn('image_base64', json.dumps(payload))
            sent=json.loads(payload['messages'][1]['content'])
            self.assertEqual(sent['mechanisms'], {})
            self.assertIn('鲁班七号',result['knowledge_gaps'])
            original_text=raw['sections'][0]['text']
            raw['sections'][0]['text']='出门学二技能，使用技能推进兵线后再支援。'
            post.return_value['choices'][0]['message']['content']=json.dumps(raw)
            rejected=analyze_personal_plan(SAMPLE['group_a'], SAMPLE['group_b'], '鲁班七号', '发育路')
            self.assertEqual(rejected['source'],'local_lane_rules')
            raw['sections'][0]['text']=original_text
            raw['evidence_ids']=['K999']
            post.return_value['choices'][0]['message']['content']=json.dumps(raw)
            rejected=analyze_personal_plan(SAMPLE['group_a'], SAMPLE['group_b'], '鲁班七号', '发育路')
            self.assertEqual(rejected['source'],'local_lane_rules')
            raw['evidence_ids']=['F1']
            raw['enemy_risks'][0]['hero'] = '赵云'
            post.return_value['choices'][0]['message']['content'] = json.dumps(raw)
            result = analyze_personal_plan(SAMPLE['group_a'], SAMPLE['group_b'], '鲁班七号', '发育路')
            self.assertEqual(result['source'], 'local_lane_rules')
            self.assertIn('fallback_reason', result)

    def test_timeout_falls_back(self):
        with patch('gameplan.tactics.personal_plan.text_model_config', return_value={'key':'test','base':'https://example.invalid','model':'test'}), patch('gameplan.ai.integrations.post_json', side_effect=TimeoutError):
            result = analyze_personal_plan(SAMPLE['group_a'], SAMPLE['group_b'], '鲁班七号', '发育路')
        self.assertEqual(result['error_type'], 'TimeoutError')
