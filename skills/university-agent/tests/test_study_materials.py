"""Offline checks for local, source-cited lecture material extraction."""
from pathlib import Path
import shutil
import sys
import unittest
import zipfile

from test_project import ProjectTestBase, snapshot, upsert
from storage.local_db import LocalDatabase
from features.study_materials import study_materials


class StudyMaterialsTests(ProjectTestBase):
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
