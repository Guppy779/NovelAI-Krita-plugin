"""Asynchronous HTTP client for NovelAI, built on QNetworkAccessManager.

Requests run on Qt's event loop, so Krita's UI never blocks and callbacks
are always invoked on the main thread.
"""

import json

from . import api
from .qt import QByteArray, QNetworkAccessManager, QNetworkReply, QNetworkRequest, QObject, QUrl

USER_AGENT = b"NovelAI-Krita-plugin/0.1"
GENERATE_TIMEOUT_MS = 5 * 60 * 1000
DEFAULT_TIMEOUT_MS = 30 * 1000
SUGGEST_TIMEOUT_MS = 10 * 1000


class Job:
    """Handle for a running request; call ``cancel()`` to abort it."""

    def __init__(self, reply):
        self._reply = reply
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        if self._reply is not None and self._reply.isRunning():
            self._reply.abort()


class NovelAIClient(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)

    def _request(self, url, token, timeout_ms):
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"Authorization", b"Bearer " + token.strip().encode("utf-8"))
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"User-Agent", USER_AGENT)
        if hasattr(request, "setTransferTimeout"):  # Qt 5.15+
            request.setTransferTimeout(timeout_ms)
        return request

    def _watch(self, reply, on_success, on_error, parse):
        job = Job(reply)

        def finished():
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            body = bytes(reply.readAll())
            error = reply.error()
            error_text = reply.errorString()
            reply.deleteLater()
            job._reply = None
            if job.cancelled or error == QNetworkReply.NetworkError.OperationCanceledError:
                return
            if status is not None and 200 <= int(status) < 300:
                try:
                    result = parse(body)
                except Exception as exc:  # Malformed response.
                    on_error("Could not read NovelAI's response: %s" % exc)
                    return
                on_success(result)
            elif status:
                on_error(api.error_message(int(status), body))
            else:
                on_error("Could not reach NovelAI: %s" % error_text)

        reply.finished.connect(finished)
        return job

    def generate(self, token, payload, on_success, on_error):
        """POST /ai/generate-image. ``on_success`` receives [(bytes, seed)]."""
        request = self._request(api.GENERATE_URL, token, GENERATE_TIMEOUT_MS)
        body = QByteArray(json.dumps(payload).encode("utf-8"))
        reply = self._manager.post(request, body)
        return self._watch(reply, on_success, on_error, api.parse_images)

    def suggest_tags(self, token, model, query, lang, on_success, on_error):
        """GET /ai/generate-image/suggest-tags. ``on_success`` receives [TagSuggestion]."""
        url = api.suggest_tags_url(model, query, lang)
        request = self._request(url, token, SUGGEST_TIMEOUT_MS)
        reply = self._manager.get(request)
        return self._watch(reply, on_success, on_error, api.parse_tag_suggestions)

    def check_subscription(self, token, on_success, on_error):
        """GET /user/subscription. ``on_success`` receives the Anlas balance or None."""
        request = self._request(api.SUBSCRIPTION_URL, token, DEFAULT_TIMEOUT_MS)
        reply = self._manager.get(request)
        return self._watch(reply, on_success, on_error, api.parse_anlas)
