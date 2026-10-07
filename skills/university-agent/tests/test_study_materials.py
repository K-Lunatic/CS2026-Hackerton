"""Offline checks for local, source-cited lecture material extraction."""
from pathlib import Path
import shutil
import sys
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from test_project import ProjectTestBase, snapshot, upsert
from storage.local_db import LocalDatabase
from features.study_materials import _docx_sections, _hwpx_sections, _pdf_sections, _text_sections, study_materials, attached_material, original_files


class StudyMaterialsTests(ProjectTestBase):
    def test_attachment_format_invalidates_analysis_from_old_text_decoder(self):
        from features.study_pipeline import analysis_key
        path = self.files / 'lecture.txt'
        path.write_bytes('한글 본문'.encode('cp949'))
        material = attached_material(str(path), cache_root=self.files.parent / 'cache')
        self.assertEqual(material['extension'], 'txt')
        self.assertEqual(material['sections'][0]['text'], '한글 본문')
        state = {'settings': {'choices': 4, 'difficulty': '보통', 'types': ['auto']},
                 'selection': {'courseId': 'user-attachment'}}
        legacy = {k: v for k, v in material.items() if k != 'extension'}
        self.assertNotEqual(analysis_key(state, material), analysis_key(state, legacy))

    def test_original_files_are_real_paths_and_prohibited_or_escaped_paths_are_not_exposed(self):
        cli_result = self.cli('resource-file', '--course', '자료구조', '--resource', '3주차')
        self.assertEqual(cli_result['data']['files'][0]['path'], str(self.pptx.resolve()))
        courses = [{'id': 'course-1', 'name': '자료구조'}]
        item = {'id': 'r1', 'courseId': 'course-1', 'title': '자료', 'fileName': self.pptx.name,
                'localPath': str(self.pptx), 'downloadStatus': 'DOWNLOADED'}
        result = original_files(courses, [item], files_root=self.files, course_query='자료구조')
        self.assertEqual(result['data']['files'][0]['path'], str(self.pptx.resolve()))
        prohibited = original_files(courses, [{**item, 'downloadStatus': 'PROHIBITED', 'downloadReason': '다운로드 금지'}], files_root=self.files)
        self.assertNotIn('path', prohibited['data']['files'][0])
        escaped = original_files(courses, [{**item, 'localPath': str(self.db_path)}], files_root=self.files)
        self.assertNotIn('path', escaped['data']['files'][0])
        self.assertEqual(original_files(courses, [item], files_root=self.files, resource_id='other-user-resource')['data']['files'], [])

    def test_windows_pdf_fallback_uses_pdftotext_pages(self):
        path = Path(self.temp.name) / 'lecture.pdf'
        path.write_bytes(b'%PDF-fixture')
        with patch('features.study_materials.PdfReader', None), patch('features.study_materials.sys.platform', 'win32'), patch('features.study_materials.shutil.which', side_effect=lambda name: 'pdftotext.exe' if name == 'pdftotext' else None), patch('features.study_materials.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='첫 장\f둘째 장')):
            pages = _pdf_sections([path], Path('unused.swift'), Path(self.temp.name) / 'cache')
        self.assertEqual([page['location'] for page in pages[str(path.resolve())]], ['PDF p.1', 'PDF p.2'])

    def test_pdf_fallback_and_missing_tool_are_not_misreported_as_scan(self):
        pdf = self.files / 'course-1' / 'lecture.pdf'
        pdf.write_bytes(self._pdf_with_text('Portable PDF'))
        with patch('features.study_materials.PdfReader', side_effect=ValueError('bad font')), patch('features.study_materials.shutil.which', side_effect=lambda name: 'pdftotext' if name == 'pdftotext' else None), patch('features.study_materials.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='복구된 본문\f')):
            self.assertEqual(attached_material(str(pdf))['sections'][0]['text'], '복구된 본문')
        with patch('features.study_materials.PdfReader', None), patch('features.study_materials.shutil.which', return_value=None):
            self.assertEqual(attached_material(str(pdf))['sections'][0]['text'], '복구된 본문')
            pdf.write_bytes(self._pdf_with_text('Changed PDF'))
            with self.assertRaisesRegex(ValueError, 'pip install pypdf'):
                attached_material(str(pdf))

    def test_empty_reader_retries_other_engine(self):
        pdf = self.files / 'course-1' / 'lecture.pdf'
        pdf.write_bytes(self._pdf_with_text('Portable PDF'))
        reader = SimpleNamespace(is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: '')])
        with patch('features.study_materials.PdfReader', return_value=reader), patch('features.study_materials.shutil.which', side_effect=lambda name: 'pdftotext' if name == 'pdftotext' else None), patch('features.study_materials.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='대체 추출 성공')):
            self.assertEqual(attached_material(str(pdf))['sections'][0]['text'], '대체 추출 성공')

    def test_portable_reader_real_pdf_without_swift_or_poppler(self):
        from features.study_materials import PdfReader
        if PdfReader is None:
            self.skipTest('Install requirements.txt to check portable PDF extraction')
        pdf = self.files / 'course-1' / 'lecture.pdf'
        pdf.write_bytes(self._pdf_with_text('Portable PDF'))
        with patch('features.study_materials.sys.platform', 'win32'), patch('features.study_materials.shutil.which', return_value=None), patch('features.study_materials.subprocess.run') as run:
            self.assertIn('Portable PDF', attached_material(str(pdf))['sections'][0]['text'])
            run.assert_not_called()

    @staticmethod
    def _pdf_with_text(text):
        stream = b"BT /F1 12 Tf 72 720 Td (" + text.encode("ascii") + b") Tj ET\n"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream",
        ]
        data = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for number, obj in enumerate(objects, 1):
            offsets.append(len(data))
            data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
        xref = len(data)
        data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
        data.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
        data.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
        return bytes(data)

    def setUp(self):
        super().setUp()
        self.files = self.db_path.parent / "files"
        self.pptx = self.files / "course-1" / "week-03.pptx"
        self.pptx.parent.mkdir(parents=True)
        with zipfile.ZipFile(self.pptx, "w") as archive:
            archive.writestr(
                "ppt/slides/slide1.xml",
                '<p:sld xmlns:p="urn:p" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                '<a:p><a:r><a:t>이진 탐색</a:t></a:r></a:p>'
                '<a:p><a:r><a:t>정렬된 배열에서 탐색한다.</a:t></a:r></a:p></p:sld>',
            )
        db = LocalDatabase(self.db_path)
        data = snapshot()
        data["courses"][0]["name"] = "자료구조"
        data["resources"] = [dict(data["resources"][0], title="3주차 탐색", fileName="week-03.pptx", extension="pptx", localPath=str(self.pptx))]
        upsert(db, "fixture-user", data)
        db.close()

    def test_course_file_text_is_returned_with_slide_location(self):
        result = self.cli("study-materials", "--course", "자료구조", "--resource", "3주차")
        material = result["data"]["materials"][0]
        self.assertEqual(material["sections"][0]["location"], "슬라이드 1")
        self.assertIn("정렬된 배열", material["sections"][0]["text"])
        self.assertNotIn("resource-1", str(result))
        self.assertNotIn(str(self.pptx), str(result))

    def test_study_materials_accepts_more_than_three_files(self):
        db = LocalDatabase(self.db_path)
        data = snapshot()
        data["courses"][0]["name"] = "자료구조"
        data["resources"] = []
        for index in range(4):
            path = self.files / "course-1" / f"week-{index}.txt"
            path.write_text(f"자료 {index}의 핵심 내용", encoding="utf-8")
            data["resources"].append(dict(snapshot()["resources"][0], id=f"resource-{index}",
                                           title=f"자료 {index}", fileName=path.name, extension="txt",
                                           localPath=str(path), downloadStatus="DOWNLOADED"))
        upsert(db, "fixture-user", data)
        db.close()
        result = self.cli("study-materials", "--course", "자료구조")
        self.assertFalse(result.get("needsInput"))
        self.assertEqual(len(result["data"]["materials"]), 4)

    def test_text_docx_and_hwpx_sections(self):
        root = self.files / "formats"
        root.mkdir()
        text_path = root / "Example.java"
        text_path.write_text("class Example {}", encoding="utf-8")
        self.assertEqual(_text_sections(text_path)[0]["text"], "class Example {}")

        docx_path = root / "Example.docx"
        with zipfile.ZipFile(docx_path, "w") as archive:
            archive.writestr("word/document.xml", "<document><body><p><r><t>워드 본문</t></r></p></body></document>")
        self.assertEqual(_docx_sections(docx_path)[0]["text"], "워드 본문")

        hwpx_path = root / "Example.hwpx"
        with zipfile.ZipFile(hwpx_path, "w") as archive:
            archive.writestr("Contents/section0.xml", "<section><p><t>한글 본문</t></p></section>")
        self.assertEqual(_hwpx_sections(hwpx_path)[0]["text"], "한글 본문")

    def test_ambiguous_course_requests_names_without_reading_files(self):
        db = LocalDatabase(self.db_path)
        data = snapshot("2")
        data["courses"][0]["name"] = "알고리즘"
        data["courses"].append({"id": "course-1", "name": "자료구조", "source": "tls"})
        data["resources"] = []
        upsert(db, "fixture-user", data)
        db.close()
        result = self.cli("study-materials")
        self.assertTrue(result["needsInput"])
        self.assertEqual(set(result["data"]["courses"]), {"자료구조", "알고리즘"})

    def test_files_outside_the_private_download_root_are_rejected(self):
        db = LocalDatabase(self.db_path)
        data = snapshot()
        data["resources"] = [dict(data["resources"][0], localPath=str(Path(self.temp.name) / "outside.pptx"))]
        upsert(db, "fixture-user", data)
        db.close()
        result = self.cli("study-materials", "--course", "테스트 과목")
        self.assertIn("로컬 파일이 없습니다", result["data"]["materials"][0]["error"])

    def test_prohibited_resource_is_reported_without_opening_a_local_file(self):
        db = LocalDatabase(self.db_path)
        data = snapshot()
        data["resources"] = [dict(data["resources"][0], fileName="week-03.pptx", extension="pptx", localPath=str(self.pptx), downloadStatus="PROHIBITED", downloadReason="과목 공지에 다운로드 금지가 표시됨")]
        upsert(db, "fixture-user", data)
        db.close()
        result = self.cli("study-materials", "--course", "테스트 과목")
        material = result["data"]["materials"][0]
        self.assertIn("다운로드 금지", material["error"])
        self.assertEqual(material["sections"], [])
        self.assertIn("테스트 자료", result["answer"])

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("swift"), "macOS PDFKit required")
    def test_pdfkit_extracts_page_text(self):
        pdf = self.files / "course-1" / "lecture.pdf"
        pdf.write_bytes(self._pdf_with_text("Turtle study source"))
        db = LocalDatabase(self.db_path)
        data = snapshot()
        data["courses"][0]["name"] = "자료구조"
        data["resources"] = [dict(data["resources"][0], title="강의 노트", fileName="lecture.pdf", extension="pdf", localPath=str(pdf))]
        upsert(db, "fixture-user", data)
        db.close()
        db = LocalDatabase(self.db_path, read_only=True)
        self.addCleanup(db.close)
        result = study_materials(db.get_courses("fixture-user"), db.get_resources("fixture-user"), files_root=self.files, course_query="자료구조")
        material = result["data"]["materials"][0]
        self.assertTrue(material["sections"], material)
        self.assertEqual(material["sections"][0]["location"], "PDF p.1")
        self.assertIn("Turtle study source", material["sections"][0]["text"])
