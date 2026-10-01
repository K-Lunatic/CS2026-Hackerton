"""Reusable secure form contract with a local terminal adapter."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from getpass import getpass
from typing import Callable, Mapping


class FormUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class FormField:
    name: str
    label: str
    secret: bool = False
    required: bool = True


@dataclass(frozen=True)
class FormDefinition:
    form_id: str
    title: str
    fields: tuple[FormField, ...]


TLS_CREDENTIALS_FORM = FormDefinition(
    form_id="tls_credentials",
    title="KKU TLS 로그인",
    fields=(
        FormField("username", "아이디"),
        FormField("password", "비밀번호", secret=True),
    ),
)


def collect_local(
    form: FormDefinition,
    *,
    initial: Mapping[str, str] | None = None,
    input_fn: Callable[[str], str] = input,
    secret_input_fn: Callable[[str], str] = getpass,
) -> dict[str, str]:
    """Collect a form without returning secret values to stdout or JSON."""
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise FormUnavailable(f"{form.title} 연결이 아직 필요해요. 비밀번호는 채팅에 보내지 말고 보안 입력 화면에서 입력해 주세요.")
    values = dict(initial or {})
    for field in form.fields:
        if values.get(field.name):
            continue
        reader = secret_input_fn if field.secret else input_fn
        value = reader(f"{field.label}: ")
        if field.required and not value:
            raise ValueError(f"{field.label}은(는) 필수입니다.")
        values[field.name] = value
    return values


def redact(form: FormDefinition, values: Mapping[str, str]) -> dict[str, str]:
    """Return a model/log-safe view; secret fields are never copied."""
    secret_names = {field.name for field in form.fields if field.secret}
    return {name: "[REDACTED]" if name in secret_names and value else value for name, value in values.items()}


def requested_schema(form: FormDefinition) -> dict[str, object]:
    """Return a host-neutral JSON Schema for a future ChatGPT form adapter."""
    properties = {
        field.name: {"type": "string", "title": field.label, "writeOnly": field.secret}
        for field in form.fields
    }
    return {"type": "object", "title": form.title, "properties": properties, "required": [field.name for field in form.fields if field.required]}
