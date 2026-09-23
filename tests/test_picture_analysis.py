import asyncio
import base64
from contextlib import nullcontext
import io
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

import gameplan.web.monitor_app as monitor_app
import gameplan.web.picture_analysis as pa
from gameplan.core.models import PictureAnalysisRequest, ROI
import gameplan.monitoring.screen_capture as screen_capture


def picture(color='navy'):
    buf=io.BytesIO();Image.new('RGB',(200,120),color).save(buf,format='PNG')
    return base64.b64encode(buf.getvalue()).decode()


def verdict():
    return {'phase':'loading','top_heroes':['小乔'],'bottom_heroes':['蔡文姬'],'uncertainty':[],
            'summary':'第一组先处理兵线再支援，团战注意保护输出位置，击退对手后推进。',
            'uncertain_summary':'先处理兵线再与队友支援，避免独自探草，队友到位后再接团，击退后争取推塔资源。',
            'watch_for':['第一组避免独自探草'],'opportunities':['第一组处理兵线后支援']}


def external(*args):
    return {'verdict':verdict(),'source':'deepseek_vision','model':'actual-provider-model','requested_model':'vision-alias'}


LOCAL={'model':'qwen3-vl:8b','observation':{'phase':'loading','ally_roster':['小乔'],'enemy_roster':['蔡文姬']}}
CONFIG={'key':'unit-test-key','base':'https://api.deepseek.com','model':'vision-alias'}


class PictureBackendTests(unittest.TestCase):
    def test_fixed_crop_precedes_downscale_and_does_not_include_outside_pixels(self):
        source={'id':'a'*16,'width':3840,'height':2160,'bbox':[0,0,3840,2160],'label':'4K'}
        original=Image.new('RGB',(3840,2160),'red');ImageDraw.Draw(original).rectangle((960,540,1439,809),fill='blue')
        roi=ROI(x=.25,y=.25,width=.125,height=.125)
        with patch('gameplan.monitoring.screen_capture.displays',return_value=[source]),patch('gameplan.monitoring.screen_capture.physical_pixels',return_value=nullcontext()),patch('gameplan.monitoring.screen_capture.ImageGrab.grab',return_value=original):
            frame=screen_capture.capture(source['id'],roi)
        self.assertEqual((frame['width'],frame['height']),(480,270))
        crop=Image.open(io.BytesIO(base64.b64decode(frame['image_base64'].split(',')[1])))
        self.assertLess(crop.getpixel((0,0))[0],5);self.assertGreater(crop.getpixel((479,269))[2],245)

    def test_picture_routes_require_local_origin_and_nonempty_roi(self):
        with TestClient(monitor_app.app,base_url='http://127.0.0.1',client=('127.0.0.1',5000)) as client:
            self.assertEqual(client.post('/api/picture/frame',json={'source_id':'a'*16}).status_code,422)
            self.assertEqual(client.post('/api/picture/frame',json={'source_id':'a'*16,'roi':{'x':.9,'y':0,'width':.2,'height':.5}}).status_code,422)
            payload={'image_base64':picture(),'match_id':'test'}
            self.assertEqual(client.post('/api/picture/analysis',json=payload,headers={'origin':'https://elsewhere.example'}).status_code,403)
        with TestClient(monitor_app.app,base_url='http://127.0.0.1',client=('192.168.1.10',5000)) as client:
            self.assertEqual(client.get('/api/picture/status').status_code,403)

    def test_real_multimodal_payload_has_selected_image_and_actual_model(self):
        req=PictureAnalysisRequest(image_base64=picture())
        response={'model':'actual-model','choices':[{'finish_reason':'stop','message':{'content':json.dumps(verdict(),ensure_ascii=False)}}]}
        with patch.object(pa,'config',return_value=CONFIG),patch.object(pa,'post_json',return_value=response) as call:
            result=pa.external_analysis(req,base64.b64decode(picture()),LOCAL)
        payload=call.call_args.args[1];parts=payload['messages'][1]['content']
        self.assertTrue(any(part['type']=='image_url' and part['image_url']['url'].startswith('data:image/jpeg;base64,') for part in parts))
        self.assertNotIn('qwen_candidates',parts[0]['text'])
        self.assertEqual(result['model'],'actual-model');self.assertNotIn(CONFIG['key'],json.dumps(result))

    def test_invalid_or_truncated_hero_output_is_not_presented(self):
        req=PictureAnalysisRequest(image_base64=picture())
        for edited,finish in [({**verdict(),'top_heroes':['不存在英雄']},'stop'),(verdict(),'length')]:
            response={'choices':[{'finish_reason':finish,'message':{'content':json.dumps(edited)}}]}
            with patch.object(pa,'config',return_value=CONFIG),patch.object(pa,'post_json',return_value=response),self.assertRaises(HTTPException) as caught:
                pa.external_analysis(req,base64.b64decode(picture()),None)
            self.assertEqual(caught.exception.status_code,502)


class PictureFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service=pa.PictureService()
        self.cfg=patch.object(pa,'config',return_value=CONFIG);self.cfg.start();self.addCleanup(self.cfg.stop)

    def request(self,revision=1,**kwargs):
        return PictureAnalysisRequest(match_id='test',revision=revision,image_base64=picture(),**kwargs)

    async def test_identical_picture_and_perspective_cache_but_changed_picture_runs_again(self):
        with patch.object(pa,'observe',new=AsyncMock(return_value=LOCAL)) as local,patch.object(pa,'external_analysis',side_effect=external) as ext:
            first=await self.service.analyze(self.request());second=await self.service.analyze(self.request(2))
            changed=self.request(3).model_copy(update={'image_base64':picture('green')});await self.service.analyze(changed)
        self.assertFalse(first['cached']);self.assertTrue(second['cached']);self.assertEqual(ext.call_count,2);self.assertEqual(local.call_count,2)
        self.assertNotIn('image_base64',str(self.service.cache))

    async def test_picture_changed_during_qwen_never_starts_external_call(self):
        started=asyncio.Event();finish=asyncio.Event()
        async def local(_):started.set();await finish.wait();return LOCAL
        with patch.object(pa,'observe',side_effect=local),patch.object(pa,'external_analysis') as ext:
            task=asyncio.create_task(self.service.analyze(self.request()));await started.wait()
            self.service.advance('test',2);finish.set()
            with self.assertRaises(HTTPException) as caught:await task
            self.assertEqual(caught.exception.status_code,409);ext.assert_not_called()

    async def test_busy_request_does_not_queue_images_and_stop_invalidates_results(self):
        async with self.service.gate:
            with self.assertRaises(HTTPException) as caught:await self.service.analyze(self.request())
            self.assertEqual(caught.exception.status_code,429)
        self.service.advance('test',3)
        with self.assertRaises(HTTPException) as caught:await self.service.analyze(self.request(2))
        self.assertEqual(caught.exception.status_code,409)

    async def test_local_failure_still_allows_honestly_labelled_external_vision(self):
        with patch.object(pa,'observe',new=AsyncMock(side_effect=HTTPException(503,'offline'))),patch.object(pa,'external_analysis',side_effect=external):
            result=await self.service.analyze(self.request())
        self.assertIsNone(result['qwen_model']);self.assertEqual(result['source'],'deepseek_vision')

    async def test_conflicting_identification_uses_external_generic_advice_until_corrected(self):
        local={'model':'qwen','observation':{'ally_roster':['后羿','韩信'],'enemy_roster':['铠','貂蝉']}}
        with patch.object(pa,'observe',new=AsyncMock(return_value=local)),patch.object(pa,'external_analysis',side_effect=external):
            result=await self.service.analyze(self.request())
        self.assertTrue(result['requires_review']);self.assertEqual(result['verdict']['summary'],verdict()['uncertain_summary']);self.assertEqual(result['verdict']['watch_for'],[])

    async def test_manual_lineup_is_checked_and_never_silently_overridden(self):
        bad=self.request().model_copy(update={'player':'用户昵称'})
        with self.assertRaises(HTTPException) as caught:await self.service.analyze(bad)
        self.assertEqual(caught.exception.status_code,422)

    async def test_low_resolution_without_local_pass_requires_review(self):
        with patch.object(pa,'observe',new=AsyncMock(side_effect=HTTPException(503,'offline'))),patch.object(pa,'external_analysis',side_effect=external):
            result=await self.service.analyze(self.request())
        self.assertTrue(result['requires_review'])
        self.assertEqual(result['verdict']['summary'],verdict()['uncertain_summary'])
