import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.scoring import analyze_intent, RULE_VERSION

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

    def test_score_is_followup_assist_metadata(self):
        result = analyze_intent('想报国考班，想预约试听')
        self.assertEqual(result['score_role'], 'followup_assist')
        self.assertEqual(result['classifier'], 'rules')
        self.assertEqual(result['rule_version'], RULE_VERSION)
        self.assertIn(result['confidence'], {'low', 'medium', 'high'})
        self.assertTrue(result['uncertainty'])


class ScoringRiskTests(unittest.TestCase):
    """High-intent false positive/negative, contact rejection, after-sales isolation."""

    def test_phone_withdrawal_forms(self):
        for text in ['别打电话给我', '不要再打电话', '不用打电话了', '请别再联系我了', '停止联系我']:
            with self.subTest(text=text):
                result = analyze_intent(text, True)
                self.assertFalse(result['contactable'])
                self.assertIn('已停止联系', result['tags'])

    def test_exam_quit_without_baoming_keyword(self):
        for text in ['不打算考事业单位了', '事业编不打算考了', '不考事业单位了']:
            with self.subTest(text=text):
                result = analyze_intent(text)
                self.assertEqual(result['purchase_state'], 'declined')
                self.assertEqual(result['score'], 0)
                self.assertNotEqual(result['level'], '高意向')

    def test_price_inquiry_is_mid_not_ignored(self):
        for text in ['想问一下国考班价格', '国考班收费标准是多少']:
            with self.subTest(text=text):
                result = analyze_intent(text)
                self.assertEqual(result['level'], '中意向')
                self.assertIn('主动询价', result['tags'])
                self.assertNotEqual(result['level'], '高意向')

    def test_named_exam_trial_not_under_scored(self):
        result = analyze_intent('不是不想试听，是想预约国考试听')
        self.assertEqual(result['level'], '中意向')
        self.assertIn('预约试听', result['tags'])
        self.assertNotEqual(result['level'], '高意向')

    def test_after_sales_then_new_purchase_in_same_turn(self):
        result = analyze_intent('投诉之后还想报国考班，想报价')
        self.assertEqual(result['purpose'], '课程咨询')
        self.assertIn('售后服务', result['purposes'])
        self.assertIn(result['level'], {'中意向', '高意向'})
        self.assertNotEqual(result['level'], '不适用')

    def test_pure_after_sales_transfer_not_sales_high(self):
        result = analyze_intent('已经报名了，想问能不能转班到省考班')
        self.assertEqual(result['purpose'], '售后服务')
        self.assertEqual(result['level'], '不适用')
        self.assertEqual(result['score'], 0)

    def test_soft_explore_not_high_and_hard_reject_stays_declined(self):
        explore = analyze_intent('先不报名，只是问问国考课程介绍')
        self.assertEqual(explore['purchase_state'], 'exploring')
        self.assertNotEqual(explore['level'], '高意向')
        rejected = analyze_intent('只是了解一下国考，暂时不报名')
        self.assertEqual(rejected['purchase_state'], 'declined')

    def test_interest_rejection_forms(self):
        for text in ['我对报名不感兴趣', '没兴趣报名', '我不想报了', '目前不考虑报班']:
            with self.subTest(text=text):
                result = analyze_intent(text)
                self.assertEqual(result['purchase_state'], 'declined')
                self.assertNotEqual(result['level'], '高意向')

    def test_do_not_cancel_enrollment_is_not_rejection(self):
        result = analyze_intent('不要取消我的报名，国考还想报')
        self.assertNotEqual(result['purchase_state'], 'declined')
        self.assertIn('国考', result['active_exam_types'])

    def test_multi_exam_partial_reject_keeps_other_high_path_safe(self):
        result = analyze_intent(
            '公安联考和国考都想了解，国考先不报了，公安联考想预约试听和报价，预算3000，下个月考试'
        )
        self.assertIn('公安联考', result['active_exam_types'])
        self.assertNotIn('国考', result['active_exam_types'])
        self.assertEqual(result['level'], '高意向')

    def test_high_intent_still_requires_action_bundle(self):
        weak = analyze_intent('准备选调生，近期考试，预算5000元以内')
        self.assertNotEqual(weak['level'], '高意向')
        strong = analyze_intent('想报选调生班，预算5000元，下个月考试，想预约试听和报价')
        self.assertEqual(strong['level'], '高意向')
