"""Command inbox scanning across all Google Tasks lists."""

from app.connectors.google import tasks as gt


class _Req:
    def __init__(self, val):
        self.val = val

    def execute(self):
        return self.val


class _FakeTasksAPI:
    """Minimal google-api-style stub: tasklists().list() + tasks().list()."""

    def __init__(self, lists, tasks_by_list):
        self._lists = lists
        self._tasks_by_list = tasks_by_list

    def tasklists(self):
        api = self

        class _TL:
            def list(self, maxResults=100):
                return _Req({"items": api._lists})

        return _TL()

    def tasks(self):
        api = self

        class _T:
            def list(self, tasklist=None, showCompleted=False, showHidden=False, maxResults=100):
                return _Req({"items": api._tasks_by_list.get(tasklist, [])})

        return _T()


def test_pending_commands_all_scans_every_list_and_filters_personal():
    api = _FakeTasksAPI(
        lists=[{"id": "d", "title": "My Tasks"}, {"id": "x", "title": "Other"}],
        tasks_by_list={
            "d": [
                {"id": "cmd1", "notes": '{"path": "/api/notion/create-pages"}'},
                {"id": "personal", "title": "buy milk", "notes": ""},          # ignored
                {"id": "done1", "notes": "✓ already processed"},               # ignored
            ],
            "x": [{"id": "cmd2", "title": '{"path": "/api/intray"}', "notes": ""}],
        },
    )
    out = gt.pending_commands_all(api)
    assert {t["id"] for t in out} == {"cmd1", "cmd2"}        # only JSON-shaped, both lists
    assert {t["_tasklist"] for t in out} == {"d", "x"}        # tagged with their list
