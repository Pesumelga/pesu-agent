"""Windows Auto-Start Registration Management (Phase 11.6).

Manages user-scoped automatic execution of PESU upon Windows login via:
    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run

Invariants:
1. Strict user-scope: Uses HKCU only (no administrative privileges required).
2. Strict registry ownership: Only creates, queries, and deletes the 'PESU Agent' value.
   The Run key itself and all other third-party entries are strictly untouched.
3. Packaged executable only: Development source execution (is_frozen() == False)
   returns AutoStartStatus.UNSUPPORTED and will never register python.exe.
4. Canonical command structure: Quoted executable path + explicit '--startup' flag.
   e.g. '"C:\\Program Files\\PESU\\PESU.exe" --startup'
5. Pluggable registry backend: All operations route through RegistryAdapter, allowing
   in-memory test doubles so unit tests never mutate the real user's registry.
"""

from __future__ import annotations

from enum import Enum
import logging
import os
from pathlib import Path
import sys
from typing import Callable, Optional, Protocol, Tuple

from pesu_agent.runtime.resource_resolver import is_frozen

logger = logging.getLogger(__name__)

RUN_SUBKEY: str = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME: str = "PESU Agent"
STARTUP_CLI_FLAG: str = "--startup"


class AutoStartStatus(str, Enum):
    """Canonical statuses for Windows Auto-Start registration."""
    UNSUPPORTED = "UNSUPPORTED"
    DISABLED = "DISABLED"
    ENABLED = "ENABLED"
    STALE = "STALE"
    ERROR = "ERROR"


class RegistryAdapter(Protocol):
    """Protocol for interacting with Windows Registry Run key."""

    def read_value(self, subkey: str, value_name: str) -> Optional[str]:
        ...

    def write_value(self, subkey: str, value_name: str, value_data: str) -> None:
        ...

    def delete_value(self, subkey: str, value_name: str) -> bool:
        ...


class WinregRegistryAdapter:
    """Production Windows Registry adapter using built-in winreg."""

    def __init__(self, hive: Optional[int] = None) -> None:
        import winreg
        self._hive = hive if hive is not None else winreg.HKEY_CURRENT_USER

    def read_value(self, subkey: str, value_name: str) -> Optional[str]:
        import winreg
        try:
            with winreg.OpenKey(self._hive, subkey, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, value_name)
                return str(val) if val is not None else None
        except FileNotFoundError:
            return None
        except Exception as ex:
            logger.warning(f"Error reading registry value '{value_name}' from '{subkey}': {ex}")
            raise

    def write_value(self, subkey: str, value_name: str, value_data: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(self._hive, subkey, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, value_data)
        except Exception as ex:
            logger.error(f"Error writing registry value '{value_name}' in '{subkey}': {ex}")
            raise

    def delete_value(self, subkey: str, value_name: str) -> bool:
        import winreg
        try:
            with winreg.OpenKey(self._hive, subkey, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, value_name)
                return True
        except FileNotFoundError:
            return False
        except Exception as ex:
            logger.error(f"Error deleting registry value '{value_name}' from '{subkey}': {ex}")
            raise


class InMemoryRegistryAdapter:
    """In-memory test double to prevent touching real HKCU."""

    def __init__(self, initial_entries: Optional[dict[str, str]] = None) -> None:
        self.entries: dict[str, str] = dict(initial_entries or {})
        self.fail_on_read: bool = False
        self.fail_on_write: bool = False
        self.fail_on_delete: bool = False

    def read_value(self, subkey: str, value_name: str) -> Optional[str]:
        if self.fail_on_read:
            raise OSError("Simulated registry read failure")
        return self.entries.get(f"{subkey}\\{value_name}")

    def write_value(self, subkey: str, value_name: str, value_data: str) -> None:
        if self.fail_on_write:
            raise OSError("Simulated registry write failure")
        self.entries[f"{subkey}\\{value_name}"] = value_data

    def delete_value(self, subkey: str, value_name: str) -> bool:
        if self.fail_on_delete:
            raise OSError("Simulated registry delete failure")
        key = f"{subkey}\\{value_name}"
        if key in self.entries:
            del self.entries[key]
            return True
        return False


class WindowsStartupManager:
    """Manages Windows auto-start registration for packaged PESU executable."""

    def __init__(
        self,
        registry_adapter: Optional[RegistryAdapter] = None,
        executable_path_resolver: Optional[Callable[[], Path]] = None,
        is_frozen_checker: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._registry = registry_adapter or WinregRegistryAdapter()
        self._executable_path_resolver = executable_path_resolver or (lambda: Path(sys.executable).resolve())
        self._is_frozen_checker = is_frozen_checker or is_frozen

    def is_supported(self) -> bool:
        """Returns True only when running as a packaged / frozen executable."""
        try:
            return bool(self._is_frozen_checker())
        except Exception:
            return False

    def get_canonical_executable_path(self) -> Path:
        """Returns the canonical absolute path of the PESU executable."""
        return self._executable_path_resolver().resolve()

    def build_command_line(self, exe_path: Optional[Path] = None) -> str:
        """Constructs the canonical quoted command line for auto-start.

        Format: '"C:\\Path\\To\\PESU.exe" --startup'
        """
        path = (exe_path or self.get_canonical_executable_path()).resolve()
        return f'"{path}" {STARTUP_CLI_FLAG}'

    def parse_command_line(self, command: str) -> Optional[Path]:
        """Extracts the executable path from a registered command line string."""
        if not command:
            return None
        cmd = command.strip()
        if cmd.startswith('"'):
            end_quote = cmd.find('"', 1)
            if end_quote != -1:
                return Path(cmd[1:end_quote])
        parts = cmd.split()
        if parts:
            return Path(parts[0])
        return None

    def get_status(self) -> Tuple[AutoStartStatus, str]:
        """Evaluates the current auto-start registration state.

        Returns (status, human_readable_detail).
        """
        if not self.is_supported():
            return AutoStartStatus.UNSUPPORTED, "자동 시작 설정은 배포된 PESU 앱에서 사용할 수 있습니다."

        try:
            current_value = self._registry.read_value(RUN_SUBKEY, VALUE_NAME)
        except Exception:
            return AutoStartStatus.ERROR, "자동 시작 설정을 확인하지 못했습니다."

        if current_value is None:
            return AutoStartStatus.DISABLED, "꺼짐"

        registered_exe = self.parse_command_line(current_value)
        canonical_exe = self.get_canonical_executable_path()

        if registered_exe is not None:
            try:
                if registered_exe.resolve() == canonical_exe:
                    return AutoStartStatus.ENABLED, "켜짐"
                else:
                    return AutoStartStatus.STALE, "경로 변경 감지 (재설정 필요)"
            except Exception:
                if str(registered_exe).lower() == str(canonical_exe).lower():
                    return AutoStartStatus.ENABLED, "켜짐"
                return AutoStartStatus.STALE, "경로 변경 감지 (재설정 필요)"

        return AutoStartStatus.STALE, "경로 변경 감지 (재설정 필요)"

    def enable(self) -> Tuple[bool, str]:
        """Registers or repairs the PESU startup command in HKCU Run.

        Returns (success, user_facing_message).
        """
        if not self.is_supported():
            return False, "자동 시작 설정은 배포된 PESU 앱에서 사용할 수 있습니다."

        try:
            cmd = self.build_command_line()
            self._registry.write_value(RUN_SUBKEY, VALUE_NAME, cmd)
            logger.info(f"PESU auto-start enabled in registry: '{cmd}'")
            return True, "Windows 로그인 시 PESU가 자동으로 실행되도록 설정되었습니다."
        except Exception as ex:
            logger.error(f"Failed to enable auto-start in registry: {ex}")
            return False, "자동 시작 설정을 변경하지 못했습니다. 다시 시도해 주세요."

    def disable(self) -> Tuple[bool, str]:
        """Deletes the PESU startup value from HKCU Run.

        Returns (success, user_facing_message).
        """
        if not self.is_supported():
            return True, "자동 시작이 비활성화되었습니다."

        try:
            self._registry.delete_value(RUN_SUBKEY, VALUE_NAME)
            logger.info("PESU auto-start disabled in registry.")
            return True, "Windows 로그인 시 자동 실행이 해제되었습니다."
        except Exception as ex:
            logger.error(f"Failed to disable auto-start in registry: {ex}")
            return False, "자동 시작 설정을 변경하지 못했습니다. 다시 시도해 주세요."
