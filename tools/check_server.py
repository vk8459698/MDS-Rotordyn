"""End-to-end check that the served app answers and its callbacks fire.

    python main.py --web 8052        (in one terminal)
    python tools/check_server.py     (in another)
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8052"


def get(path: str) -> str:
    with urllib.request.urlopen(BASE + path, timeout=20) as response:
        return response.read().decode()


def post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


def main() -> int:
    index = get("/")
    print(f"index page          {len(index):>7} bytes   react mount: "
          f"{'react-entry-point' in index}")

    layout = get("/_dash-layout")
    controls = ["upload", "btn-sample", "segment", "rotation", "rpm-range",
                "options", "trend-channels", "clearance", "tabs", "graph"]
    missing = [c for c in controls if f'"{c}"' not in layout]
    print(f"layout              {len(layout):>7} bytes   missing controls: {missing or 'none'}")

    dependencies = json.loads(get("/_dash-dependencies"))
    print(f"callbacks           {len(dependencies):>7}")
    for dependency in dependencies:
        print(f"    -> {dependency['output']}")

    # Dash keys its callback map by the exact output string it advertises, so
    # look each one up rather than reconstructing the format.
    def key_for(prop: str) -> str:
        return next(d["output"] for d in dependencies if prop in d["output"])

    # Fire the sample-data callback the way the browser does.
    loaded = post(
        "/_dash-update-component",
        {
            "output": key_for("session.data"),
            "outputs": [
                {"id": "session", "property": "data"},
                {"id": "banner", "property": "children"},
                {"id": "banner", "property": "style"},
            ],
            "inputs": [
                {"id": "upload", "property": "contents", "value": None},
                {"id": "btn-sample", "property": "n_clicks", "value": 1},
            ],
            "state": [{"id": "upload", "property": "filename", "value": None}],
            "changedPropIds": ["btn-sample.n_clicks"],
        },
    )
    response = loaded["response"]
    token = response["session"]["data"]
    print(f"\nsample data load    {response['banner']['children']}")
    print(f"session token       {token[:12]}...")

    for tab in ("polar", "bode", "trend", "centreline"):
        figure = post(
            "/_dash-update-component",
            {
                "output": key_for("graph.figure"),
                "outputs": [
                    {"id": "graph", "property": "figure"},
                    {"id": "status", "property": "children"},
                ],
                "inputs": [
                    {"id": "session", "property": "data", "value": token},
                    {"id": "tabs", "property": "value", "value": tab},
                    {"id": "segment", "property": "value", "value": "Coast-up (startup)"},
                    {"id": "rotation", "property": "value", "value": 45},
                    {"id": "rpm-range", "property": "value", "value": [0, 3614]},
                    {"id": "options", "property": "value", "value": ["hide", "labels"]},
                    {"id": "sr-low", "property": "value", "value": 300},
                    {"id": "sr-high", "property": "value", "value": 700},
                    {"id": "trend-channels", "property": "value", "value": ["1X amplitude"]},
                    {"id": "clearance", "property": "value", "value": 12},
                ],
                "changedPropIds": ["tabs.value"],
            },
        )["response"]
        traces = len(figure["graph"]["figure"]["data"])
        status = figure["status"]["children"][0]["props"]["children"]
        print(f"{tab:<12} {traces} traces   {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
