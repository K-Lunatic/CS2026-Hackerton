"""Plain-language discovery; never guesses targets or performs mutations."""
from __future__ import annotations

import re


def usage_guide(text: str = "") -> dict:
    if re.search(r"과제|할\s*일|마감", text):
        examples = ["이번 주 안 낸 과제 알려줘", "새 과제 추가해줘", "하던 과제 어디까지 했지?"]
        intro = "남은 과제를 확인하거나, 새 과제를 적어 두거나, 하던 과제의 기록을 찾을 수 있어요."
    elif re.search(r"강의|영상|수업", text):
        examples = ["아직 안 본 강의 알려줘", "지금 내 상태 어때?"]
        intro = "덜 본 강의와 시청률, 학교에 표시된 시청 기한을 확인할 수 있어요."
    elif re.search(r"팀플|팀\s*프로젝트|인수인계", text):
        examples = ["팀플 진행 상황 정리해줘", "팀플 인수인계 정리해줘"]
        intro = "회의 내용이나 작업 메모를 보내주면 누가 무엇을 했고 무엇이 남았는지 정리해요."
    else:
        examples = ["지금 내 상태 어때?", "이번 주 안 낸 과제 알려줘", "아직 안 본 강의 알려줘", "새 과제 추가해줘", "팀플 진행 상황 정리해줘"]
        intro = "학교에서 할 일을 확인하고 정리하는 도우미예요. 기능 이름을 몰라도 평소 말하듯 요청하면 돼요."
    return {"toolCalls": [], "data": {"suggestions": examples}, "needsInput": True,
            "answer": intro + "\n예를 들면:\n" + "\n".join(f"• {example}" for example in examples) + "\n원하는 일을 한 문장으로 말해 주세요."}


def guidance_request(text: str) -> dict | None:
    if re.search(r"과제\s*(?:id|아이디)|저장.{0,12}(?:방법|어떻게)|(?:어떻게|방법).{0,12}저장", text, re.I):
        return {"toolCalls": [], "needsInput": True, "data": {"intent": "find-assignment"},
                "answer": "과목명이나 과제 제목의 일부로 고르면 됩니다. 예: ‘과제 저장 자바 Ex05’. 어떤 과제인지 알려주시면 맞는 이름을 찾아드릴게요. 여러 개가 일치하면 과제명과 마감일로 골라 주세요."}
    if re.search(r"도움말|사용법|사용\s*방법|어떻게\s*(?:써|쓰|사용)|뭘\s*할\s*수|뭐\s*할\s*수|무슨\s*기능|처음이|처음\s*(?:써|사용)|기능.*알려", text):
        return usage_guide(text)
    if re.search(r"(?:과제|할\s*일).{0,20}(?:등록|추가)|(?:등록|추가).{0,20}(?:과제|할\s*일)", text):
        return {"toolCalls": [], "data": {"intent": "assignment-add", "requiredFields": ["title"], "optionalFields": ["courseId", "dueAt", "description"]}, "needsInput": True,
                "answer": "새로 적어 둘 과제 제목이 무엇인가요? 예: ‘독서 보고서’. 과목과 마감일은 알고 있으면 함께 말해 주세요."}
    if re.search(r"(?:과제|할\s*일).{0,20}(?:삭제|지워|완료\s*처리)|(?:삭제|지워).{0,20}(?:과제|할\s*일)", text):
        return {"toolCalls": [], "data": {"intent": "select-assignment"}, "needsInput": True,
                "answer": "어떤 과제인지 제목을 알려주세요. 직접 추가한 과제는 완료 표시하거나 지울 수 있어요. 학교 과제의 제출 상태는 학교에서 확인해 반영해요."}
    return None


def is_status_request(text: str) -> bool:
    return bool(re.search(r"컨텍스트|전체|상태|(?:지금|현재)(?:은|는)?.{0,12}어때|나[.\s…]*어때|할\s*일|해야\s*할\s*일|마감\s*목록|(?:오늘|지금).{0,12}(?:뭐|무엇|뭘).{0,8}(?:해|하)|급한.*(?:것|거|일)", text))
