import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features import analysis_records, study_pack
from features.guidance import guidance_request
from storage.local_db import LocalDatabase


class StudyPackTests(unittest.TestCase):
    def test_guidance_routes_pack_requests_without_exposing_storage_details(self):
        exported = guidance_request("분석한 자료구조 학습 세트를 다른 기기로 보내줘")
        imported = guidance_request("받은 학습 팩 가져와")
        self.assertTrue(exported["needsStudyPackExport"])
        self.assertIn(".tpack", exported["nextCommands"][0])
        self.assertTrue(imported["needsStudyPackImport"])
        self.assertNotIn("sqlite", exported["answer"].lower())

    def test_exports_analysis_without_original_file_or_credentials_and_imports_idempotently(self):
        course = {"id": "course-1", "externalId": "course-1", "name": "자료구조", "professor": "교수", "semester": "2026-2"}
        resource = {
            "id": "resource-1", "externalId": "resource-1", "courseId": "course-1",
            "title": "배열", "fileName": "array.pdf", "extension": "pdf", "mimeType": "application/pdf",
            "remotePath": "/resource/1", "downloadStatus": "DOWNLOADED", "source": "tls",
        }
        source = {
            "resourceId": "resource-1", "courseId": "course-1", "name": "array.pdf",
            "location": "PDF p.1", "text": "배열은 같은 자료형의 값을 연속으로 저장한다.",
        }
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            left, right = Path(left), Path(right)
            with closing(LocalDatabase(left / "university.db")) as db:
                db.upsert_tls_snapshot("student", [course], [], [], "student", None, resources=[resource])
            analysis_records.save(left, "student", [source], {}, "concepts", {
                "concepts": [{"concept": "배열", "explanation": "연속 저장", "evidence": [source]}]
            })
            with sqlite3.connect(left / "study-sessions.db") as db:
                db.execute("CREATE TABLE study_exams (user TEXT, exam_id TEXT, conversation TEXT, created_at TEXT, title TEXT, question_count INTEGER, phase TEXT, score REAL, state TEXT, PRIMARY KEY(user,exam_id))")
                db.execute("INSERT INTO study_exams VALUES (?,?,?,?,?,?,?,?,?)", ("student", "exam-1", "chat", "2026-10-07", "자료구조", 1, "finished", 10, json.dumps({"sources": [{"courseId": "course-1"}]})))
                db.commit()
            pack_path = left / "자료구조.tpack"
            exported = study_pack.export_pack(left, "student", pack_path, courses=[course], resources=[resource])
            self.assertFalse(exported["containsOriginalFiles"])
            self.assertFalse(exported["containsCredentials"])
            with closing(LocalDatabase(right / "university.db")) as db:
                first = study_pack.import_pack(right, "local-user", pack_path, database=db)
                second = study_pack.import_pack(right, "local-user", pack_path, database=db)
                self.assertEqual(first["analyses"], 1)
                self.assertEqual(second["analyses"], 0)
                self.assertEqual(db.get_courses("local-user")[0]["name"], "자료구조")
                self.assertEqual(db.get_resources("local-user")[0]["localPath"], None)
            self.assertEqual(analysis_records.read(right, "local-user", [resource])["records"][0]["resourceId"], "resource-1")


if __name__ == "__main__":
    unittest.main()
