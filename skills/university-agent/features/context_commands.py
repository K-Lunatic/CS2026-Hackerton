"""Strict command parsing and intent guidance for assignment checkpoints."""
from __future__ import annotations

import re
import shlex
from typing import Any


_SELECTOR_OPTIONS = {"--과목": "course", "--과제": "title", "--마감": "dueAt", "--과제ID": "assignmentId"}


def parse_context_command(text: str) -> dict[str, Any] | None:
    """Parse only the exact, user-facing checkpoint command forms."""
    try:
        tokens = shlex.split(text.strip())
    except ValueError as error:
        prefix = re.match(r"^과제\s+(저장|불러오기)(?:\s|$)", text.strip())
        if prefix:
            operation = "save" if prefix.group(1) == "저장" else "load"
            return {"operation": operation, "error": f"따옴표 짝이 맞지 않습니다: {error}"}
        return None

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

    if "assignmentId" in values and len(values) != 1:
        return {"operation": operation, "error": "이름으로 선택하는 형식과 이전 선택 형식을 함께 사용할 수 없습니다."}
    if operation == "save" and not values:
        return {"operation": operation, "error": "저장할 과목명이나 과제 키워드를 덧붙여 주세요."}

    return {"operation": operation, "values": values}


def detect_context_intent(text: str) -> str | None:
    """Recognize paraphrased save/load requests without performing them."""
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
        r"어디까지|어디서.{0,8}(?:했|멈)|무엇부터|뭐부터|불러|복귀|재개|"
        r"다시.{0,8}시작|이어\s?(?:서|갈|가려|하)|계속.{0,8}과제|"
        r"다음.{0,8}(?:뭐|뭘|무엇|행동|할)|진행.{0,16}(?:보여|알려|확인)|"
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
        if re.search(r"팀플|인수인계", text) and not re.search(r"체크포인트|컨텍스트|불러|복귀|재개", text):
            return None
        return "load"
    return None


def command_template(operation: str) -> str:
    if operation == "save":
        return "과제 저장 <과목명 또는 과제 키워드>"
    return "과제 불러오기"
