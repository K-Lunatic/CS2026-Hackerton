"""Strict command parsing and intent guidance for assignment checkpoints."""
from __future__ import annotations

import re
import shlex
import json
from typing import Any


_SELECTOR_OPTIONS = {"--과목": "course", "--과제": "title", "--마감": "dueAt", "--과제ID": "assignmentId", "--새과제": "newTitle",
                     "--course": "course", "--title": "title", "--due": "dueAt"}


def parse_context_command(text: str) -> dict[str, Any] | None:
    """Parse only the exact, user-facing checkpoint command forms."""
    # A copied command may be wrapped in one Markdown code block, but not prose.
    fenced = re.fullmatch(r"```(?:text|txt)?[ \t]*\r?\n((?:save|load)(?:[ \t]+[^\r\n]*)?)\r?\n```", text.strip())
    if fenced:
        text = fenced.group(1)
    if text in {"list", "과제 목록 불러오기"}:
        return {"operation": "list", "values": {}}
    # Conversational request: let host-supplied clues drive metadata search.
    if text.strip() == "과제 저장":
        return None
    if detect_context_intent(text) == "list":
        return None
    if re.search(r"\b(?:save|bookmark)\s+(?:this|my)\s+(?:chat|conversation)\b", text, re.I):
        return None
    text = text.strip()
    try:
        tokens = shlex.split(text.strip())
    except ValueError as error:
        prefix = re.match(r"^(?:과제\s+(저장|불러오기)|(save|load))(?:\s|$)", text.strip(), re.I)
        if prefix:
            operation = "save" if (prefix.group(1) == "저장" or prefix.group(2) == "save") else "load"
            return {"operation": operation, "error": f"따옴표 짝이 맞지 않습니다: {error}"}
        return None

    if tokens and tokens[0] in {"save", "load"}:
        operation = tokens[0]
        if operation == "save" and re.match(r"^save\s+new(?:\s|$)", text):
            if len(tokens) == 3 and not tokens[2].startswith("--") and tokens[2].strip():
                return {"operation": "save", "values": {"newTitle": tokens[2]}}
            if len(tokens) < 4 or not tokens[2].startswith("--"):
                return {"operation": "save", "error": '새 과제는 save new "제목" 형식으로 입력해 주세요.'}
            index = 2
            values: dict[str, Any] = {"source": "manual"}
            allowed = {"--course": "course", "--title": "title", "--due": "dueAt"}
            while index < len(tokens):
                option = tokens[index]
                if option not in allowed or index + 1 >= len(tokens) or not tokens[index + 1].strip() or tokens[index + 1].startswith("--"):
                    return {"operation": "save", "error": "선택 옵션에는 --course, --title, --due와 값을 사용해 주세요."}
                key = allowed[option]
                if key in values:
                    return {"operation": "save", "error": "같은 선택 옵션은 한 번만 지정해 주세요."}
                values[key] = tokens[index + 1]
                index += 2
            if "title" not in values:
                return {"operation": "save", "error": "--title 뒤에 과제명을 입력해 주세요."}
            return {"operation": "save", "values": values}
        if len(tokens) == 1:
            return {"operation": operation, "values": {}} if operation == "load" else {"operation": operation, "error": 'TLS 과제 이름을 입력해 주세요.'}
        if not tokens[1].startswith("--"):
            if any(token.startswith("--") for token in tokens[1:]):
                return {"operation": operation, "error": "키워드와 선택 옵션을 섞지 말고 한 가지 형식으로 입력해 주세요."}
            query = " ".join(tokens[1:]).strip()
            if not query:
                return {"operation": operation, "error": "과목명이나 과제 키워드를 입력해 주세요."}
            return {"operation": operation, "values": {"query": query, **({"source": "tls"} if operation == "save" else {})}}
        index = 1
        values: dict[str, Any] = {"source": "tls"} if operation == "save" else {}
        allowed = {"--course": "course", "--title": "title", "--due": "dueAt"}
        while index < len(tokens):
            option = tokens[index]
            if option not in allowed or index + 1 >= len(tokens) or not tokens[index + 1].strip() or tokens[index + 1].startswith("--"):
                return {"operation": operation, "error": "선택 옵션에는 --course, --title, --due와 값을 사용해 주세요."}
            key = allowed[option]
            if key in values:
                return {"operation": operation, "error": "같은 선택 옵션은 한 번만 지정해 주세요."}
            values[key] = tokens[index + 1]
            index += 2
        if "title" not in values:
            return {"operation": operation, "error": "--title 뒤에 과제명을 입력해 주세요."}
        return {"operation": operation, "values": values}

    if len(tokens) < 2 or tokens[0] != "과제" or tokens[1] not in {"저장", "불러오기"}:
        return None

    operation = "save" if tokens[1] == "저장" else "load"
    options = _SELECTOR_OPTIONS
    values: dict[str, Any] = {}
    # A plain keyword phrase is the primary user-facing form. Flag forms are
    # useful for distinguishing identical titles across courses and deadlines.
    if len(tokens) > 2 and not tokens[2].startswith("--"):
        if any(token.startswith("--") for token in tokens[2:]):
            return {"operation": operation, "error": "키워드와 선택 옵션을 섞지 말고 한 가지 형식으로 입력해 주세요."}
        query = " ".join(tokens[2:]).strip()
        if not query:
            return {"operation": operation, "error": "과목명이나 과제 키워드를 입력해 주세요."}
        return {"operation": operation, "values": {"query": query}}
    index = 2
    while index < len(tokens):
        option = tokens[index]
        if option not in options:
            return {"operation": operation, "error": "지원하지 않는 선택 옵션입니다. 과목명이나 과제 키워드로 입력해 주세요."}
        if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
            return {"operation": operation, "error": "선택 옵션 뒤에 값을 입력해 주세요."}
        key = options[option]
        value = tokens[index + 1]
        if not value.strip():
            return {"operation": operation, "error": "선택 옵션 뒤에 과목명이나 과제 키워드를 입력해 주세요."}
        if key in values:
            return {"operation": operation, "error": "같은 선택 옵션은 한 번만 지정해 주세요."}
        else:
            values[key] = value
        index += 2

    if "newTitle" in values and (operation != "save" or len(values) != 1):
        return {"operation": operation, "error": '새 과제 제목은 save new "제목" 형식으로 입력해 주세요.'}
    if "assignmentId" in values and len(values) != 1:
        return {"operation": operation, "error": "이름으로 선택하는 형식과 이전 선택 형식을 함께 사용할 수 없습니다."}
    if operation == "save" and not values:
        return {"operation": operation, "error": "저장할 과목명이나 과제 키워드를 덧붙여 주세요."}

    return {"operation": operation, "values": values}


def detect_context_intent(text: str) -> str | None:
    """Recognize paraphrased save/load requests without performing them."""
    if text.strip().lower() in {"list", "list saved", "list assignments"}:
        return "list"
    if (re.search(r"과제.{0,12}목록|목록.{0,12}과제", text) and re.search(r"저장|기록|하던|미완성|불러", text)) or (
        "과제" in text and re.search(r"저장|미완성|하던", text) and re.search(r"전부|모두|전체", text)
        and re.search(r"불러|보여|조회|확인|알려", text)
    ):
        return "list"
    save = re.search(
        r"(?:진행|과제|작업|중단|현재\s*상태|지금까지|하던\s*일|컨텍스트|체크포인트|대화|채팅|요약|북마크|즐겨찾기).{0,24}(?:저장|기록|기억|메모)|"
        r"(?:대화|채팅|요약|진행|컨텍스트).{0,24}(?:북마크|즐겨찾기).{0,8}(?:해|추가|등록|남겨)|"
        r"(?:저장|기록|기억|메모).{0,24}(?:진행|과제|작업|현재|내용|컨텍스트|체크포인트|대화|채팅|요약|북마크|즐겨찾기)|"
        r"(?:과제|작업).{0,12}(?:중단|멈추|그만)|(?:중단|멈추|그만).{0,12}(?:과제|작업)|"
        r"(?:save|bookmark).{0,30}(?:progress|assignment|checkpoint|context|conversation|chat)|"
        r"(?:progress|assignment|checkpoint|context|conversation|chat).{0,30}(?:save|bookmark)",
        text,
        re.I,
    )
    if re.search(r"(?:저장|기록|기억)(?:된|한|했던|해\s*둔|해둔)", text):
        save = None
    load = re.search(
        r"어디까지|어디서.{0,8}(?:했|멈)|불러|복귀|재개|"
        r"다시.{0,8}시작|이어\s?(?:서|갈|가려|하)|계속.{0,8}과제|"
        r"진행.{0,16}(?:보여|알려|확인)|"
        r"(?:저장|기록).{0,12}(?:불러|보여|알려|조회)|저장한.{0,12}(?:진행|과제|작업)|"
        r"(?:어제|지난번|전에).{0,12}(?:한|했던|하던|진행|과제|작업|어디|뭐|기억)|"
        r"복기|뭐였(?:지|더라)|뭐\s*했(?:지|더라)|기억나|"
        r"컨텍스트.{0,12}(?:불러|보여|확인)|(?:과제|컨텍스트).{0,16}북마크.{0,8}(?:목록|불러|보여|확인)|"
        r"(?:대화|채팅).{0,24}(?:보여|조회|복원|불러)|"
        r"resume|load.{0,12}context|show.{0,12}(?:progress|context|checkpoint)|"
        r"where\s+was\s+i|what\s+did\s+i\s+do|what\s+next|what\s+should\s+i\s+do\s+next|"
        r"(?:context|checkpoint).{0,12}(?:show|load|resume)",
        text,
        re.I,
    )
    if save and load:
        return "ambiguous"
    if save:
        return "save"
    if load:
        return "load"
    return None


def command_template(operation: str) -> str:
    if operation == "save":
        return 'TLS 과제: save 과제명 (공백 가능) / TLS에 없는 과제: save new "새 과제명"'
    if operation == "list":
        return "list"
    return 'load "과제명" (가장 최근 기록: load)'


def next_commands(result: dict[str, Any] | None = None) -> list[str]:
    """Offer actions relevant to the current step, without restarting selection."""
    result = result or {}
    if "nextCommands" in result:
        return result["nextCommands"]
    calls = result.get("toolCalls", [])
    data = result.get("data")
    if "find_assignments" in calls and isinstance(data, dict) and "candidates" in data:
        return list(dict.fromkeys(item["command"] for item in data["candidates"] if item.get("command")))
    if result.get("needsSummary") or result.get("error"):
        return []
    commands = []
    if "list_context_bookmarks" in calls and isinstance(data, list):
        titles = [item["assignmentTitle"] for item in data]
        for item in data:
            title = item["assignmentTitle"]
            if titles.count(title) == 1:
                commands.append("load " + json.dumps(title, ensure_ascii=False))
            else:
                commands.append("load --course " + json.dumps(item.get("courseName") or "기타 과제", ensure_ascii=False)
                                + " --title " + json.dumps(title, ensure_ascii=False))
        return list(dict.fromkeys(commands)) or ["과제 진행 상황 저장해줘"]
    elif isinstance(data, dict) and data.get("assignmentTitle"):
        return (["list"] if "get_context_bookmark" in calls else
                ["load " + json.dumps(data["assignmentTitle"], ensure_ascii=False), "list"])
    if "get_context_bookmark" in calls:
        return ["list"]
    if "prompt_context_command" in calls and isinstance(data, dict):
        return {"list": ["list"], "load": ["load"]}.get(data.get("operation"), [])
    return []


def with_next_commands(result: dict[str, Any]) -> dict[str, Any]:
    commands = next_commands(result)
    result["nextCommands"] = commands
    remaining = [command for command in commands if command not in result.get("answer", "")]
    if result.get("answer") and remaining:
        result["answer"] += "\n다음 명령:\n" + "\n".join("• " + command for command in remaining)
    return result
