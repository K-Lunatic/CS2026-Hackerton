from __future__ import annotations

import re


def create_handover(text: str) -> dict[str, list[str]]:
    completed, pending, files, environment, notes = [], [], [], [], []
    for sentence in re.split(r"[.。!！?？\n]+", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        has_environment = re.search(r"(/api/|API_BASE_URL|\benv\b|ENV|JWT|refresh token)", sentence, re.I)
        if has_environment:
            environment.append(sentence)
        if re.search(r"(아직|남았|미완|못 했|못했|TODO)", sentence, re.I):
            pending.append(sentence)
        elif re.search(r"(구현|완료|연결|만들었|만듦|했음)", sentence):
            completed.append(sentence)
        elif not has_environment:
            notes.append(sentence)
    return {"completed": completed, "pending": pending, "files": files, "environment": environment, "notes": notes, "nextActions": pending.copy()}
