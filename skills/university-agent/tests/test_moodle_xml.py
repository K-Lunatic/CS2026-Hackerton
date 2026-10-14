import json
import base64
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from features.moodle_xml import MoodleXmlError, exam_to_xml, export_saved_exam, reference_to_xml


class MoodleXmlTests(unittest.TestCase):
    def question(self, kind='mcq'):
        value = {
            'type': kind,
            'question': '정렬 조건을 확인하시오.',
            'answer': '정렬된 배열',
            'explanation': '자료 근거 해설',
            'concept': '이진 탐색',
            'rubric': ['정렬 조건'],
            'points': 2,
        }
        if kind == 'mcq':
            value['options'] = ['정렬된 배열', '연결 리스트']
        if kind == 'short':
            value['acceptedAnswers'] = ['정렬된 배열', 'sorted array']
        if kind == 'code_fix':
            value['code'] = 'int low = 0;'
        return value

    def test_supported_types_and_custom_types_are_converted(self):
        payload, report = exam_to_xml({'questions': [
            self.question('mcq'), self.question('short'), self.question('essay'), self.question('code_fix')
        ]})
        root = ET.fromstring(payload)
        self.assertEqual([node.attrib['type'] for node in root.findall('question')],
                         ['category', 'multichoice', 'shortanswer', 'essay', 'essay'])
        self.assertEqual(report['downgraded'], [{'index': 4, 'type': 'code_fix', 'as': 'essay'}])
        self.assertIn('&lt;pre&gt;int low = 0;&lt;/pre&gt;', payload.decode())

    def test_invalid_multiple_choice_is_rejected(self):
        question = self.question('mcq')
        question['answer'] = '없는 선택지'
        with self.assertRaisesRegex(MoodleXmlError, '정답이 선택지'):
            exam_to_xml({'questions': [question]})

    def test_observed_moodle_question_types_keep_their_native_type(self):
        cases = []
        for kind in ('multichoiceset', 'calculatedmulti'):
            question = self.question(kind)
            question.update(options=['정렬된 배열', '연결 리스트'], correctAnswers=['정렬된 배열'])
            cases.append(question)
        cases.append({**self.question('truefalse'), 'answer': 'true'})
        cases.append({**self.question('numerical'), 'answer': '3.14', 'tolerance': 0.01})
        cases.append({**self.question('match'), 'pairs': [
            {'prompt': '배열', 'answer': '연속 메모리'}, {'prompt': '이진 탐색', 'answer': '정렬 필요'}]})
        cases.append({**self.question('calculated'), 'answer': '{x}+1', 'formula': '{x}+1', 'tolerance': 0.01})
        cases.append({**self.question('calculatedsimple'), 'answer': '{x}+1', 'formula': '{x}+1'})
        cases.append({**self.question('randomsamatch'), 'choose': 2})
        cases.append({**self.question('multianswer'), 'question': '정렬된 배열에서 {1:SHORTANSWER:%100%이진 탐색}을 사용한다.'})
        cases.append({**self.question('description'), 'answer': ''})
        payload, report = exam_to_xml({'questions': cases})
        root = ET.fromstring(payload)
        self.assertEqual(report['downgraded'], [])
        self.assertEqual([node.attrib['type'] for node in root.findall('question')][1:],
                         ['multichoiceset', 'calculatedmulti', 'truefalse', 'numerical', 'match',
                          'calculated', 'calculatedsimple', 'randomsamatch', 'cloze', 'description'])

    def test_leak_like_visible_names_are_rejected_before_export(self):
        question = self.question()
        question['name'] = '자료구조 기출문제'
        with self.assertRaisesRegex(MoodleXmlError, '과거 시험 유출'):
            exam_to_xml({'questions': [question]})

    def test_saved_exam_export_does_not_need_tls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / 'study-sessions.db'
            output = root / 'exam.xml'
            import sqlite3
            with sqlite3.connect(database_path) as database:
                database.execute('CREATE TABLE study_exams (user TEXT, exam_id TEXT, state TEXT)')
                database.execute('INSERT INTO study_exams VALUES (?,?,?)',
                                 ('student', 'exam-1', json.dumps({'questions': [self.question()]})))
            result = export_saved_exam(database_path, 'student', 'exam-1', output)
            self.assertEqual(result['questionCount'], 1)
            self.assertTrue(output.is_file())

    def test_images_are_embedded_and_referenced_as_plugin_files(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'diagram.png'
            image.write_bytes(b'fake-png')
            question = self.question()
            question['images'] = [{'path': str(image), 'alt': '배열 그림'}]
            payload, report = exam_to_xml({'questions': [question]})
            root = ET.fromstring(payload)
            file_node = root.find('./question[2]/questiontext/file')
            self.assertIsNotNone(file_node)
            self.assertEqual(base64.b64decode(file_node.text), b'fake-png')
            self.assertIn('@@PLUGINFILE@@/', payload.decode())
            self.assertEqual(report['assetCount'], 1)

    def test_user_reference_is_neutral_description_with_original_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / '자료구조_기출.txt'
            source.write_text('중요 개념과 참고 내용\n', encoding='utf-8')
            output = root / 'reference.xml'
            report = reference_to_xml(source, output)
            parsed = ET.parse(output).getroot()
            self.assertEqual(parsed.findall('question')[1].attrib['type'], 'description')
            name = parsed.findtext('./question[2]/name/text')
            self.assertNotIn('기출', name)
            file_node = parsed.find('./question[2]/questiontext/file')
            self.assertEqual(base64.b64decode(file_node.text), source.read_bytes())
            self.assertEqual(report['sourceType'], 'user_reference')
            self.assertFalse(report['textTruncated'])


if __name__ == '__main__':
    unittest.main()
