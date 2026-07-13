import re

# Total memory budget per chat: bounds the system-prompt injection cost.
_TOTAL_LIMIT = 4000
_ROOT = "/memories"


def _numbered(content: str) -> str:
    lines = content.rstrip("\n").split("\n")
    return "\n".join(f"{i + 1:6d}\t{line}" for i, line in enumerate(lines))


class MemoryToolBackend:
    """Handles memory_20250818 tool commands against a store's memory files.

    Paths map /memories/<name> onto flat per-chat files; anything nested or
    escaping the root is rejected (path traversal protection).
    """

    def __init__(self, store, chat_id: str):
        self._store = store
        self._chat_id = chat_id

    def _resolve(self, path: str) -> str | None:
        if not isinstance(path, str) or not path.startswith(_ROOT):
            return None
        name = path[len(_ROOT):].lstrip("/")
        if not name or not re.fullmatch(r"[\w.\-]+", name):
            return None
        return name

    def _files(self) -> dict[str, str]:
        return self._store.list_memory_files(self._chat_id)

    def _check_budget(self, files: dict[str, str], name: str, content: str) -> str | None:
        total = sum(len(v) for k, v in files.items() if k != name) + len(content)
        if total > _TOTAL_LIMIT:
            return (
                f"Error: memory is full ({total}/{_TOTAL_LIMIT} chars). "
                "Delete outdated entries or condense existing files first."
            )
        return None

    def handle(self, command: str = "", path: str = "", **kw) -> tuple[str, bool]:
        try:
            return self._dispatch(command, path, **kw)
        except Exception as e:
            return f"Error: {e}", True

    def _dispatch(self, command: str, path: str, **kw) -> tuple[str, bool]:
        files = self._files()

        if command == "view":
            if path.rstrip("/") == _ROOT:
                listing = [f"{_TOTAL_LIMIT / 1024:.1f}K\t{_ROOT}"] + [
                    f"{len(c) / 1024:.1f}K\t{_ROOT}/{n}" for n, c in sorted(files.items())
                ]
                return (
                    f"Here're the files and directories up to 2 levels deep in {_ROOT}, "
                    "excluding hidden items and node_modules:\n" + "\n".join(listing),
                    False,
                )
            name = self._resolve(path)
            if name is None or name not in files:
                return f"The path {path} does not exist. Please provide a valid path.", True
            return f"Here's the content of {path} with line numbers:\n{_numbered(files[name])}", False

        if command == "create":
            name = self._resolve(path)
            if name is None:
                return f"Error: invalid path {path}; use {_ROOT}/<filename>", True
            content = kw.get("file_text", "")
            if err := self._check_budget(files, name, content):
                return err, True
            self._store.write_memory_file(self._chat_id, name, content)
            return f"File created successfully at: {path}", False

        if command == "str_replace":
            name = self._resolve(path)
            if name is None or name not in files:
                return f"Error: The path {path} does not exist. Please provide a valid path.", True
            old, new = kw.get("old_str", ""), kw.get("new_str", "")
            content = files[name]
            count = content.count(old)
            if count == 0:
                return f"No replacement was performed, old_str `{old}` did not appear verbatim in {path}.", True
            if count > 1:
                return (
                    f"No replacement was performed. Multiple occurrences of old_str `{old}` "
                    "found. Please ensure it is unique",
                    True,
                )
            updated = content.replace(old, new, 1)
            if err := self._check_budget(files, name, updated):
                return err, True
            self._store.write_memory_file(self._chat_id, name, updated)
            return "The memory file has been edited.", False

        if command == "insert":
            name = self._resolve(path)
            if name is None or name not in files:
                return f"Error: The path {path} does not exist", True
            lines = files[name].split("\n")
            at = kw.get("insert_line", 0)
            if not isinstance(at, int) or at < 0 or at > len(lines):
                return (
                    f"Error: Invalid `insert_line` parameter: {at}. It should be within "
                    f"the range of lines of the file: [0, {len(lines)}]",
                    True,
                )
            lines.insert(at, str(kw.get("insert_text", "")).rstrip("\n"))
            updated = "\n".join(lines)
            if err := self._check_budget(files, name, updated):
                return err, True
            self._store.write_memory_file(self._chat_id, name, updated)
            return f"The file {path} has been edited.", False

        if command == "delete":
            if path.rstrip("/") == _ROOT:
                return f"Error: cannot delete the {_ROOT} directory itself", True
            name = self._resolve(path)
            if name is None or name not in files:
                return f"Error: The path {path} does not exist", True
            self._store.delete_memory_file(self._chat_id, name)
            return f"Successfully deleted {path}", False

        if command == "rename":
            old_path, new_path = kw.get("old_path", ""), kw.get("new_path", "")
            old = self._resolve(old_path)
            new = self._resolve(new_path)
            if old is None or old not in files:
                return f"Error: The path {old_path} does not exist", True
            if new is None:
                return f"Error: invalid destination {new_path}", True
            if new in files:
                return f"Error: The destination {new_path} already exists", True
            self._store.write_memory_file(self._chat_id, new, files[old])
            self._store.delete_memory_file(self._chat_id, old)
            return f"Successfully renamed {old_path} to {new_path}", False

        return f"Error: unknown command {command}", True

    def render(self) -> str:
        """Render all memory files for system-prompt injection ('' when empty)."""
        files = self._files()
        if not files:
            return ""
        parts = [f"=== {_ROOT}/{name} ===\n{content.rstrip()}" for name, content in sorted(files.items())]
        return "\n\n".join(parts)
