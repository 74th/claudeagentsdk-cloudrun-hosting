"""利用者 identity のアプリケーション境界。"""

from __future__ import annotations

from typing import Protocol


class IdentityProvider(Protocol):
    """フロントエンドが会話サービスへ渡す利用者 ID の境界。"""

    def user_id(self) -> str: ...


class StaticIdentity:
    """CLI やサンプルで利用する固定 identity。"""

    def __init__(self, value: str) -> None:
        value = value.strip()
        if not value:
            raise ValueError("user_id must not be blank")
        self._value = value

    def user_id(self) -> str:
        return self._value
