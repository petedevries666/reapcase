from unittest.mock import Mock, call, patch

from stadium_reaper_bridge.editor.app import _present_migration_error


def test_build_error_logs_traceback_before_showing_modal():
    try:
        raise AttributeError("bad optional metadata")
    except AttributeError as error:
        saved_error = error
        parent = Mock()
        events = Mock()
        with (patch("stadium_reaper_bridge.editor.app.LOG.exception",
                    side_effect=lambda *args, **kwargs: events("log")) as logged,
              patch("stadium_reaper_bridge.editor.app.messagebox.showerror",
                    side_effect=lambda *args, **kwargs: events("modal")) as shown):
            _present_migration_error(error, "Build analysis failed", parent)

    assert events.call_args_list == [call("log"), call("modal")]
    logged.assert_called_once()
    assert logged.call_args.args == ("Stadium %s", "build analysis failed")
    assert logged.call_args.kwargs["exc_info"][1] is saved_error
    shown.assert_called_once_with("Build analysis failed", "bad optional metadata", parent=parent)
