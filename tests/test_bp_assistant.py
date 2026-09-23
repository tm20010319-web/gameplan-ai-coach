import unittest
from pydantic import ValidationError
from gameplan.tactics.bp_assistant import BPSelection, BPObservation, player_lane_from_slots, recommend


class BPTests(unittest.TestCase):
    def request(self, **changes):
        return BPSelection(**{'allies':[],'enemies':['关羽'],'banned':[],
            'available':['金蝉','老夫子'],'lane':'中路','confirmed':True,**changes})

    def test_draft_relation_and_lane_filter(self):
        result=recommend(self.request())
        self.assertEqual([i['hero'] for i in result['items']],['金蝉'])
        self.assertIn('待审核',result['items'][0]['relation'])

    def test_excludes_banned_selected_and_unavailable(self):
        for changes in ({'banned':['金蝉']},{'allies':['金蝉']},{'available':[]}):
            self.assertEqual(recommend(self.request(**changes))['items'],[])

    def test_lock_and_confirmation(self):
        self.assertEqual(recommend(self.request(confirmed=False))['items'],[])
        result=recommend(self.request(allies=['金蝉'],locked='金蝉'))
        self.assertEqual(result['status'],'locked')
        self.assertEqual(result['items'],[])

    def test_invalid_names_and_cross_team_duplicates(self):
        for changes in ({'available':['不存在']},{'allies':['关羽']},{'locked':'金蝉'}):
            with self.assertRaises(ValidationError):self.request(**changes)

    def test_observation_rejects_unknown_and_duplicates(self):
        for left in (['不存在'],['关羽','关羽']):
            with self.assertRaises(ValidationError):
                BPObservation(phase='bp',left=left,right=[],banned=[],confidence=.9,note='')

    def test_player_lane_uses_personal_row_or_unique_missing_lane(self):
        def item(text, y):
            import numpy as np
            return {'text':text, 'score':.99, 'box':np.array([[0,y],[1,y],[1,y+1],[0,y+1]])}
        self.assertEqual(player_lane_from_slots([item('个人',50),item('发育路',51)],100)[0],'发育路')
        labels=[item('对抗路',10),item('游走',30),item('发育路',50),item('中路',70),item('个人',90)]
        self.assertEqual(player_lane_from_slots(labels,100),('打野','其他四名队友分路排除'))
