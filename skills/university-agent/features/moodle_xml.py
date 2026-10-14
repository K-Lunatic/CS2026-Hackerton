"""Convert a saved Turtleneck exam into Moodle question XML.

The exporter is deliberately local-only. Uploading or publishing a quiz is a
separate, explicitly targeted operation because it changes the school site.
"""
from __future__ import annotations

from html import escape
import base64
import hashlib
from pathlib import Path
import json
import sqlite3
import xml.etree.ElementTree as ET

from features.autonomous_course_policy import AutonomousCoursePolicyError, validate_visible_name
from features.study_materials import attached_material


class MoodleXmlError(ValueError):
    pass


IMAGE_SUFFIXES = {'.gif', '.jpe', '.jpeg', '.jpg', '.png', '.svg', '.svgz'}


def _text(parent: ET.Element, tag: str, value: object, *, attrib: dict[str, str] | None = None) -> ET.Element:
    node = ET.SubElement(parent, tag, attrib or {})
    node.text = str(value or "")
    return node


def _html(value: object) -> str:
    """Keep generated text inert while retaining readable line breaks."""
    return escape(str(value or "")).replace("\n", "<br>\n")


def _html_node(parent: ET.Element, tag: str, value: object) -> ET.Element:
    node = ET.SubElement(parent, tag, {"format": "html"})
    _text(node, "text", _html(value))
    return node


def _image_path(item: object, assets: dict[str, Path]) -> tuple[Path, str]:
    if isinstance(item, str):
        raw_path, alt = item, ''
    elif isinstance(item, dict):
        raw_path, alt = item.get('path'), str(item.get('alt') or '')
    else:
        raise MoodleXmlError('이미지 항목 형식이 올바르지 않습니다.')
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise MoodleXmlError('이미지 경로가 없습니다.')
    path = assets.get(raw_path) or Path(raw_path).expanduser()
    path = path.resolve()
    if not path.is_file() or path.suffix.casefold() not in IMAGE_SUFFIXES:
        raise MoodleXmlError(f'문항 이미지 파일을 읽을 수 없습니다: {raw_path}')
    return path, alt


def _asset_name(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    return f'{digest}-{path.name}'


def _question_text(question: dict, assets: dict[str, Path]) -> tuple[str, list[tuple[str, Path]]]:
    body = _html(question.get("question"))
    code = str(question.get("code") or "")
    if code:
        body += "<br><pre>" + escape(code) + "</pre>"
    blocks = question.get("blocks") or []
    if blocks:
        body += "<br>" + "<br>".join(f"{index}. {_html(block)}" for index, block in enumerate(blocks, 1))
    files = []
    for item in question.get('images') or []:
        path, alt = _image_path(item, assets)
        filename = _asset_name(path)
        body += f'<br><img src="@@PLUGINFILE@@/{escape(filename, quote=True)}" alt="{escape(alt, quote=True)}">'
        files.append((filename, path))
    return body, files


def _feedback(value: object = "") -> str:
    return _html(value)


def _add_common(node: ET.Element, question: dict, index: int, assets: dict[str, Path]) -> None:
    try:
        name = validate_visible_name(question.get("name") or f"터틀넥 문항 {index}", field="문항 이름")
    except AutonomousCoursePolicyError as error:
        raise MoodleXmlError(str(error)) from error
    _html_node(node, "name", name)
    questiontext = ET.SubElement(node, "questiontext", {"format": "html"})
    body, files = _question_text(question, assets)
    _text(questiontext, "text", body)
    for filename, path in files:
        file_node = ET.SubElement(questiontext, "file", {
            "name": filename, "path": "/", "encoding": "base64",
        })
        file_node.text = base64.b64encode(path.read_bytes()).decode('ascii')
    _html_node(node, "generalfeedback", question.get("explanation", ""))
    _text(node, "defaultgrade", f"{float(question.get('points', 1) or 1):.7f}")
    _text(node, "penalty", "0.3333333")
    _text(node, "hidden", "0")


def _add_multichoice(node: ET.Element, question: dict, *, multiple: bool = False) -> None:
    options = question.get("options")
    if not isinstance(options, list) or len(options) < 2 or any(not str(option).strip() for option in options):
        raise MoodleXmlError("객관식 문항에는 선택지 2개 이상이 필요합니다.")
    correct = question.get('correctAnswers') or [question.get("answer", "")]
    if not isinstance(correct, list) or not correct or any(str(item).strip() not in {str(option).strip() for option in options} for item in correct):
        raise MoodleXmlError("객관식 정답이 선택지에 없습니다.")
    _text(node, "single", "false" if multiple else "true")
    _text(node, "shuffleanswers", "true")
    _text(node, "answernumbering", "abc")
    for option in options:
        option_text = str(option)
        fraction = f"{100 / len(correct):.7g}" if option_text.strip() in {str(item).strip() for item in correct} else "0"
        answer_node = ET.SubElement(node, "answer", {"fraction": fraction, "format": "html"})
        _text(answer_node, "text", _html(option_text))
        _html_node(answer_node, "feedback", "")


def _add_numerical(node: ET.Element, question: dict) -> None:
    _text(node, "tolerance", str(question.get("tolerance", 0)))
    answer_node = ET.SubElement(node, "answer", {"fraction": "100", "format": "html"})
    _text(answer_node, "text", _html(question.get("answer", "")))
    _html_node(answer_node, "feedback", "")
    for unit in question.get("units") or []:
        if not isinstance(unit, dict) or not str(unit.get("name", "")).strip():
            raise MoodleXmlError("수치형 단위는 name이 필요합니다.")
        unit_node = ET.SubElement(node, "unit")
        _text(unit_node, "multiplier", unit.get("multiplier", 1))
        _text(unit_node, "unit_name", unit["name"])


def _add_matching(node: ET.Element, question: dict) -> None:
    pairs = question.get("pairs") or []
    if not isinstance(pairs, list) or len(pairs) < 2:
        raise MoodleXmlError("짝짓기 문항에는 2개 이상의 쌍이 필요합니다.")
    _text(node, "shuffleanswers", "1")
    for pair in pairs:
        if not isinstance(pair, dict) or not str(pair.get("prompt", "")).strip() or not str(pair.get("answer", "")).strip():
            raise MoodleXmlError("짝짓기 문항의 prompt/answer를 확인하세요.")
        sub = ET.SubElement(node, "subquestion")
        _html_node(sub, "text", pair["prompt"])
        answer = ET.SubElement(sub, "answer")
        _html_node(answer, "text", pair["answer"])


def _add_calculated(node: ET.Element, question: dict, *, multi: bool = False) -> None:
    if multi:
        _add_multichoice(node, question, multiple=False)
    else:
        _add_numerical(node, question)
    _text(node, "synchronize", "0")
    _text(node, "unitgradingtype", "0")
    _text(node, "unitpenalty", "0.1")
    _text(node, "unitrole", "1")
    _text(node, "unitsleft", "0")
    datasets = ET.SubElement(node, "dataset_definitions")
    definition = ET.SubElement(datasets, "dataset_definition")
    _text(definition, "status", "private")
    _text(definition, "name", "turtleneck")
    _text(definition, "type", "private")


def _add_random_short_match(node: ET.Element, question: dict) -> None:
    _text(node, "choose", question.get("choose", 2))


def _add_cloze(node: ET.Element, question: dict) -> None:
    if not str(question.get("question", "")).strip():
        raise MoodleXmlError("빈칸 채우기 문항 본문이 없습니다.")


def _add_shortanswer(node: ET.Element, question: dict) -> None:
    _text(node, "usecase", "0")
    answers = question.get("acceptedAnswers") or [question.get("answer", "")]
    if not isinstance(answers, list) or not answers or any(not str(answer).strip() for answer in answers):
        raise MoodleXmlError("단답형 문항에는 정답이 필요합니다.")
    for answer in answers:
        answer_node = ET.SubElement(node, "answer", {"fraction": "100", "format": "html"})
        _text(answer_node, "text", _html(answer))
        _html_node(answer_node, "feedback", "")


def _add_essay(node: ET.Element, question: dict) -> None:
    _text(node, "responseformat", "editor")
    _text(node, "responserequired", "1")
    _text(node, "responsefieldlines", "10")
    _text(node, "attachments", "0")
    rubric = "\n".join(question.get("rubric") or [])
    expected = str(question.get("answer") or "")
    _html_node(node, "graderinfo", "평가 기준:\n" + rubric + "\n예상 답안:\n" + expected)


def _add_truefalse(node: ET.Element, question: dict) -> None:
    answer = str(question.get("answer", "")).strip().casefold()
    if answer not in {"true", "false", "참", "거짓"}:
        raise MoodleXmlError("OX 문항의 정답은 참 또는 거짓이어야 합니다.")
    truth = answer in {"true", "참"}
    for value, label in ((True, "true"), (False, "false")):
        answer_node = ET.SubElement(node, "answer", {"fraction": "100" if value == truth else "0"})
        _text(answer_node, "text", label)
        _html_node(answer_node, "feedback", "")


def _question_node(question: dict, index: int, assets: dict[str, Path]) -> tuple[ET.Element, str | None]:
    kind = str(question.get("type") or "").strip()
    if not kind:
        raise MoodleXmlError(f"{index}번 문항의 유형이 없습니다.")
    if kind in {"mcq", "multichoice"}:
        moodle_type, unsupported = "multichoice", None
    elif kind == "multichoiceset":
        moodle_type, unsupported = "multichoiceset", None
    elif kind == "short":
        moodle_type, unsupported = "shortanswer", None
    elif kind == "shortanswer":
        moodle_type, unsupported = "shortanswer", None
    elif kind == "truefalse":
        moodle_type, unsupported = "truefalse", None
    elif kind == "multianswer":
        moodle_type, unsupported = "cloze", None
    elif kind in {"calculatedmulti", "calculated", "calculatedsimple", "randomsamatch", "numerical", "match", "description"}:
        moodle_type, unsupported = kind, None
    else:
        moodle_type, unsupported = "essay", None if kind == "essay" else kind
    node = ET.Element("question", {"type": moodle_type})
    _add_common(node, question, index, assets)
    if moodle_type == "multichoice":
        _add_multichoice(node, question)
    elif moodle_type == "multichoiceset":
        _add_multichoice(node, question, multiple=True)
    elif moodle_type == "shortanswer":
        _add_shortanswer(node, question)
    elif moodle_type == "truefalse":
        _add_truefalse(node, question)
    elif moodle_type == "numerical":
        _add_numerical(node, question)
    elif moodle_type == "match":
        _add_matching(node, question)
    elif moodle_type == "calculatedmulti":
        _add_calculated(node, question, multi=True)
    elif moodle_type in {"calculated", "calculatedsimple"}:
        _add_calculated(node, question)
    elif moodle_type == "randomsamatch":
        _add_random_short_match(node, question)
    elif moodle_type == "cloze":
        _add_cloze(node, question)
    elif moodle_type == "description":
        pass
    else:
        _add_essay(node, question)
    return node, unsupported


def exam_to_xml(state: dict, *, category: str = "$course$/top/터틀넥") -> tuple[bytes, dict]:
    """Return UTF-8 Moodle XML and a conversion report without network access."""
    try:
        category = validate_visible_name(category, field="문제 분류 이름")
    except AutonomousCoursePolicyError as error:
        raise MoodleXmlError(str(error)) from error
    questions = state.get("questions")
    if not isinstance(questions, list) or not questions:
        raise MoodleXmlError("저장된 시험지에 문항이 없습니다.")
    root = ET.Element("quiz")
    category_node = ET.SubElement(root, "question", {"type": "category"})
    category_text = ET.SubElement(category_node, "category")
    _text(category_text, "text", category)
    raw_assets = state.get('assets') or {}
    assets = {}
    if isinstance(raw_assets, dict):
        asset_items = raw_assets.items()
    elif isinstance(raw_assets, list):
        asset_items = ((item.get('key') or item.get('path'), item.get('path'))
                       for item in raw_assets if isinstance(item, dict))
    else:
        raise MoodleXmlError('시험지 이미지 자산 목록 형식이 올바르지 않습니다.')
    for key, value in asset_items:
        if isinstance(key, str) and isinstance(value, str):
            assets[key] = Path(value).expanduser()
    converted, downgraded, asset_count = 0, [], 0
    for index, question in enumerate(questions, 1):
        if not isinstance(question, dict):
            raise MoodleXmlError(f"{index}번 문항 형식이 올바르지 않습니다.")
        node, original_type = _question_node(question, index, assets)
        root.append(node)
        converted += 1
        asset_count += len(node.findall('./questiontext/file'))
        if original_type:
            downgraded.append({"index": index, "type": original_type, "as": "essay"})
    ET.indent(root, space="  ")
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return payload, {"questionCount": converted, "downgraded": downgraded,
                     "assetCount": asset_count, "category": category}


def export_exam(path: Path, output: Path, *, category: str = "$course$/top/터틀넥") -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MoodleXmlError("시험지 저장 상태를 읽지 못했습니다.") from error
    payload, report = exam_to_xml(state, category=category)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    return {**report, "path": str(output.resolve())}


def export_saved_exam(database_path: Path, user: str, exam_id: str, output: Path,
                      *, category: str = "$course$/top/터틀넥") -> dict:
    try:
        with sqlite3.connect(database_path) as database:
            row = database.execute(
                "SELECT state FROM study_exams WHERE user=? AND exam_id=?",
                (user, exam_id),
            ).fetchone()
    except sqlite3.Error as error:
        raise MoodleXmlError("저장된 시험지 데이터베이스를 읽지 못했습니다.") from error
    if not row:
        raise MoodleXmlError("현재 사용자의 저장된 시험지를 찾지 못했습니다.")
    try:
        state = json.loads(row[0])
    except (TypeError, ValueError) as error:
        raise MoodleXmlError("저장된 시험지 형식이 올바르지 않습니다.") from error
    payload, report = exam_to_xml(state, category=category)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    return {**report, "path": str(output.resolve()), "examId": exam_id}


def reference_to_xml(path: Path, output: Path, *, category: str = "$course$/top/터틀넥") -> dict:
    """Store one user-supplied reference file in a private, non-question Moodle XML."""
    path = Path(path).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    if path == output:
        raise MoodleXmlError("원본 파일과 XML 저장 위치는 달라야 합니다.")
    try:
        category = validate_visible_name(category, field="참고자료 분류 이름")
    except AutonomousCoursePolicyError as error:
        raise MoodleXmlError(str(error)) from error
    if not path.is_file():
        raise MoodleXmlError("사용자 참고자료 파일을 찾지 못했습니다.")
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        material = attached_material(str(path), title="사용자 참고자료", max_chars=200_000,
                                     cache_root=output.parent / ".turtleneck-cache")
    except (OSError, ValueError) as error:
        raise MoodleXmlError(str(error)) from error

    root = ET.Element("quiz")
    category_node = ET.SubElement(root, "question", {"type": "category"})
    _text(ET.SubElement(category_node, "category"), "text", category)
    node = ET.SubElement(root, "question", {"type": "description"})
    _html_node(node, "name", f"터틀넥 참고자료 {digest[:10]}")
    body = "\n".join(f"[{section['location']}]\n{section['text']}" for section in material["sections"])
    questiontext = ET.SubElement(node, "questiontext", {"format": "html"})
    _text(questiontext, "text", _html(body))
    filename = f"reference-{digest[:16]}{path.suffix.lower()}"
    file_node = ET.SubElement(questiontext, "file", {
        "name": filename, "path": "/", "encoding": "base64",
    })
    file_node.text = base64.b64encode(path.read_bytes()).decode("ascii")
    _html_node(node, "generalfeedback", "사용자가 제공한 참고자료입니다. 공개·업로드 전 별도 확인이 필요합니다.")
    _text(node, "defaultgrade", "0.0000000")
    _text(node, "penalty", "0.0000000")
    _text(node, "hidden", "0")
    ET.indent(root, space="  ")
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload)
    except OSError as error:
        raise MoodleXmlError("참고자료 XML을 저장하지 못했습니다.") from error
    return {"sourceType": "user_reference", "textCharacters": sum(len(s["text"]) for s in material["sections"]),
            "textTruncated": material["truncated"], "originalFileIncluded": True,
            "sourceHash": digest, "category": category, "path": str(output)}
