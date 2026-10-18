"""Error pages and error logging."""

import logging


def test_an_unhandled_error_is_logged_once_without_the_emailed_token(app, caplog):
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.route("/reset-password/<token>/boom")
    def boom(token):
        raise RuntimeError("kaboom")

    caplog.set_level(logging.ERROR)
    response = app.test_client().get("/reset-password/IjEyMyI.Zx9Q.secret-token/boom")
    assert response.status_code == 500

    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].getMessage() == "Unhandled error on GET /reset-password/[token]/boom"
    assert errors[0].exc_info[0] is RuntimeError  # the traceback is still there
    assert "secret-token" not in caplog.text
