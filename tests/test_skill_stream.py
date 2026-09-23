import io,json
import pytest
from unittest.mock import patch
from gameplan.ai.integrations import post_json


class Response(io.BytesIO):
    def __init__(self, pieces, timeout=False):
        super().__init__(b''.join(json.dumps(p).encode()+b'\n' for p in pieces));self.timeout=timeout
    def readline(self, *args):
        value=super().readline(*args)
        if not value and self.timeout:raise TimeoutError()
        return value


def test_stream_keeps_answer_tokens_when_final_review_times_out():
    response=Response([{'message':{'content':'{"events":[],'},'done':False}],timeout=True)
    with patch('urllib.request.urlopen',return_value=response):
        answer=post_json('http://localhost/api/chat',{'stream':True},1)
    assert answer['message']['content']=='{"events":[],'
    assert answer['partial'] is True


def test_complete_stream_preserves_final_stop_and_assembled_json():
    response=Response([{'message':{'content':'{"events":'},'done':False},
                       {'message':{'content':'[]}'},'done':True,'done_reason':'stop'}])
    with patch('urllib.request.urlopen',return_value=response):
        answer=post_json('http://localhost/api/chat',{'stream':True},1)
    assert answer['message']['content']=='{"events":[]}' and answer['done_reason']=='stop'


@pytest.mark.parametrize('prefix',['','{','{"ev','{"events":','{"events":[{"hero":"铠"',
                                   '{"description":"quoted events: [{ fake }]", "reviews":'])
def test_incomplete_or_quoted_objects_never_create_an_event(prefix):
    from gameplan.skills.partial_skill_answer import completed_fields
    assert completed_fields(prefix)['events']==[]
