import unittest
from unittest.mock import patch

from fastapi import HTTPException

from gameplan.tactics.loading_plan import SAMPLE
from gameplan.core.models import PersonalPlanRequest, PictureAnalysisRequest, CoachAnalysisRequest
from gameplan.tactics.personal_plan import build_personal_plan, LANES
from gameplan.web.picture_analysis import PictureService


class PersonalPlans(unittest.TestCase):
    def setUp(self):
        self.service=PictureService()
        self.service.advance('personal-test',1)
        self.bound=self.service.bind_identity(PictureAnalysisRequest(match_id='personal-test',revision=1,image_base64=''),
            {'complete':True,'verdict':{'top_heroes':SAMPLE['group_a'],'bottom_heroes':SAMPLE['group_b']}})

    def request(self, **changes):
        return PersonalPlanRequest(**{'match_id':'personal-test','revision':1,'identity_id':self.bound['identity_id'],
                                     'side':'b','player':'后羿','lane':'发育路',**changes})

    def test_every_lane_is_respected_and_enemy_labels_only_reference_enemies(self):
        for lane in LANES:
            with self.subTest(lane=lane):
                result=self.service.personal_plan(self.request(lane=lane))
                self.assertEqual(result['lane'],lane)
                self.assertEqual(result['allies'],SAMPLE['group_b'])
                self.assertEqual(result['enemies'],SAMPLE['group_a'])
                self.assertTrue(all(i['hero'] in SAMPLE['group_a'] for i in result['enemy_risks']))
                self.assertTrue(all(i['hero'] in SAMPLE['group_b'] for i in result['ally_coordination']))
                self.assertGreaterEqual(len(result['sections']),7)

    def test_cannot_select_enemy_as_player(self):
        with self.assertRaises(HTTPException) as e:self.service.personal_plan(self.request(side='a'))
        self.assertEqual(e.exception.status_code,422)

    def test_new_image_invalidates_previous_plan(self):
        self.service.advance('personal-test',2)
        for revision in (1,2):
            with self.assertRaises(HTTPException) as e:self.service.personal_plan(self.request(revision=revision))
            self.assertEqual(e.exception.status_code,409)

    def test_cross_session_result_is_rejected(self):
        with self.assertRaises(HTTPException) as e:self.service.personal_plan(self.request(match_id='other'))
        self.assertEqual(e.exception.status_code,404)

    def test_incomplete_identity_does_not_produce_a_specific_plan(self):
        self.service.identities[self.bound['identity_id']]['result']['complete']=False
        with self.assertRaises(HTTPException) as e:self.service.personal_plan(self.request())
        self.assertEqual(e.exception.status_code,422)

    def test_legacy_coach_request_has_lane_default(self):
        self.assertEqual(CoachAnalysisRequest(frame_id='a'*32).lane,'unknown')


if __name__=='__main__':unittest.main()
