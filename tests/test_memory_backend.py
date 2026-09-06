"""memory_20250818 tool backend: rendering, commands, error paths, guards.

See wiki/memory-design.md - read is "render everything into the system prompt",
write is the tool commands, with a 4000-char total budget and flat paths only.
"""
import pytest

from family_line_bot.services.memory import MemoryToolBackend, _TOTAL_LIMIT

CHAT = "Cgroup"


@pytest.fixture
def backend(store):
    return MemoryToolBackend(store, CHAT)


# --- render ---------------------------------------------------------------

def test_render_is_empty_string_when_no_files(backend):
    assert backend.render() == ""


def test_render_includes_every_file_with_a_path_header(backend, store):
    store.write_memory_file(CHAT, "members.md", "- Alice: 媽媽")
    store.write_memory_file(CHAT, "dates.md", "- 10/5 生日")
    rendered = backend.render()
    assert "=== /memories/members.md ===" in rendered
    assert "=== /memories/dates.md ===" in rendered
    assert "- Alice: 媽媽" in rendered


def test_render_sorts_files_deterministically(backend, store):
    store.write_memory_file(CHAT, "z.md", "z")
    store.write_memory_file(CHAT, "a.md", "a")
    rendered = backend.render()
    assert rendered.index("a.md") < rendered.index("z.md")


def test_render_is_scoped_to_the_chat(store):
    store.write_memory_file("Cother", "members.md", "secret")
    assert MemoryToolBackend(store, CHAT).render() == ""


# --- create ---------------------------------------------------------------

def test_create_writes_the_file(backend, store):
    out, is_error = backend.handle(
        command="create", path="/memories/members.md", file_text="- Alice"
    )
    assert is_error is False
    assert "created successfully" in out
    assert store.list_memory_files(CHAT)["members.md"] == "- Alice"


@pytest.mark.parametrize(
    "path",
    [
        "/memories/../etc/passwd",
        "/memories/nested/file.md",
        "/etc/passwd",
        "/memories/",
        "/memories",
    ],
)
def test_create_rejects_paths_outside_the_flat_root(backend, store, path):
    out, is_error = backend.handle(command="create", path=path, file_text="x")
    assert is_error is True
    assert store.list_memory_files(CHAT) == {}


def test_create_refuses_to_exceed_the_total_budget(backend, store):
    out, is_error = backend.handle(
        command="create", path="/memories/big.md", file_text="x" * (_TOTAL_LIMIT + 1)
    )
    assert is_error is True
    assert "memory is full" in out
    assert store.list_memory_files(CHAT) == {}


def test_budget_counts_other_files_but_not_the_one_being_replaced(backend, store):
    store.write_memory_file(CHAT, "a.md", "x" * 3000)
    # Rewriting a.md at the same size stays inside budget...
    _, is_error = backend.handle(command="create", path="/memories/a.md", file_text="y" * 3000)
    assert is_error is False
    # ...but a *second* 3000-char file blows it.
    _, is_error = backend.handle(command="create", path="/memories/b.md", file_text="z" * 3000)
    assert is_error is True


# --- view -----------------------------------------------------------------

def test_view_root_lists_all_files(backend, store):
    store.write_memory_file(CHAT, "members.md", "- Alice")
    out, is_error = backend.handle(command="view", path="/memories")
    assert is_error is False
    assert "/memories/members.md" in out


def test_view_file_returns_numbered_lines(backend, store):
    store.write_memory_file(CHAT, "a.md", "first\nsecond")
    out, is_error = backend.handle(command="view", path="/memories/a.md")
    assert is_error is False
    lines = out.splitlines()
    assert lines[1].strip().startswith("1") and lines[1].endswith("\tfirst")
    assert lines[2].strip().startswith("2") and lines[2].endswith("\tsecond")


def test_view_missing_file_is_an_error(backend):
    out, is_error = backend.handle(command="view", path="/memories/missing.md")
    assert is_error is True
    assert "does not exist" in out


# --- str_replace ----------------------------------------------------------

def test_str_replace_edits_the_file(backend, store):
    store.write_memory_file(CHAT, "a.md", "- Alice 喜歡貓")
    out, is_error = backend.handle(
        command="str_replace", path="/memories/a.md", old_str="貓", new_str="狗"
    )
    assert is_error is False
    assert store.list_memory_files(CHAT)["a.md"] == "- Alice 喜歡狗"


def test_str_replace_with_no_match_reports_and_changes_nothing(backend, store):
    store.write_memory_file(CHAT, "a.md", "- Alice")
    out, is_error = backend.handle(
        command="str_replace", path="/memories/a.md", old_str="Bob", new_str="Carol"
    )
    assert is_error is True
    assert "No replacement was performed" in out
    assert store.list_memory_files(CHAT)["a.md"] == "- Alice"


def test_str_replace_with_multiple_matches_refuses(backend, store):
    store.write_memory_file(CHAT, "a.md", "cat\ncat")
    out, is_error = backend.handle(
        command="str_replace", path="/memories/a.md", old_str="cat", new_str="dog"
    )
    assert is_error is True
    assert "Multiple occurrences" in out
    assert store.list_memory_files(CHAT)["a.md"] == "cat\ncat"


def test_str_replace_on_a_missing_file_is_an_error(backend):
    _, is_error = backend.handle(
        command="str_replace", path="/memories/missing.md", old_str="a", new_str="b"
    )
    assert is_error is True


# --- insert ---------------------------------------------------------------

def test_insert_at_line_zero_prepends(backend, store):
    store.write_memory_file(CHAT, "a.md", "second")
    _, is_error = backend.handle(
        command="insert", path="/memories/a.md", insert_line=0, insert_text="first"
    )
    assert is_error is False
    assert store.list_memory_files(CHAT)["a.md"] == "first\nsecond"


def test_insert_at_end_of_file_appends(backend, store):
    store.write_memory_file(CHAT, "a.md", "one\ntwo")
    _, is_error = backend.handle(
        command="insert", path="/memories/a.md", insert_line=2, insert_text="three"
    )
    assert is_error is False
    assert store.list_memory_files(CHAT)["a.md"] == "one\ntwo\nthree"


def test_insert_out_of_range_is_rejected(backend, store):
    store.write_memory_file(CHAT, "a.md", "one")
    out, is_error = backend.handle(
        command="insert", path="/memories/a.md", insert_line=99, insert_text="x"
    )
    assert is_error is True
    assert "Invalid" in out
    assert store.list_memory_files(CHAT)["a.md"] == "one"


# --- delete / rename ------------------------------------------------------

def test_delete_removes_the_file(backend, store):
    store.write_memory_file(CHAT, "a.md", "x")
    _, is_error = backend.handle(command="delete", path="/memories/a.md")
    assert is_error is False
    assert store.list_memory_files(CHAT) == {}


def test_delete_of_the_root_directory_is_refused(backend, store):
    store.write_memory_file(CHAT, "a.md", "x")
    out, is_error = backend.handle(command="delete", path="/memories")
    assert is_error is True
    assert store.list_memory_files(CHAT) == {"a.md": "x"}


def test_rename_moves_content_and_drops_the_old_name(backend, store):
    store.write_memory_file(CHAT, "a.md", "content")
    _, is_error = backend.handle(
        command="rename", old_path="/memories/a.md", new_path="/memories/b.md"
    )
    assert is_error is False
    assert store.list_memory_files(CHAT) == {"b.md": "content"}


def test_rename_onto_an_existing_file_is_refused(backend, store):
    store.write_memory_file(CHAT, "a.md", "A")
    store.write_memory_file(CHAT, "b.md", "B")
    out, is_error = backend.handle(
        command="rename", old_path="/memories/a.md", new_path="/memories/b.md"
    )
    assert is_error is True
    assert store.list_memory_files(CHAT) == {"a.md": "A", "b.md": "B"}


# --- dispatch guards ------------------------------------------------------

def test_unknown_command_is_an_error(backend):
    out, is_error = backend.handle(command="chmod", path="/memories/a.md")
    assert is_error is True
    assert "unknown command" in out


def test_handler_never_raises_on_bad_arguments(backend, store):
    store.write_memory_file(CHAT, "a.md", "x")
    # A non-int insert_line must come back as an error tuple the tool loop can
    # relay to the model, never as an exception.
    out, is_error = backend.handle(command="insert", path="/memories/a.md", insert_line="two")
    assert is_error is True
