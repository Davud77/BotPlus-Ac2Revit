"""Summarize saved import evidence; does not open or modify a Revit model."""
import argparse
import collections
import csv
import json
import statistics
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def csv_write(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--wall-audit", type=Path, help="Final GeometryAudit JSONL; needed for a resumed import")
    args = parser.parse_args()
    manifest = load(args.manifest)
    elements = {e["sourceGuid"].upper(): e for e in manifest["elements"]}
    types = {t["key"]: t for t in manifest["types"]}
    levels = {l["key"]: l["name"] for l in manifest["levels"]}
    walls_report = load(args.run / "stages/wallsSlabsMorphs.json")
    spatial_report = load(args.run / "stages/wallJoinsAndRooms.json")
    args.out.mkdir(parents=True, exist_ok=True)
    readback = [json.loads(line) for line in args.wall_audit.read_text(encoding="utf-8-sig").splitlines()] if args.wall_audit else None
    represented = {r['sourceGuid'].upper() for r in readback} if readback is not None else set()
    walls = []
    for item in walls_report["Items"]:
        if item["Kind"] != "Wall" or item["Status"] != "blocked":
            continue
        if item['SourceKey'].upper() in represented:
            continue  # A blocked native adapter is not a missing fallback wall.
        element = elements[item["SourceKey"].upper()]
        typ = types.get(element["payload"].get("typeKey"), {})
        walls.append({"guid": item["SourceKey"], "mark": element["mark"],
                      "level": levels[element["levelKey"]], "type": typ.get("name", ""),
                      "reason": item["Message"]})
    rooms = []
    for area in spatial_report["ZoneAreas"]:
        element = elements[area["SourceGuid"].upper()]
        rooms.append({"guid": area["SourceGuid"], "name": element["payload"].get("name", ""),
                      "number": element["payload"].get("number", ""),
                      "level": levels[element["levelKey"]], "status": area["Status"],
                      "source_polygon_m2": area["SourceAreaSquareMeters"],
                      "revit_m2": area["RevitAreaSquareMeters"],
                      "absolute_difference_m2": area["AbsoluteDifferenceSquareMeters"],
                      "signed_difference_m2": area["RevitAreaSquareMeters"] - area["SourceAreaSquareMeters"],
                      "tolerance_m2": area["ToleranceSquareMeters"]})
    csv_write(args.out / "missing-walls.csv", walls, ["guid", "mark", "level", "type", "reason"])
    csv_write(args.out / "room-areas.csv", sorted(rooms, key=lambda r: r["absolute_difference_m2"], reverse=True),
              ["guid", "name", "number", "level", "status", "source_polygon_m2", "revit_m2",
               "absolute_difference_m2", "signed_difference_m2", "tolerance_m2"])
    over = [r for r in rooms if r["status"] == "overTolerance"]
    summary = {
        "evidence": {"manifest": str(args.manifest.resolve()), "run": str(args.run.resolve())},
        "walls": {"source": sum(e["kind"] == "Wall" for e in elements.values()),
                  "created": sum(i["Kind"] == "Wall" and i["Status"] == "created" for i in walls_report["Items"]),
                  "blocked": len(walls), "reasons": dict(collections.Counter(r["reason"] for r in walls))},
        "rooms": {"source": len(rooms), "statuses": dict(collections.Counter(r["status"] for r in rooms)),
                  "comparison": "Revit Room.Area versus source polygon including holes; not source regulatory/net area",
                  "over_tolerance": {"count": len(over),
                      "revit_larger": sum(r["signed_difference_m2"] > 0 for r in over),
                      "min_m2": min((r["absolute_difference_m2"] for r in over), default=None),
                      "median_m2": statistics.median(r["absolute_difference_m2"] for r in over) if over else None,
                      "max_m2": max((r["absolute_difference_m2"] for r in over), default=None)}},
        "wall_end_checks": dict(collections.Counter(r["Status"] for r in spatial_report["WallJoins"])),
        "join_stage_summary": [i["Message"] for i in spatial_report["Items"] if i["SourceKey"] == "wall-topology" and i["Status"] in ("joined", "retained")],
        "limitations": ["Join checks were recorded during the join stage, not a final independent whole-model junction audit." if spatial_report["WallJoins"] else "No new junction checks in this run; existing joins were preserved or joining was disabled.",
                        "Saved import reports do not prove complete surface or geometry equivalence."]
    }
    if readback is not None:
        wall_sources={k:e for k,e in elements.items() if e['kind']=='Wall'}
        actual={r['sourceGuid'].upper():r for r in readback}
        if len(actual)!=len(readback):raise ValueError('Duplicate wall readback GUID')
        representation=[]
        for key,element in wall_sources.items():
            row=actual.get(key)
            comments='; '.join(v for v in row.get('parameters',{}).values() if 'НЕПОЛНОЦЕННАЯ' in v) if row else ''
            representation.append({'guid':key,'mark':element['mark'],'level':levels[element['levelKey']],
                'elementId':row['elementId'] if row else '', 'revitClass':row['elementClass'] if row else 'MISSING',
                'incomplete':bool(comments),'annotation':comments})
        csv_write(args.out/'wall-representations.csv',representation,['guid','mark','level','elementId','revitClass','incomplete','annotation'])
        summary['walls']={'source':len(wall_sources),'represented':sum(k in actual for k in wall_sources),
            'native':sum(actual.get(k,{}).get('elementClass')=='Wall' for k in wall_sources),
            'geometryFallback':sum(actual.get(k,{}).get('elementClass')=='DirectShape' for k in wall_sources),
            'missing':len(wall_sources.keys()-actual.keys()),'foreignGuids':sorted(actual.keys()-wall_sources.keys()),
            'explicitlyIncomplete':sum(r['incomplete'] for r in representation),
            'nativeCreatedInThisRun':sum(i['Kind']=='Wall' and i['Status']=='created' for i in walls_report['Items']),
            'readback':str(args.wall_audit.resolve())}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
