import unittest
from app.scoring import analyze_intent, analyze_messages

PURCHASE='想报公安联考班，预算2000元，下个月考试，想预约试听和报价'

class IntentStateTests(unittest.TestCase):
    def test_polite_messages_never_restore_cancelled_interest(self):
        result=analyze_intent(PURCHASE+'\n我不打算报名了，不用联系我\n好的谢谢',True)
        self.assertEqual(result['purchase_state'],'declined')
        self.assertEqual(result['score'],0)
        self.assertFalse(result['contactable'])

    def test_cancellation_survives_more_than_eight_turns(self):
        result=analyze_intent(PURCHASE+'\n不打算报名了\n'+'谢谢\n'*12)
        self.assertEqual(result['purchase_state'],'declined')

    def test_new_explicit_interest_does_not_restore_old_budget(self):
        result=analyze_intent(PURCHASE+'\n不打算报名了\n我又想报名公安联考课程，想先试听')
        self.assertNotEqual(result['purchase_state'],'declined')
        self.assertNotIn('预算明确',result['tags'])
        self.assertLess(result['score'],70)

    def test_new_course_after_refund_is_not_stuck_in_after_sales(self):
        result=analyze_intent('我要退费\n另外想报国考基础班，预算1500元，下个月考试，想预约试听')
        self.assertEqual(result['purpose'],'课程咨询')
        self.assertIn('售后服务',result['purposes'])
        self.assertEqual(result['level'],'高意向')

    def test_exam_switch_keeps_history_without_active_old_exam(self):
        result=analyze_intent(PURCHASE+'\n不考公安联考了，改考国考，想了解国考课程')
        self.assertEqual(result['active_exam_types'],['国考'])
        self.assertTrue(any(n['exam_type']=='公安联考' and n['state']!='active' for n in result['needs']))

    def test_multiple_exams_have_separate_needs_and_scores(self):
        result=analyze_intent('准备国考，想预约试听\n也准备省考，预算1500元\n国考不打算报名了')
        self.assertIn('省考',result['active_exam_types'])
        self.assertNotIn('国考',result['active_exam_types'])
        self.assertTrue(any(n['exam_type']=='国考' and n['state']=='declined' for n in result['needs']))

    def test_evidence_retains_message_identity(self):
        result=analyze_messages([{'id':'question-1','content':'准备国考，想预约试听'}])
        self.assertTrue(all(e.get('message_id')=='question-1' for e in result['evidence']))

    def test_trial_cancellation_does_not_cancel_purchase(self):
        result=analyze_intent('想报国考课程，想预约试听\n取消试听，但还是想报名国考课程')
        self.assertNotEqual(result['purchase_state'],'declined')
        self.assertNotIn('预约试听',result['tags'])
