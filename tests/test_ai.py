import io
import json
import unittest
from unittest.mock import patch
import ai_explanations
from demo import dataset
from engine import calculate


class ExplanationTests(unittest.TestCase):
    def setUp(self):
        self.rows=calculate(dataset(),{})['rows'][:2]

    def test_no_key_keeps_local_calculation(self):
        with patch.object(ai_explanations,'config',return_value=('', 'test')):
            self.assertEqual(ai_explanations.explain(self.rows),({},'not_configured'))

    def test_structured_response_and_minimal_payload(self):
        body={'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps({
            'explanations':[{'index':i,'text':'Пополнение поддержит спрос.'} for i in range(2)]})}]}]}
        before=json.dumps(self.rows)
        with patch.object(ai_explanations,'config',return_value=('secret', 'test')), patch.object(
                ai_explanations,'urlopen',return_value=io.StringIO(json.dumps(body))) as call:
            result,status=ai_explanations.explain(self.rows)
        self.assertEqual(status,'ready');self.assertEqual(len(result),2)
        sent=json.loads(call.call_args.args[0].data)
        self.assertFalse(sent['store'])
        self.assertNotIn('DEMO',sent['input']);self.assertNotIn('supplier',sent['input'])
        self.assertEqual(json.dumps(self.rows),before)

    def test_timeout_and_bad_response_fall_back(self):
        with patch.object(ai_explanations,'config',return_value=('secret','test')), patch.object(
                ai_explanations,'urlopen',side_effect=TimeoutError):
            self.assertEqual(ai_explanations.explain(self.rows),({},'unavailable'))
        body={'status':'completed','output':[{'content':[{'type':'output_text','text':
            '{"explanations":[{"index":0,"text":"Закажите 999 штук"}]}'}]}]}
        with patch.object(ai_explanations,'config',return_value=('secret','test')), patch.object(
                ai_explanations,'urlopen',return_value=io.StringIO(json.dumps(body))):
            self.assertEqual(ai_explanations.explain(self.rows),({},'unavailable'))
