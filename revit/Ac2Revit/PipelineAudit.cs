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
        var axes=new List<object>();int axisPass=0;
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
            axes.Add(new{sourceGuid=record.SourceGuid,status,deviationMeters=deviation,revitName=name,sourceLabel=record.Mark});
        }
        using var openings=JsonDocument.Parse(File.ReadAllText(Resolve(plan.OpeningsPlan)));var records=openings.RootElement.GetProperty("elements").EnumerateArray().ToList();
        var checks=new List<object>();var outcomes=new Dictionary<string,int>();int done=0;
        foreach(var record in records)
        {
            string key=record.S("sourceGuid"),status="missingCut",error="";double? overlap=null;long? cutId=null,hostId=null,bodyId=null;
            try
            {
                string cutKey=record.B("emptyOpening")?key:"opening:"+key;
                if(index.GetValueOrDefault(cutKey) is Element cutter)
                {
                    cutId=cutter.Id.Value;
                    if(index.GetValueOrDefault(record.S("hostGuid")) is not Wall wall)throw new InvalidDataException("Source wall host missing");
                    hostId=wall.Id.Value;
                    bool relation=cutter is Opening native?native.Host?.Id==wall.Id:InstanceVoidCutUtils.InstanceVoidCutExists(wall,cutter);
                    if(!relation)throw new InvalidDataException("Actual native cut/host link missing");
                    var vg=record.GetProperty("voidGeometry");var points=vg.GetProperty("vertices").Read<List<Point>>().Select(NativeGeometry.At).ToList();
                    var c=new XYZ((points.Min(p=>p.X)+points.Max(p=>p.X))/2,(points.Min(p=>p.Y)+points.Max(p=>p.Y))/2,(points.Min(p=>p.Z)+points.Max(p=>p.Z))/2);
                    var voids=NativeGeometry.MeshSolids(vg,Transform.CreateTranslation(c.Negate())).Select(s=>SolidUtils.CreateTransformed(s,Transform.CreateTranslation(c))).ToList();
                    double volume=0;
                    foreach(var solid in NativeGeometry.Solids(wall))foreach(var v in voids)
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
        var result=new{document=doc.PathName,gridRecords=axes.Count,verifiedAxes=axisPass,openingRecords=records.Count,outcomes,axes,openings=checks};
        File.WriteAllText(output,JsonSerializer.Serialize(result,Json.Options));return new{gridRecords=axes.Count,verifiedAxes=axisPass,openingRecords=records.Count,outcomes,output};
    }
}
