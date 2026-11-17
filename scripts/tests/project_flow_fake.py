"""Test fake for the project_flow transport: records calls, serves canned responses."""
import os
import sys

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from project_flow import GhError  # noqa: E402


class FakeTransport:
    """Serves responses in order, or by matcher; records every call and mutation.

    responses: list of (matcher, response). A matcher is None (matches any call), a
    string (substring of the query or path) or a callable taking the call dict. A
    response that is an Exception instance is raised (use GhError for gh failures).
    A matched entry is consumed once served.
    """

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []
        self.mutations = []
        self._hooks = []

    def on_call(self, hook):
        """Register hook(call) to run on every call, before the response is chosen."""
        self._hooks.append(hook)

    @staticmethod
    def _matches(matcher, call):
        if matcher is None:
            return True
        if callable(matcher):
            return bool(matcher(call))
        text = call.get("query") or call.get("path") or ""
        return matcher in text

    def _serve(self, call):
        self.calls.append(call)
        if call["kind"] == "rest" and call["method"].upper() != "GET":
            self.mutations.append(call)
        elif call["kind"] == "graphql" and call["query"].lstrip().startswith("mutation"):
            self.mutations.append(call)
        for hook in self._hooks:
            hook(call)
        for index, (matcher, response) in enumerate(self.responses):
            if self._matches(matcher, call):
                del self.responses[index]
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError("FakeTransport: no response for %r" % (call,))

    def graphql(self, query, variables=None):
        return self._serve({"kind": "graphql", "query": query, "variables": variables or {}})

    def rest(self, method, path, body=None):
        return self._serve({"kind": "rest", "method": method, "path": path, "body": body})


__all__ = ["FakeTransport", "GhError"]
