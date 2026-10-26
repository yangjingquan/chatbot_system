import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.scoring import analyze_intent

class ScoringTests(unittest.TestCase):
    def test_unknown_stays_unknown(self):
        self.assertEqual(analyze_intent('你好')['level'], '待判定')

    def test_negative_does_not_trigger_purchase(self):
        result = analyze_intent('我不想预约试听，也不需要报价，不打算报名')
        self.assertNotIn('预约试听', result['tags'])
        self.assertNotEqual(result['level'], '高意向')

    def test_after_sales_is_not_sales(self):
        result = analyze_intent('我要退费，不是来报名的')
        self.assertEqual(result['purpose'], '售后服务')
        self.assertEqual(result['level'], '不适用')

    def test_evidence_and_contact_are_separate(self):
        result = analyze_intent('想报公安联考班，预算2000元，下个月考试，想预约试听和报价')
        self.assertEqual(result['level'], '高意向')
        self.assertIn('公安联考', result['tags'])
        self.assertTrue(result['evidence'])
        self.assertFalse(result['contactable'])

    def test_repeated_quote_counts_once(self):
        self.assertEqual(analyze_intent('想要报价，报价，报价')['score'], 25)
