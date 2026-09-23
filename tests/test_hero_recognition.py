import asyncio
import base64
import io
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

from gameplan.vision.hero_recognition import combine, locate_labels, name_in_label, read_names
from gameplan.core.models import PictureAnalysisRequest
from gameplan.web.picture_analysis import PictureService


def labels():
    return [{'slot':i+1, 'row':i//5, 'column':i%5+1, 'box':[0,0,20,10], 'ocr_hero':None} for i in range(10)]


class IdentityTests(unittest.TestCase):
    def test_only_complete_hero_suffix_counts_as_a_name(self):
        names={'妲己','蔡文姬','孙悟空'}
        self.assertEqual(name_in_label('蔷薇王座 蔡文姬', names), '蔡文姬')
        self.assertIsNone(name_in_label('我是妲己玩家', names))
        self.assertIsNone(name_in_label('SNK', names))

    def test_conflict_and_missing_name_keep_their_slots(self):
        items=labels();items[0]['ocr_hero']='蔡文姬'
        result=combine(items,['妲己','', '铠']+['']*7)
        self.assertEqual(len(result),10)
        self.assertIsNone(result[0]['hero']);self.assertEqual(result[0]['source'],'conflict')
        self.assertEqual(result[2]['slot'],3);self.assertEqual(result[2]['hero'],'铠')

    def test_duplicate_predictions_are_not_silently_deduplicated(self):
        result=combine(labels(),['铠','铠']+['']*8)
        self.assertTrue(all(s['hero'] is None for s in result))
        self.assertEqual([s['source'] for s in result[:2]],['duplicate','duplicate'])

    def test_layout_does_not_assume_all_images_are_loading_cards(self):
        self.assertEqual(locate_labels([], (1920,1080), {'铠'}),[])

    def test_nonhero_output_is_rejected_even_if_provider_ignores_schema(self):
        import json
        response={'message':{'content':json.dumps({'heroes':['SNK']+['']*9})}}
        with patch('gameplan.vision.hero_recognition.post_json',return_value=response), self.assertRaises(ValueError):
            read_names(Image.new('RGB',(100,100)), 'test', {'铠'})


class IdentityServiceTests(unittest.IsolatedAsyncioTestCase):
    def request(self, revision=1):
        buf=io.BytesIO();Image.new('RGB',(100,100)).save(buf,format='PNG')
        return PictureAnalysisRequest(match_id='identity-test',revision=revision,image_base64=base64.b64encode(buf.getvalue()).decode())

    async def test_identity_never_calls_external_advice_and_incomplete_is_retryable(self):
        service=PictureService()
        detected={'slots':[], 'complete':False, 'model':'qwen', 'phase':'unknown', 'note':'待确认'}
        with patch('gameplan.vision.hero_recognition.recognize',return_value=detected) as run, patch('gameplan.web.picture_analysis.external_analysis') as external:
            first=await service.recognize(self.request());await service.recognize(self.request(2))
        external.assert_not_called();self.assertEqual(run.call_count,2)
        self.assertEqual(first['verdict']['watch_for'],[]);self.assertFalse(first['complete'])

    async def test_stale_revision_is_rejected_before_inference(self):
        service=PictureService();service.advance('identity-test',3)
        with patch('gameplan.vision.hero_recognition.recognize') as run,self.assertRaises(HTTPException) as caught:
            await service.recognize(self.request())
        self.assertEqual(caught.exception.status_code,409);run.assert_not_called()

    async def test_live_confirmation_does_not_reuse_cached_read(self):
        service=PictureService()
        from gameplan.tactics.loading_plan import SAMPLE
        slots=combine(labels(),SAMPLE['group_a']+SAMPLE['group_b'])
        detected={'slots':slots,'complete':True,'model':'qwen','phase':'loading','note':'ok'}
        with patch('gameplan.vision.hero_recognition.recognize',return_value=detected) as run:
            await service.recognize(self.request())
            cached=await service.recognize(self.request(2))
            self.assertTrue(cached['cached'])
            live=await service.recognize(self.request(3).model_copy(update={'input_kind':'live'}))
        self.assertEqual(run.call_count,2)
        self.assertFalse(live['cached'])


if __name__=='__main__':
    unittest.main()
