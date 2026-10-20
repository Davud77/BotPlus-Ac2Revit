using Autodesk.Revit.DB;
using Autodesk.Revit.DB.ExtensibleStorage;
using System.Security.Cryptography;
using System.Text.Json;

namespace Ac2Revit;

/// <summary>Exact source drawing geometry plus explicitly classified native room-boundary adaptation.</summary>
public static class RoomSeparatorStage
{
    public sealed class Plan
    {
        public string Format {get;set;}="";
        public string ProjectId {get;set;}="";
        public string SourceManifestSha256 {get;set;}="";
        public string NativeCleanupPolicy {get;set;}="strict";
        public double RevitToleranceMeters {get;set;}=1e-6;
        public List<JsonElement> SourceElements {get;set;}=[];
        public List<Segment> Segments {get;set;}=[];
        public List<JsonElement> Unsupported {get;set;}=[];
    }
    public sealed class Segment
    {
        public string Key {get;set;}="";
        public string LevelKey {get;set;}="";
        public Edge Curve {get;set;}=new();
        public List<SourceRef> SourceRefs {get;set;}=[];
    }
    public sealed class SourceRef
    {
        public string SourceGuid {get;set;}="";
        public int EdgeIndex {get;set;}
    }
    static readonly double[] Samples=[0,0.25,0.5,0.75,1];
    static string ReferenceKey(string key)=>"separator-source-curve:"+key["room-separator:".Length..];
    static string StateKey(string key)=>"separator-native-state:"+key["room-separator:".Length..];
    static double[][] Points(Curve curve)=>Samples.Select(u=>{
        var p=curve.Evaluate(u,true);return new[]{UnitUtils.ConvertFromInternalUnits(p.X,UnitTypeId.Meters),UnitUtils.ConvertFromInternalUnits(p.Y,UnitTypeId.Meters),UnitUtils.ConvertFromInternalUnits(p.Z,UnitTypeId.Meters)};
    }).ToArray();
    static double Deviation(double[][] a,double[][] b)
    {
        if(a.Length!=5 || b.Length!=5)return double.PositiveInfinity;
        double forward=0,reverse=0;
        for(int i=0;i<5;i++)
        {
            double Distance(double[] p,double[] q)=>Math.Sqrt(Enumerable.Range(0,3).Sum(k=>Math.Pow(p[k]-q[k],2)));
            forward=Math.Max(forward,Distance(a[i],b[i]));reverse=Math.Max(reverse,Distance(a[i],b[4-i]));
        }
        return Math.Min(forward,reverse);
    }
    static JsonElement Metadata(Element element)
    {
        var schema=Storage.Schema();var entity=element.GetEntity(schema);
        return JsonSerializer.Deserialize<JsonElement>(entity.Get<string>(schema.GetField("RawJson")),Json.Options);
    }
    static bool SnapshotMatches(Element element,Curve curve,double tolerance,string nativeUniqueId)
    {
        try
        {
            var metadata=Metadata(element);
            return metadata.S("nativeElementUniqueId")==nativeUniqueId && Deviation(Points(curve),metadata.GetProperty("nativePointsMeters").Read<double[][]>())<=tolerance;
        }
        catch{return false;}
    }
    static Plan Load(Manifest manifest,string input)
    {
        var plan=JsonSerializer.Deserialize<Plan>(File.ReadAllText(input),Json.Options)??throw new InvalidDataException("Empty separator plan");
        if(plan.Format!="ac2r.room-separators/1" || plan.ProjectId!=manifest.Source.ProjectId || plan.NativeCleanupPolicy is not ("strict" or "markAdjusted"))
            throw new InvalidDataException("Invalid separator project/cleanup policy");
        if(!double.IsFinite(plan.RevitToleranceMeters) || plan.RevitToleranceMeters<1e-8 || plan.RevitToleranceMeters>0.001)
            throw new InvalidDataException("Geometry tolerance must not exceed 1 mm");
        return plan;
    }
    public static object Audit(Document doc,Manifest manifest,string input,string output)
    {
        var plan=Load(manifest,input);var index=Storage.Index(doc,plan.ProjectId);var geometry=new Geometry(doc,manifest.Coordinates.Transform);
        var rows=new List<object>();var represented=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        int verified=0,adjusted=0,changed=0,missing=0,referencesVerified=0,snapshotsVerified=0;double maximum=0,referenceMaximum=0;
        foreach(var segment in plan.Segments)
        {
            if(index.GetValueOrDefault(segment.Key) is not ModelCurve actual || index.GetValueOrDefault(segment.LevelKey) is not Level level)
            {missing++;rows.Add(new{key=segment.Key,status="missing"});continue;}
            var expected=Points(geometry.Curve(segment.Curve,level.Elevation));var points=Points(actual.GeometryCurve);
            double deviation=Deviation(expected,points);maximum=Math.Max(maximum,deviation);
            var state=index.GetValueOrDefault(StateKey(segment.Key));
            string sourceHash=Json.Hash(segment);
            bool snapshot=state!=null && Storage.Read(actual)?.Hash==sourceHash && Storage.Read(state)?.Hash==sourceHash
                && SnapshotMatches(state,actual.GeometryCurve,plan.RevitToleranceMeters,actual.UniqueId);if(snapshot)snapshotsVerified++;
            string status;
            if(deviation<=plan.RevitToleranceMeters){status="verified";verified++;}
            else if(plan.NativeCleanupPolicy=="markAdjusted" && snapshot && Metadata(state!).S("transferStatus")=="nativeAdjusted"){status="nativeAdjusted";adjusted++;}
            else{status="changed";changed++;}
            bool referenceVerified=false;double? referenceDeviation=null;
            if(index.GetValueOrDefault(ReferenceKey(segment.Key)) is DetailCurve reference)
            {
                referenceDeviation=Deviation(expected,Points(reference.GeometryCurve));referenceMaximum=Math.Max(referenceMaximum,referenceDeviation.Value);
                referenceVerified=referenceDeviation<=plan.RevitToleranceMeters && Storage.Read(reference)?.Hash==sourceHash;if(referenceVerified)referencesVerified++;
            }
            if(status is "verified" or "nativeAdjusted")foreach(var r in segment.SourceRefs)represented.Add(r.SourceGuid);
            rows.Add(new{key=segment.Key,status,elementId=actual.Id.Value,sourceRefs=segment.SourceRefs,levelKey=segment.LevelKey,
                curveClass=actual.GeometryCurve.GetType().Name,sourceDeviationMeters=deviation,nativeSnapshotVerified=snapshot,
                exactSourceReferenceVerified=referenceVerified,referenceDeviationMeters=referenceDeviation,actualPointsMeters=points});
        }
        var expectedKeys=plan.Segments.Select(s=>s.Key).ToHashSet(StringComparer.OrdinalIgnoreCase);
        var extra=index.Keys.Where(k=>k.StartsWith("room-separator:",StringComparison.Ordinal) && !expectedKeys.Contains(k)).ToArray();
        var result=new{projectId=plan.ProjectId,document=doc.PathName,expectedNativeCurves=plan.Segments.Count,verified,nativeAdjusted=adjusted,missing,changed,
            nativeSnapshotsVerified=snapshotsVerified,exactSourceReferencesVerified=referencesVerified,sourceElements=plan.SourceElements.Count,
            representedSourceGuids=represented.Count,extraTaggedKeys=extra,maxNativeSourceDeviationMeters=maximum,maxReferenceDeviationMeters=referenceMaximum,curves=rows};
        File.WriteAllText(output,JsonSerializer.Serialize(result,Json.Options));return result;
    }
    public static Report Run(Document doc,Manifest manifest,string manifestPath,string input,string output,Action<string,int,int>? progress=null)
    {
        var plan=Load(manifest,input);
        using(var stream=File.OpenRead(manifestPath))
            if(!Convert.ToHexString(SHA256.HashData(stream)).Equals(plan.SourceManifestSha256,StringComparison.OrdinalIgnoreCase))throw new InvalidDataException("Different source snapshot");
        string project=manifest.Source.ProjectId;var index=Storage.Index(doc,project);var geometry=new Geometry(doc,manifest.Coordinates.Transform);
        var source=new Dictionary<string,JsonElement>(StringComparer.OrdinalIgnoreCase);
        foreach(var raw in plan.SourceElements)
            if(!Guid.TryParse(raw.S("sourceGuid"),out _) || !raw.GetProperty("details").B("roomSeparator") || !source.TryAdd(raw.S("sourceGuid"),raw))
                throw new InvalidDataException("Unconfirmed or duplicate source separator");
        var prepared=new List<(Segment Segment,Level Level,Curve Curve)>();var keys=new HashSet<string>();var coverage=new HashSet<(string,int)>();
        foreach(var segment in plan.Segments)
        {
            if(!segment.Key.StartsWith("room-separator:",StringComparison.Ordinal) || segment.Key.Length!=79 || !keys.Add(segment.Key) || segment.SourceRefs.Count==0)
                throw new InvalidDataException("Invalid/duplicate separator key");
            if(index.GetValueOrDefault(segment.LevelKey) is not Level level)throw new InvalidDataException("Imported source level missing");
            foreach(var r in segment.SourceRefs)
            {
                if(!source.TryGetValue(r.SourceGuid,out var raw) || "story:"+raw.GetProperty("floorIndex").GetInt32()!=segment.LevelKey || r.EdgeIndex<0 || !coverage.Add((r.SourceGuid.ToUpperInvariant(),r.EdgeIndex)))
                    throw new InvalidDataException("Invalid/duplicate source edge");
                double z=raw.GetProperty("details").D("zCoordinate");
                if(Math.Abs(segment.Curve.Start.Z-z)>1e-7 || Math.Abs(segment.Curve.End.Z-z)>1e-7)throw new InvalidDataException("Source elevation mismatch");
            }
            prepared.Add((segment,level,geometry.Curve(segment.Curve,level.Elevation)));
        }
        var report=new Report{ProjectId=project,Mode="roomSeparators"};var finalChecks=new List<object>();
        var partialSources=new HashSet<string>(StringComparer.OrdinalIgnoreCase);int done=0,differences=0;double maximum=0;
        Directory.CreateDirectory(output);
        using var group=new TransactionGroup(doc,"Source room separators and exact drawing references");group.Start();
        try
        {
            var viewType=new FilteredElementCollector(doc).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>().First(v=>v.ViewFamily==ViewFamily.FloorPlan);
            foreach(var rows in prepared.GroupBy(p=>p.Level.Id))
            {
                var level=rows.First().Level;string levelKey=rows.First().Segment.LevelKey;
                using var t=new Transaction(doc,"Source separators "+level.Name);t.Start();NativeGeometry.Configure(t,report,levelKey,"RoomSeparator");
                var view=new FilteredElementCollector(doc).OfClass(typeof(ViewPlan)).Cast<ViewPlan>().Where(v=>!v.IsTemplate && v.ViewType==ViewType.FloorPlan && v.GenLevel?.Id==level.Id)
                    .OrderByDescending(v=>v.Name.StartsWith("AC Plan ",StringComparison.Ordinal)).FirstOrDefault();
                if(view==null){view=ViewPlan.Create(doc,viewType.Id,level.Id);view.Name="AC Separators "+level.Id.Value;}
                string viewKey="separator-source-view:"+levelKey;
                ViewPlan sourceView;
                if(index.GetValueOrDefault(viewKey) is ViewPlan storedView)sourceView=storedView;
                else
                {
                    sourceView=ViewPlan.Create(doc,viewType.Id,level.Id);sourceView.Name="AC Source separators "+level.Name;
                    sourceView.SetCategoryHidden(new ElementId(BuiltInCategory.OST_RoomSeparationLines),true);
                    Storage.Tag(sourceView,project,viewKey,Json.Hash(levelKey));index.Add(viewKey,sourceView);
                }
                SketchPlane? plane=null;
                foreach(var row in rows)
                {
                    string hash=Json.Hash(row.Segment);ModelCurve line;
                    if(index.TryGetValue(row.Segment.Key,out var existing))
                    {
                        if(existing is not ModelCurve curve || curve.Category.Id!=new ElementId(BuiltInCategory.OST_RoomSeparationLines)
                            || Storage.Read(curve)?.Hash!=hash || index.GetValueOrDefault(StateKey(row.Segment.Key)) is not DataStorage savedState
                            || Storage.Read(savedState)?.Hash!=hash || Metadata(savedState).S("nativeElementUniqueId")!=curve.UniqueId)
                            throw new InvalidDataException("Existing native separator changed; no overwrite: "+row.Segment.Key);
                        if(!SnapshotMatches(savedState,curve.GeometryCurve,plan.RevitToleranceMeters,curve.UniqueId))
                        {
                            if(plan.NativeCleanupPolicy=="strict")throw new InvalidDataException("Existing native separator changed; no overwrite: "+row.Segment.Key);
                            report.Add(row.Segment.Key,"RoomSeparator","incomplete","Фактическая нативная кривая изменилась после предыдущего измерения. Геометрия сохранена без перезаписи; причина изменения не устанавливается. Новое измерение принимается отдельно от точной исходной 2D-ссылки.",curve.UniqueId);
                        }
                        line=curve;report.Add(row.Segment.Key,"RoomSeparator","unchanged","Native snapshot and source hash verified",line.UniqueId);
                    }
                    else
                    {
                        plane??=SketchPlane.Create(doc,Plane.CreateByNormalAndOrigin(XYZ.BasisZ,new XYZ(0,0,level.Elevation)));
                        using var curves=new CurveArray();curves.Append(row.Curve);var created=doc.Create.NewRoomBoundaryLines(plane,curves,view);
                        if(created.Size!=1)throw new InvalidOperationException("Unexpected native curve count");
                        line=created.get_Item(0);
                        Storage.Tag(line,project,row.Segment.Key,hash,JsonSerializer.Serialize(row.Segment,Json.Options));
                        index.Add(row.Segment.Key,line);
                        report.Add(row.Segment.Key,"RoomSeparator","created","Explicit roomSeparator=true; final cleanup classification follows",line.UniqueId);
                    }
                    string referenceKey=ReferenceKey(row.Segment.Key);
                    if(!index.TryGetValue(referenceKey,out var reference))
                    {
                        reference=doc.Create.NewDetailCurve(sourceView,row.Curve);Storage.Tag(reference,project,referenceKey,hash,JsonSerializer.Serialize(row.Segment,Json.Options));
                        index.Add(referenceKey,reference);report.Add(referenceKey,"RoomSeparatorReference","created","Exact source 2D curve in diagnostic view",reference.UniqueId);
                    }
                    if(reference is not DetailCurve detail || Deviation(Points(row.Curve),Points(detail.GeometryCurve))>plan.RevitToleranceMeters)
                        throw new InvalidOperationException("Source drawing reference geometry differs");
                    if(++done%50==0)progress?.Invoke("roomSeparators",done,prepared.Count);
                }
                if(t.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Separator transaction rolled back");
            }
            using(var annotate=new Transaction(doc,"Classify native separator cleanup"))
            {
                annotate.Start();NativeGeometry.Configure(annotate,report,"separator-cleanup","RoomSeparator");
                foreach(var row in prepared)
                {
                    var line=(ModelCurve)index[row.Segment.Key];var actual=Points(line.GeometryCurve);double deviation=Deviation(Points(row.Curve),actual);
                    var reference=(DetailCurve)index[ReferenceKey(row.Segment.Key)];
                    double refDeviation=Deviation(Points(row.Curve),Points(reference.GeometryCurve));
                    if(refDeviation>plan.RevitToleranceMeters)throw new InvalidOperationException("Final exact source reference changed");
                    bool matches=deviation<=plan.RevitToleranceMeters;maximum=Math.Max(maximum,deviation);
                    if(!matches)
                    {
                        differences++;foreach(var r in row.Segment.SourceRefs)partialSources.Add(r.SourceGuid);
                        if(plan.NativeCleanupPolicy=="strict")throw new InvalidOperationException("Native separator differs: "+row.Segment.Key);
                        string note=$"НЕПОЛНОЦЕННЫЙ РАЗДЕЛИТЕЛЬ — нативная кривая отличается от исходной до {deviation*1000:G8} мм. Точная 2D ссылка — AC Source separators. Геометрия сохранена; проверка площади помещения отдельная.";
                        report.Add(row.Segment.Key,"RoomSeparator","incomplete",note,line.UniqueId);
                    }
                    // Changing an existing separator's parameters retriggers wall cleanup in Revit.
                    // Keep the immutable source tag on the curve; store measured acceptance separately.
                    string stateKey=StateKey(row.Segment.Key);
                    var state=index.GetValueOrDefault(stateKey) as DataStorage;
                    if(state==null){state=DataStorage.Create(doc);index.Add(stateKey,state);}
                    var previous=Storage.Read(state)==null?(JsonElement?)null:Metadata(state);
                    Storage.Tag(state,project,stateKey,Json.Hash(row.Segment),JsonSerializer.Serialize(new{segment=row.Segment,sources=row.Segment.SourceRefs.Select(r=>source[r.SourceGuid]),
                        previousNativePointsMeters=previous?.GetProperty("nativePointsMeters"),
                        nativeElementUniqueId=line.UniqueId,transferStatus=matches?"verified":"nativeAdjusted",nativePointsMeters=actual,sourceDeviationMeters=deviation,sourceReferenceKey=ReferenceKey(row.Segment.Key)},Json.Options));
                    finalChecks.Add(new{key=row.Segment.Key,sourceRefs=row.Segment.SourceRefs,status=matches?"verified":"nativeAdjusted",sourceDeviationMeters=deviation,
                        actualPointsMeters=actual,referenceElementId=reference.Id.Value,referenceDeviationMeters=refDeviation});
                }
                if(annotate.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Separator annotation rolled back");
            }
            group.Assimilate();
        }
        catch(Exception error)
        {
            group.RollBack();
            report.Add("room-separator-stage","RoomSeparator","failed",error.ToString());
            report.Save(Path.Combine(output,"failed-report.json"));
            throw;
        }
        foreach(var row in source)report.Add(row.Key,"RoomSeparatorSource",partialSources.Contains(row.Key)?"partial":coverage.Any(c=>c.Item1==row.Key.ToUpperInvariant())?"represented":"blocked",
            "Exact drawing reference and native boundary adaptation have separate acceptance");
        foreach(var row in plan.Unsupported)report.Add(row.S("sourceGuid"),"RoomSeparatorSource","blocked",row.S("reason"));
        File.WriteAllText(Path.Combine(output,"final-geometry.json"),JsonSerializer.Serialize(new{sourceElements=source.Count,nativeCurves=prepared.Count,nativeAdjusted=differences,
            maxNativeSourceDeviationMeters=maximum,exactSourceReferences=prepared.Count,toleranceMeters=plan.RevitToleranceMeters,curves=finalChecks},Json.Options));
        return report;
    }
}
