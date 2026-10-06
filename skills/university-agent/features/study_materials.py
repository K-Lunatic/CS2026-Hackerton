"""Extract text from locally downloaded TLS course files for grounded study aids."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any
from features import material_cache

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


def _normalize(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum())


def _pptx_sections(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        slides = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=lambda name: int(re.search(r"slide(\d+)", name).group(1)),
        )
        if len(slides) > 200 or any(archive.getinfo(name).file_size > 2_000_000 for name in slides):
            raise ValueError("슬라이드 수나 크기가 너무 커서 읽을 수 없습니다.")
        sections = []
        for name in slides:
            number = int(re.search(r"slide(\d+)", name).group(1))
            root = ET.fromstring(archive.read(name))
            paragraphs = []
            for paragraph in root.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}p"):
                text = "".join(node.text or "" for node in paragraph.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t")).strip()
                if text:
                    paragraphs.append(text)
            if paragraphs:
                sections.append({"location": f"슬라이드 {number}", "text": "\n".join(paragraphs)})
        return sections


def _xml_sections(path: Path, names: list[str], location: str) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        if len(names) > 200 or any(archive.getinfo(name).file_size > 2_000_000 for name in names):
            raise ValueError("문서 XML의 크기가 너무 커서 읽을 수 없습니다.")
        sections: list[dict[str, str]] = []
        for name in names:
            root = ET.fromstring(archive.read(name))
            paragraphs = []
            for paragraph in root.iter():
                if paragraph.tag.rsplit("}", 1)[-1] != "p":
                    continue
                text = "".join(node.text or "" for node in paragraph.iter() if node.tag.rsplit("}", 1)[-1] in {"t", "text"}).strip()
                if text:
                    paragraphs.append(text)
            if paragraphs:
                section_number = re.search(r"section(\d+)\.xml$", name)
                suffix = f" {section_number.group(1)}" if section_number else ""
                sections.append({"location": f"{location}{suffix}".strip(), "text": "\n".join(paragraphs)})
        return sections


def _docx_sections(path: Path) -> list[dict[str, str]]:
    return _xml_sections(path, ["word/document.xml"], "DOCX 본문")


def _hwpx_sections(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        names = sorted(name for name in archive.namelist() if re.fullmatch(r"Contents/section\d+\.xml", name))
    return _xml_sections(path, names, "HWPX 본문")


def _legacy_office_sections(path: Path) -> list[dict[str, str]]:
    extension = path.suffix.lower()
    commands: list[list[str]] = []
    if extension == ".hwp":
        if command := shutil.which("hwp5txt"):
            commands.append([command, str(path)])
    else:
        if command := shutil.which("textutil"):
            commands.append([command, "-convert", "txt", "-stdout", str(path)])
        for name in ("antiword", "catdoc"):
            if command := shutil.which(name):
                commands.append([command, str(path)])
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and result.stdout.strip():
            return [{"location": f"{extension[1:].upper()} 본문", "text": result.stdout.strip()}]
    for name in ("soffice", "libreoffice"):
        command = shutil.which(name)
        if not command:
            continue
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([command, "--headless", "--convert-to", "txt:Text", "--outdir", directory, str(path)], capture_output=True, text=True, timeout=120)
            converted = Path(directory) / f"{path.stem}.txt"
            if result.returncode == 0 and converted.is_file():
                text = converted.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    return [{"location": f"{extension[1:].upper()} 본문", "text": text}]
    label = "HWP" if extension == ".hwp" else "DOC"
    raise RuntimeError(f"{label} 파일을 읽을 변환 도구가 없습니다. {label}X 또는 PDF로 저장해 다시 동기화해 주세요.")


def _text_sections(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    if b"\x00" in raw[:4096] and not raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        raise RuntimeError("텍스트 파일이 아닙니다.")
    for encoding in (('utf-16',) if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'cp949')):
        try:
            text = raw.decode(encoding)
            return [{'location': f'줄 {i}', 'text': line} for i, line in enumerate(text.splitlines(), 1) if line.strip()]
        except UnicodeError:
            pass
    raise RuntimeError('텍스트 인코딩을 확인하지 못했어요. UTF-8로 저장한 파일을 골라주세요.')


def _pdf_sections(paths: list[Path], script: Path, cache_dir: Path) -> dict[str, list[dict[str, str]]]:
    pdftotext = shutil.which("pdftotext")
    swift = shutil.which("swift") if sys.platform == "darwin" else None
    if not (PdfReader or pdftotext or swift):
        raise RuntimeError("PDF 읽기 도구가 없어요. 같은 Python에서 python -m pip install pypdf를 실행한 뒤 다시 시도해 주세요. Windows에서도 Swift는 필요 없어요.")
    output: dict[str, list[dict[str, str]]] = {}
    for path in paths:
        pages = []
        succeeded = False
        if PdfReader:
            try:
                reader = PdfReader(path)
                if reader.is_encrypted:
                    raise RuntimeError('암호화된 PDF는 자동으로 해제하지 않습니다.')
                pages = [{"location": f"PDF p.{index}", "text": text.strip()}
                         for index, page in enumerate(reader.pages, 1) if (text := page.extract_text()) and text.strip()]
                succeeded = True
            except Exception:
                # Different readers handle broken fonts differently; do not label this OCR yet.
                pass
        if not pages and pdftotext:
            try:
                result = subprocess.run([pdftotext, "-enc", "UTF-8", "-layout", str(path), "-"], capture_output=True, text=True, encoding='utf-8', timeout=120)
                if result.returncode == 0:
                    pages = [{"location": f"PDF p.{index}", "text": page.strip()}
                             for index, page in enumerate(result.stdout.split("\f"), 1) if page.strip()]
                    succeeded = True
            except (OSError, UnicodeError, subprocess.SubprocessError):
                pass
        if not pages and swift:
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                result = subprocess.run([swift, "-module-cache-path", str(cache_dir), str(script), str(path)], capture_output=True, text=True, encoding='utf-8', timeout=120)
                if result.returncode == 0:
                    decoded = json.loads(result.stdout)
                    pages = decoded.get(str(path), [])
                    succeeded = succeeded or str(path) in decoded
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
        if not pages:
            if succeeded:
                raise RuntimeError("여러 읽기 방법에서도 본문 글자가 나오지 않았어요. 이미지형 자료이거나 글자 정보가 없는 PDF일 수 있어요. OCR 또는 텍스트가 있는 원본이 필요합니다.")
            raise RuntimeError("PDF 추출기가 파일을 열지 못했어요. 손상·암호화 또는 추출 도구 오류를 확인해 주세요. 스캔본이라고 단정할 수는 없어요.")
        output[str(path.resolve())] = pages
    return output


def _ppt_sections(path: Path) -> list[dict[str, str]]:
    textutil = shutil.which("textutil")
    if textutil:
        result = subprocess.run([textutil, "-convert", "txt", "-stdout", str(path)], capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and result.stdout.strip():
            return [{"location": "슬라이드 위치 미확인", "text": result.stdout.strip()}]
    for name in ("soffice", "libreoffice", "catppt"):
        command = shutil.which(name)
        if not command:
            continue
        if name == "catppt":
            result = subprocess.run([command, str(path)], capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and result.stdout.strip():
                return [{"location": "슬라이드 위치 미확인", "text": result.stdout.strip()}]
            continue
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([command, "--headless", "--convert-to", "txt:Text", "--outdir", directory, str(path)], capture_output=True, text=True, timeout=120)
            converted = Path(directory) / f"{path.stem}.txt"
            if result.returncode == 0 and converted.is_file():
                text = converted.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    return [{"location": "슬라이드 위치 미확인", "text": text}]
    raise RuntimeError("구형 PPT를 읽을 변환 도구가 없습니다. PPTX 또는 PDF로 변환해 다시 동기화해 주세요.")


def _read_sections(path: Path, extension: str, cache_root: Path):
    text_formats = {'txt', 'md', 'py', 'js', 'java', 'c', 'cpp', 'html', 'css', 'csv', 'json', 'xml', 'yaml', 'yml'}
    identifier = material_cache.key(['text-v2' if extension in text_formats else 'text-v1', extension, material_cache.fingerprint(cache_root, path)])
    cached = material_cache.get(cache_root, 'text', '', identifier)
    if cached is not None:
        return cached
    if extension == 'pdf':
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'extract_pdf.swift'
        sections = _pdf_sections([path], script, cache_root / 'swift-modules').get(str(path), [])
    elif extension in ('txt', 'md'):
        sections = _text_sections(path)
    elif extension in ('pptx', 'ppt', 'docx', 'hwpx'):
        sections = {'pptx': _pptx_sections, 'ppt': _ppt_sections, 'docx': _docx_sections, 'hwpx': _hwpx_sections}[extension](path)
    elif extension in ('doc', 'hwp'):
        sections = _legacy_office_sections(path)
    elif extension in ('py', 'js', 'java', 'c', 'cpp', 'html', 'css', 'csv', 'json', 'xml', 'yaml', 'yml'):
        sections = _text_sections(path)
    else:
        raise ValueError('지원하지 않는 파일 형식입니다.')
    if sections and sum(len(section['text']) for section in sections) <= 10_000_000:
        material_cache.put(cache_root, 'text', '', identifier, sections)
    return sections


def original_files(courses, resources, *, files_root, course_query='', resource_query='', resource_id=''):
    """Return authorized original paths, not extracted text or regenerated documents."""
    courses = [c for c in courses if not course_query or _normalize(course_query) in _normalize(c['name'])]
    allowed = {c['id'] for c in courses}
    selected = [r for r in resources if r['courseId'] in allowed
                and (not resource_id or r['id'] == resource_id)
                and (not resource_query or _normalize(resource_query) in _normalize(r['title'] + ' ' + r['fileName']))]
    result = []
    for item in selected:
        entry = {'id': item['id'], 'title': item['title'], 'fileName': item['fileName']}
        if item.get('downloadStatus') == 'PROHIBITED':
            entry['error'] = item.get('downloadReason') or '다운로드 금지 자료예요.'
        else:
            try:
                path = Path(item.get('localPath') or '').resolve(strict=True)
                path.relative_to(Path(files_root).resolve())
                if not path.is_file():
                    raise ValueError
                entry.update(path=str(path), sizeBytes=path.stat().st_size)
            except (OSError, ValueError):
                entry['error'] = '원본 파일이 아직 없어요. 전체 자료 새로고침이 필요해요.'
        result.append(entry)
    return {'data': {'files': result}, 'answer': '요청한 강의 원본 파일이에요.' if result else '일치하는 자료가 없어요.'}


def attached_material(path_text: str, *, title: str = "", max_chars: int = 30000, cache_root: Path | None = None) -> dict[str, Any]:
    """Read a file explicitly supplied by the current user, without claiming TLS ownership."""
    path = Path(path_text).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 10_000_000:
        raise ValueError("첨부 파일은 10MB 이하의 일반 파일이어야 합니다.")
    extension = path.suffix.lower()
    try:
        if extension not in ('.pdf', '.pptx', '.ppt', '.txt', '.md'):
            raise ValueError("PDF, PPTX, TXT, MD 파일만 첨부할 수 있습니다.")
        sections = _read_sections(path, extension[1:], cache_root or path.parent / 'cache')
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    except (OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile, ET.ParseError, subprocess.SubprocessError) as exc:
        raise ValueError("첨부 파일의 본문을 읽지 못했습니다. 텍스트 파일이나 다른 자료를 골라주세요.") from exc
    usable = []
    remaining = max_chars
    for section in sections:
        if section.get("text", "").strip() and remaining:
            excerpt = section["text"][:remaining]
            usable.append({"location": section["location"], "text": excerpt})
            remaining -= len(excerpt)
    if not usable:
        raise ValueError("첨부 파일에서 읽을 수 있는 텍스트가 없습니다. 이미지형 PDF라면 OCR이 필요합니다.")
    digest = material_cache.fingerprint(cache_root or path.parent / 'cache', path)
    identifier = "attachment-" + digest[:16]
    return {"id": identifier, "title": title or path.name, "extension": extension[1:], "sections": usable,
            'contentKey': digest,
            "truncated": sum(len(s.get("text", "")) for s in sections) > max_chars}


def study_materials(
    courses: list[dict[str, Any]], resources: list[dict[str, Any]], *,
    files_root: Path, course_query: str = "", resource_query: str = "", max_chars: int = 30000,
    course_id: str = "", resource_ids: list[str] | None = None, locations: dict | None = None, include_ids: bool = False,
    metadata_only: bool = False,
) -> dict[str, Any]:
    """Return grounded, bounded source text without local paths or database IDs."""
    if course_id:
        matching_courses = [c for c in courses if c["id"] == course_id]
    elif course_query:
        needle = _normalize(course_query)
        matching_courses = [course for course in courses if needle in _normalize(course["name"])]
    else:
        matching_courses = courses
    if len(matching_courses) != 1:
        names = [course["name"] for course in matching_courses]
        message = "과목명을 더 구체적으로 지정해 주세요." if names else "일치하는 과목이 없습니다."
        return {"needsInput": True, "answer": message, "data": {"courses": names}}

    course = matching_courses[0]
    needle = _normalize(resource_query)
    selected = [item for item in resources if item.get("courseId") == course["id"]]
    if needle:
        selected = [item for item in selected if needle in _normalize(item.get("title", "") + item.get("fileName", ""))]
    if resource_ids is not None:
        available = {item['id'] for item in selected}
        if not resource_ids or not set(resource_ids) <= available:
            raise ValueError('선택 과목에서 접근 가능한 자료 ID만 사용하세요.')
        selected = [item for item in selected if item['id'] in resource_ids]
    if len(selected) > 3:
        return {'needsInput': True, 'answer': '자료가 많습니다. 사용할 자료를 3개 이하로 골라주세요.',
                'data': {'materials': [{'id': x['id'], 'title': x['title']} for x in selected[:10]], 'totalMaterials': len(selected)}}
    if not selected:
        return {"needsInput": bool(not resource_query), "answer": "이 과목에서 조건에 맞는 다운로드 자료를 찾지 못했습니다.", "data": {"courseName": course["name"], "materials": []}}

    root = files_root.resolve()
    resolved: dict[str, Path] = {}
    errors: dict[str, str] = {}
    for item in selected:
        if item.get("downloadStatus") == "PROHIBITED":
            errors[item["id"]] = item.get("downloadReason") or "TLS에 다운로드 금지 표시가 있어 파일을 가져오지 않았고 분석에서 제외했습니다."
            continue
        try:
            path = Path(item.get("localPath") or "").resolve(strict=True)
            path.relative_to(root)
            if not path.is_file():
                raise ValueError
            if path.stat().st_size > 10_000_000:
                errors[item["id"]] = "파일이 10MB를 넘어서 현재 읽을 수 없습니다. 범위를 좁히거나 작은 파일을 첨부해주세요."
                continue
            resolved[item["id"]] = path
        except (OSError, ValueError):
            errors[item["id"]] = "로컬 파일이 없습니다. TLS 동기화를 다시 실행해 주세요."

    materials = []
    remaining = max_chars
    for item in selected:
        material = {"title": item["title"], "fileName": item["fileName"], "extension": item["extension"], "downloadStatus": item.get("downloadStatus", "NOT_DOWNLOADED"), "sections": []}
        if item["id"] in errors:
            material["error"] = errors[item["id"]]
            materials.append(material)
            continue
        if include_ids:
            material.update(id=item['id'], courseId=course['id'])
        path = resolved[item["id"]]
        try:
            extension = item["extension"].lower()
            if include_ids:
                material['contentKey'] = material_cache.fingerprint(files_root.parent / 'cache', path)
            if metadata_only:
                if not include_ids:
                    raise ValueError('내부 자료 확인에는 ID가 필요합니다.')
                materials.append(material)
                continue
            sections = _read_sections(path, extension, files_root.parent / 'cache')
        except RuntimeError as exc:
            material['error'] = str(exc)
            materials.append(material)
            continue
        except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError, subprocess.SubprocessError, RuntimeError):
            material["error"] = "파일 텍스트를 읽지 못했습니다. 오래된 HWP/DOC 파일은 HWPX/DOCX 또는 PDF로 변환해 주세요."
            materials.append(material)
            continue
        requested = (locations or {}).get(item['id'])
        if requested is not None:
            found = {section['location'] for section in sections}
            if not requested or not set(requested) <= found:
                material['error'] = '지정한 페이지/슬라이드/줄의 본문을 전부 읽지 못했습니다. 범위를 확인하세요.'
                materials.append(material)
                continue
            sections = [section for section in sections if section['location'] in requested]
        text_found = False
        for section in sections:
            text = section["text"].strip()
            if not text:
                continue
            text_found = True
            if remaining <= 0:
                material["truncated"] = True
                break
            excerpt = text[:remaining]
            material["sections"].append({"location": section["location"], "text": excerpt})
            remaining -= len(excerpt)
            if len(excerpt) < len(text):
                material["truncated"] = True
                break
        if not text_found:
            material["error"] = "추출 가능한 텍스트가 없습니다. 이미지 스캔 자료는 OCR이 필요합니다."
        materials.append(material)

    blocked = [item["title"] for item in materials if item["downloadStatus"] == "PROHIBITED"]
    loaded = any(item["sections"] for item in materials)
    answer = "강의 자료 텍스트를 불러왔습니다. 출처 위치가 표시된 내용만 근거로 학습 자료를 만드세요." if loaded else "읽을 수 있는 강의 자료 텍스트가 없습니다."
    if blocked:
        answer += "\n다운로드 제한으로 가져오지 않은 자료: " + ", ".join(blocked)
    return {
        "needsInput": False,
        "toolCalls": ["read_course_files"],
        "data": {"courseName": course["name"], **({"courseId": course["id"]} if include_ids else {}), "materials": materials, "truncated": remaining <= 0},
        "answer": answer,
    }
