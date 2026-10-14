import unittest

from scripts.control_tls_quiz_questions import _FormParser, _payload, _visible_errors, _plan_questions


class QuizQuestionControlTests(unittest.TestCase):
    def test_payload_keeps_form_defaults_but_only_sends_normal_submit(self):
        parser = _FormParser()
        parser.feed('''<form method="post" action="/question/question.php">
          <input type="hidden" name="sesskey" value="private">
          <input type="hidden" name="fraction[0]" value="0.0">
          <input type="submit" name="submitbutton" value="저장">
          <input type="submit" name="updatebutton" value="계속">
        </form>''')
        payload = _payload(parser, {'fraction[0]': '1.0'})
        self.assertEqual(payload['fraction[0]'], '1.0')
        self.assertEqual(payload['submitbutton'], '저장')
        self.assertNotIn('updatebutton', payload)
        self.assertEqual(payload['sesskey'], 'private')

    def test_visible_error_reader_ignores_hidden_fields(self):
        html = '<input type="hidden" name="sesskey" value="secret"><div class="alert-danger">보기 중 하나는 100%여야 합니다.</div>'
        self.assertEqual(_visible_errors(html), '보기 중 하나는 100%여야 합니다.')

    def test_repeated_form_fields_and_multi_value_plan_are_preserved(self):
        parser = _FormParser()
        parser.feed('''<form method="post" action="/question/question.php">
          <input type="hidden" name="answer[0]" value="첫 답">
          <input type="hidden" name="answer[0]" value="두 번째 답">
          <select name="unit[]" multiple>
            <option value="g" selected>그램</option><option value="kg" selected>킬로그램</option>
          </select>
          <input type="submit" name="submitbutton" value="저장">
        </form>''')
        payload = _payload(parser, {'unit[]': ['kg', 'mg']})
        self.assertEqual(payload['answer[0]'], ['첫 답', '두 번째 답'])
        self.assertEqual(payload['unit[]'], ['kg', 'mg'])
        self.assertEqual(_plan_questions({'quizId': '1', 'questions': [
            {'type': 'multichoice', 'fields': {'answer[0]': ['첫 답', '두 번째 답']}}
        ]}, '1')[0]['fields']['answer[0]'], ['첫 답', '두 번째 답'])


if __name__ == '__main__':
    unittest.main()
