"""
Token 号池管理 — 多账号轮询 + 冷却机制。

类似 grok2api 的 AccountDirectory，但无需 Redis/MySQL，
纯内存管理，适合 HF Space / Docker 单实例部署。
"""

import asyncio
import time
from dataclasses import dataclass, field

from .config import get_settings


@dataclass
class TokenSlot:
    """单个 Token 的状态"""
    token: str
    last_used: float = 0.0
    in_flight: int = 0
    total_used: int = 0
    total_errors: int = 0
    disabled: bool = False
    disable_reason: str = ""


class TokenPool:
    """
    多 Token 轮询池，支持冷却和禁用。

    策略：选择冷却完成且 in_flight 最少的 Token。
    无数据库依赖，所有状态存内存。
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._slots: list[TokenSlot] = [
            TokenSlot(token=t) for t in settings.sso_tokens
        ]
        self._cooldown = settings.cooldown
        self._lock = asyncio.Lock()

    @property
    def total(self) -> int:
        return len(self._slots)

    @property
    def available(self) -> int:
        return sum(1 for s in self._slots if not s.disabled)

    def get_slot_by_index(self, index: int) -> TokenSlot | None:
        """按索引获取 Token slot（用于指定 Token 查询额度等）"""
        if 0 <= index < len(self._slots):
            return self._slots[index]
        return None

    def status(self) -> list[dict]:
        """返回所有 Token 状态（脱敏）"""
        return [
            {
                "index": i,
                "token_prefix": s.token[:4] + "..." if len(s.token) > 4 else "***",
                "in_flight": s.in_flight,
                "total_used": s.total_used,
                "total_errors": s.total_errors,
                "disabled": s.disabled,
                "disable_reason": s.disable_reason,
                "cooldown_remaining": max(
                    0, self._cooldown - (time.monotonic() - s.last_used)
                ),
            }
            for i, s in enumerate(self._slots)
        ]

    async def acquire(self) -> TokenSlot | None:
        """
        获取一个可用 Token。

        优先选择：已冷却 > in_flight 最少 > 总使用次数最少。
        """
        async with self._lock:
            now = time.monotonic()
            candidates = [
                s for s in self._slots
                if not s.disabled
            ]
            if not candidates:
                return None

            # 优先选已冷却的
            cooled = [
                s for s in candidates
                if (now - s.last_used) >= self._cooldown
            ]
            pool = cooled if cooled else candidates

            # 选 in_flight 最少、总使用最少的
            best = min(pool, key=lambda s: (s.in_flight, s.total_used))
            best.in_flight += 1
            best.total_used += 1
            best.last_used = now
            return best

    async def release(self, slot: TokenSlot, *, error: bool = False) -> None:
        """释放 Token（请求完成后调用）"""
        async with self._lock:
            slot.in_flight = max(0, slot.in_flight - 1)
            if error:
                slot.total_errors += 1

    async def disable(self, slot: TokenSlot, reason: str = "") -> None:
        """禁用 Token（如 401/403 时）"""
        async with self._lock:
            slot.disabled = True
            slot.disable_reason = reason

    async def enable_all(self) -> int:
        """重新启用所有 Token"""
        async with self._lock:
            count = 0
            for s in self._slots:
                if s.disabled:
                    s.disabled = False
                    s.disable_reason = ""
                    count += 1
            return count


# 全局单例
_pool: TokenPool | None = None


def get_token_pool() -> TokenPool:
    global _pool
    if _pool is None:
        _pool = TokenPool()
    return _pool
