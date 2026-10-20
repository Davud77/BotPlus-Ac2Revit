using Autodesk.Revit.DB;
using System.Text.Json;

namespace Ac2Revit;

/// <summary>Independent readback of actual grid curves, native cut links and remaining host/void intersection.</summary>
public static class PipelineAudit
{
    public static object Write(Document doc,string pipelinePath,string output,Action<string,int,int>? progress=null)
    {
        if(File.Exists(output))throw new IOException("Audit output exists");
        var plan=JsonSerializer.Deserialize<PipelineRunner.Plan>(File.ReadAllText(pipelinePath),Json.Options)!;
        string Resolve(string p)=>Path.GetFullPath(p,Path.GetDirectoryName(pipelinePath)!);
        var grids=Manifest.Load(Resolve(plan.GridsManifest));string project=grids.Source.ProjectId;var index=Storage.Index(doc,project);
        var axes=new List<object>();int axisPass=0,labelPass=0;
        foreach(var record in grids.Elements.Where(e=>e.Kind=="Grid"))
        {
            var curve=record.Payload.GetProperty("curve").Read<Edge>();string status="missing";double? deviation=null;string name="";
            if(index.GetValueOrDefault(record.SourceGuid) is Grid grid)
            {
                var actual=grid.Curve;var a=NativeGeometry.At(curve.Start);var b=NativeGeometry.At(curve.End);
                // Grid vertical extents do not move its XY axis. Ignore the
                // display-plane elevation when checking a source plan axis.
                double XY(XYZ p,XYZ q)=>new XYZ(p.X-q.X,p.Y-q.Y,0).GetLength();
                deviation=UnitUtils.ConvertFromInternalUnits(Math.Min(Math.Max(XY(actual.GetEndPoint(0),a),XY(actual.GetEndPoint(1),b)),Math.Max(XY(actual.GetEndPoint(1),a),XY(actual.GetEndPoint(0),b))),UnitTypeId.Meters);
                status=deviation<=1e-6?"curveVerified":"curveMismatch";name=grid.Name;if(status=="curveVerified")axisPass++;
            }
            bool labelVerified=GridLabels.Visible(name)==record.Mark;if(labelVerified)labelPass++;
            axes.Add(new{sourceGuid=record.SourceGuid,status,deviationMeters=deviation,revitName=name,sourceLabel=record.Mark,visibleLabelVerified=labelVerified});
        }
        using var openings=JsonDocument.Parse(File.ReadAllText(Resolve(plan.OpeningsPlan)));var records=openings.RootElement.GetProperty("elements").EnumerateArray().ToList();
        var checks=new List<object>();var outcomes=new Dictionary<string,int>();int done=0;
        foreach(var record in records)
        {
            string key=record.S("sourceGuid"),status="missingCut",error="";double? overlap=null;long? cutId=null,hostId=null,bodyId=null;
            try
            {
                string cutKey=record.B("emptyOpening")?key:"opening:"+key;
                if(!record.B("emptyOpening") && index.GetValueOrDefault(key) is Element filling)
                {
                    bodyId=filling.Id.Value;
                    var fillingCategory=record.S("kind")=="Door"?BuiltInCategory.OST_Doors:BuiltInCategory.OST_Windows;
                    status=filling.Category?.Id==new ElementId(fillingCategory)?"fillingPresentWithoutNativeCut":"wrongFillingCategory";
                }
                if(index.GetValueOrDefault(cutKey) is Element cutter)
                {
                    cutId=cutter.Id.Value;
                    var hostElement=index.GetValueOrDefault(record.S("hostGuid"))??throw new InvalidDataException("Source opening host missing");
                    hostId=hostElement.Id.Value;
                    bool relation=cutter is Opening native?native.Host?.Id==hostElement.Id:InstanceVoidCutUtils.InstanceVoidCutExists(hostElement,cutter);
                    if(!relation)throw new InvalidDataException("Actual native cut/host link missing");
                    var vg=record.GetProperty("voidGeometry");var points=vg.GetProperty("vertices").Read<List<Point>>().Select(NativeGeometry.At).ToList();
                    var c=new XYZ((points.Min(p=>p.X)+points.Max(p=>p.X))/2,(points.Min(p=>p.Y)+points.Max(p=>p.Y))/2,(points.Min(p=>p.Z)+points.Max(p=>p.Z))/2);
                    var voids=NativeGeometry.MeshSolids(vg,Transform.CreateTranslation(c.Negate())).Select(s=>SolidUtils.CreateTransformed(s,Transform.CreateTranslation(c))).ToList();
                    double volume=0;
                    foreach(var solid in NativeGeometry.Solids(hostElement))foreach(var v in voids)
                        volume+=BooleanOperationsUtils.ExecuteBooleanOperation(solid,v,BooleanOperationsType.Intersect).Volume;
                    overlap=UnitUtils.ConvertFromInternalUnits(volume,UnitTypeId.CubicMeters);
                    status=overlap>1e-6?"hostStillFillsOpening":"nativeCutVerified";
                    if(!record.B("emptyOpening"))
                    {
                        if(index.GetValueOrDefault(key) is not Element body)status="missingFilling";
                        else
                        {
                            bodyId=body.Id.Value;var category=record.S("kind")=="Door"?BuiltInCategory.OST_Doors:BuiltInCategory.OST_Windows;
                            if(body.Category?.Id!=new ElementId(category))status="wrongFillingCategory";
                        }
                    }
                }
            }
            catch(Exception ex){status="auditError";error=ex.ToString();}
            outcomes[status]=outcomes.GetValueOrDefault(status)+1;
            checks.Add(new{sourceGuid=key,kind=record.S("kind"),empty=record.B("emptyOpening"),status,cutId,hostId,bodyId,remainingHostIntersectionM3=overlap,error});
            if(++done%25==0)progress?.Invoke("openingReadback",done,records.Count);
        }
        var manifest=Manifest.Load(Resolve(plan.SourceManifest));var config=ImportConfig.Load(Resolve(plan.Mapping));
        var coverage=manifest.Elements.Select(e=>new{sourceGuid=e.SourceGuid,kind=e.Kind,represented=index.ContainsKey(e.SourceGuid),representation=index.GetValueOrDefault(e.SourceGuid)?.GetType().Name??""}).ToArray();
        var coverageCounts=coverage.GroupBy(e=>e.kind).ToDictionary(g=>g.Key,g=>new{source=g.Count(),represented=g.Count(e=>e.represented),missing=g.Count(e=>!e.represented),representations=g.Where(e=>e.represented).GroupBy(e=>e.representation).ToDictionary(r=>r.Key,r=>r.Count())});
        var zeroBoundaries=new List<object>();var zeroOutcomes=new Dictionary<string,int>();
        var geometry=new Geometry(doc,manifest.Coordinates.Transform);
        foreach(var wall in manifest.Elements.Where(SourceZeroThicknessWall.IsCandidate))
        {
            try
            {
            var source=SourceZeroThicknessWall.Read(wall);if(!source.RoomBoundary)continue;
            var axis=geometry.Curve(source.Curve);double bottom=axis.GetEndPoint(0).Z,top=bottom+Geometry.Feet(source.Height);
            foreach(var levelRecord in manifest.Levels)
            {
                if(index.GetValueOrDefault(levelRecord.Key) is not Level level)continue;
                double plane=level.Elevation+(level.get_Parameter(BuiltInParameter.LEVEL_ROOM_COMPUTATION_HEIGHT)?.AsDouble()??0);
                if(plane<bottom-1e-7 || plane>=top-1e-7)continue;
                string key=ZeroThicknessWallStage.BoundaryKey(wall.SourceGuid,levelRecord.Key),status="missing";double? deviation=null;
                if(index.GetValueOrDefault(key) is ModelCurve curve)
                {
                    var a=axis.GetEndPoint(0);var b=axis.GetEndPoint(1);a=new XYZ(a.X,a.Y,level.Elevation);b=new XYZ(b.X,b.Y,level.Elevation);
                    var actual=curve.GeometryCurve;
                    deviation=UnitUtils.ConvertFromInternalUnits(Math.Min(Math.Max(actual.GetEndPoint(0).DistanceTo(a),actual.GetEndPoint(1).DistanceTo(b)),Math.Max(actual.GetEndPoint(0).DistanceTo(b),actual.GetEndPoint(1).DistanceTo(a))),UnitTypeId.Meters);
                    status=deviation<=1e-6?"curveVerified":"curveMismatch";
                }
                zeroOutcomes[status]=zeroOutcomes.GetValueOrDefault(status)+1;
                zeroBoundaries.Add(new{sourceGuid=wall.SourceGuid,levelKey=levelRecord.Key,status,deviationMeters=deviation});
            }
            }
            catch(Exception ex)
            {
                zeroOutcomes["auditError"]=zeroOutcomes.GetValueOrDefault("auditError")+1;
                zeroBoundaries.Add(new{sourceGuid=wall.SourceGuid,status="auditError",error=ex.Message});
            }
        }
        var roomRows=new List<object>();var roomOutcomes=new Dictionary<string,int>();
        foreach(var record in manifest.Elements.Where(e=>e.Kind=="Zone"))
        {
            string status="missing",error="";double? expected=null,area=null,difference=null;
            try
            {
                var loops=record.Payload.GetProperty("loops").Read<List<List<Edge>>>();
                expected=Math.Abs(SourcePlanarGeometry.SignedArea(loops[0]))-loops.Skip(1).Sum(l=>Math.Abs(SourcePlanarGeometry.SignedArea(l)));
                if(index.GetValueOrDefault(record.SourceGuid) is Autodesk.Revit.DB.Architecture.Room room)
                    area=UnitUtils.ConvertFromInternalUnits(room.Area,UnitTypeId.SquareMeters);
                else if(index.GetValueOrDefault(record.SourceGuid) is Autodesk.Revit.DB.Mechanical.Space space)
                    area=UnitUtils.ConvertFromInternalUnits(space.Area,UnitTypeId.SquareMeters);
                if(area.HasValue)
                {
                    difference=Math.Abs(area.Value-expected.Value);
                    status=area<=0?"notEnclosed":difference<=config.RoomAreaToleranceSquareMeters+1e-9?"withinTolerance":"overTolerance";
                }
            }
            catch(Exception ex){status="auditError";error=ex.Message;}
            roomOutcomes[status]=roomOutcomes.GetValueOrDefault(status)+1;
            roomRows.Add(new{sourceGuid=record.SourceGuid,status,sourceAreaSquareMeters=expected,revitAreaSquareMeters=area,
                absoluteDifferenceSquareMeters=difference,toleranceSquareMeters=config.RoomAreaToleranceSquareMeters,error});
        }
        var result=new{document=doc.PathName,gridRecords=axes.Count,verifiedAxes=axisPass,verifiedGridLabels=labelPass,openingRecords=records.Count,outcomes,
            sourceRoomRecords=roomRows.Count,roomOutcomes,coverageCounts,zeroWallBoundaryRecords=zeroBoundaries.Count,zeroWallBoundaryOutcomes=zeroOutcomes,zeroWallBoundaries=zeroBoundaries,coverage,axes,openings=checks,rooms=roomRows};
        File.WriteAllText(output,JsonSerializer.Serialize(result,Json.Options));return new{gridRecords=axes.Count,verifiedAxes=axisPass,verifiedGridLabels=labelPass,openingRecords=records.Count,outcomes,
            sourceRoomRecords=roomRows.Count,roomOutcomes,coverageCounts,zeroWallBoundaryRecords=zeroBoundaries.Count,zeroWallBoundaryOutcomes=zeroOutcomes,output};
    }
}
