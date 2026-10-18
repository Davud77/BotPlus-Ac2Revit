import json
import urllib.request
from pathlib import Path

def call(command, parameters=None, tapir=True):
    payload = {"command": "API.ExecuteAddOnCommand", "parameters": {
        "addOnCommandId": {"commandNamespace": "TapirCommand", "commandName": command},
        "addOnCommandParameters": parameters or {}}} if tapir else {"command": "API." + command, "parameters": parameters or {}}
    request = urllib.request.Request("http://127.0.0.1:19723", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(request, timeout=90).read())

if __name__ == "__main__":
    out = Path("artifacts/probe")
    out.mkdir(parents=True, exist_ok=True)
    for name, params, tapir in [("GetProductInfo", None, False), ("GetAddOnVersion", None, True), ("GetProjectInfo", None, True), ("GetStories", None, True), ("GetAllElements", None, True)]:
        result = call(name, params, tapir)
        (out / (name + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(name, str(result)[:1500])
    for kind in ["Wall", "Slab", "Door", "Window", "Zone", "Column", "Beam", "Object"]:
        result = call("GetElementsByType", {"elementType": kind}, False)
        ids = result.get("result", {}).get("elements", [])
        print(kind, "count", len(ids))
        if ids:
            result = call("GetDetailsOfElements", {"elements": ids[:2], "fields": ["type", "id", "floorIndex", "details"]})
            (out / (kind + ".json")).write_text(json.dumps({"ids": ids[:2], "response": result}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=True)[:4000])
