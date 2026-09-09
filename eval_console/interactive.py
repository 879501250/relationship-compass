"""Shared non-throwing user input primitives for registry management."""

from __future__ import annotations

import getpass
from typing import Callable, Sequence, TypeVar


class InteractiveBack(Exception): pass
class InteractiveCancel(Exception): pass
class InteractiveEOF(Exception): pass


YES = {"y", "yes", "是"}
NO = {"n", "no", "否"}
BACK = {"b", "back", "返回", "上一步"}
CANCEL = {"c", "cancel", "取消"}
CLEAR = {"clear", "none", "清除"}
CLEAR_VALUE = object()
T = TypeVar("T")


class InteractiveReader:
    def __init__(self, input_fn: Callable[[str], str] | None = None, secret_fn: Callable[[str], str] | None = None) -> None:
        self.input_fn, self.secret_fn = input_fn or input, secret_fn or getpass.getpass

    def text(self, prompt: str, *, default: str | None = None, required: bool = False, allow_back: bool = True) -> str:
        value = self._read(prompt).strip()
        if allow_back and value.lower() in BACK: raise InteractiveBack()
        if value.lower() in CANCEL: raise InteractiveCancel()
        if not value and default is not None: return default
        if required and not value: raise ValueError("此项不能为空。")
        return value

    def optional(self, prompt: str, *, default: str | None = None) -> str | None:
        value = self.text(prompt, default=default)
        return value or None

    def optional_value(self, prompt: str, *, default: str | None = None) -> str | object | None:
        """Read optional text with distinct keep-default and clear semantics."""
        value = self._read(prompt).strip()
        if value.lower() in BACK: raise InteractiveBack()
        if value.lower() in CANCEL: raise InteractiveCancel()
        if value.lower() in CLEAR: return CLEAR_VALUE
        return default if not value else value

    def secret(self, prompt: str, *, allow_back: bool = True) -> str:
        try: value = self.secret_fn(prompt)
        except EOFError as error: raise InteractiveEOF() from error
        except KeyboardInterrupt as error: raise InteractiveCancel() from error
        value = value.strip()
        if allow_back and value.lower() in BACK: raise InteractiveBack()
        if value.lower() in CANCEL: raise InteractiveCancel()
        if not value: raise ValueError("令牌不能为空。")
        return value

    def confirm(self, prompt: str, *, default: bool = True, allow_back: bool = False) -> bool:
        suffix = "[Y/n]" if default else "[y/N]"
        while True:
            value = self.text(f"{prompt} {suffix}: ", allow_back=allow_back).lower()
            if not value: return default
            if value in YES: return True
            if value in NO: return False
            print("请输入 y/yes/是 或 n/no/否。")

    def choice(self, prompt: str, choices: Sequence[tuple[str, T]]) -> T:
        print(f"\n{prompt}")
        for index, (label, _) in enumerate(choices, start=1): print(f"  {index}. {label}")
        while True:
            value = self.text("请选择：")
            if value.isdigit() and 1 <= int(value) <= len(choices): return choices[int(value) - 1][1]
            print(f"请输入 1 到 {len(choices)} 的编号。")

    def multi_choice(self, prompt: str, choices: Sequence[tuple[str, T]]) -> list[T]:
        """Read an ordered, non-empty selection using numbers or ``all``."""
        print(f"\n{prompt}")
        for index, (label, _) in enumerate(choices, start=1): print(f"  {index}. {label}")
        while True:
            value = self.text("请选择（例如 1,2 或 all）：").lower()
            if value == "all": return [item for _, item in choices]
            parts = value.replace(",", " ").split()
            if not parts or not all(item.isdigit() for item in parts):
                print("请输入一个或多个编号（例如 1,2），或 all。")
                continue
            indexes = [int(item) for item in parts]
            if len(indexes) != len(set(indexes)):
                print("不能重复选择同一项。")
                continue
            if any(index < 1 or index > len(choices) for index in indexes):
                print(f"请输入 1 到 {len(choices)} 的编号。")
                continue
            return [choices[index - 1][1] for index in indexes]

    def _read(self, prompt: str) -> str:
        try: return self.input_fn(prompt)
        except EOFError as error: raise InteractiveEOF() from error
        except KeyboardInterrupt as error: raise InteractiveCancel() from error
