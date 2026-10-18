using Autodesk.Revit.DB;
using System.Text.Json;

namespace Ac2Revit;

public static class NativeGeometry
{
    public static XYZ At(Point p)=>new(Geometry.Feet(p.X),Geometry.Feet(p.Y),Geometry.Feet(p.Z));
    public static List<Solid> Solids(Element element)
    {
        var list=new List<Solid>();
        void Read(GeometryElement g)
        {
            foreach(var o in g)
                if(o is GeometryInstance i)Read(i.GetInstanceGeometry());
                else if(o is Solid s && s.Volume>1e-12)list.Add(SolidUtils.CreateTransformed(s,Transform.Identity));
        }
        var geometry=element.get_Geometry(new Options{DetailLevel=ViewDetailLevel.Fine});
        if(geometry!=null)Read(geometry);return list;
    }
    public static List<Solid> MeshSolids(JsonElement record,Transform worldToLocal)
    {
        var points=record.GetProperty("vertices").Read<List<Point>>().Select(p=>worldToLocal.OfPoint(At(p))).ToList();
        var faces=record.GetProperty("faces").Read<List<List<int>>>();
        try {return TessellatedSolids(points,faces);}
        catch(Exception tessellationError)
        {
            // BRepBuilder explicitly permits exact short edges. Do not thicken
            // or simplify millimetre reveals to satisfy sketch tolerances.
            try {return BrepSolids(points,faces);}
            catch(Exception brepError){throw new AggregateException("Neither native solid builder could preserve the reference mesh",tessellationError,brepError);}
        }
    }
    static List<Solid> TessellatedSolids(List<XYZ> points,List<List<int>> faces)
    {
        using var builder=new TessellatedShapeBuilder();builder.OpenConnectedFaceSet(true);
        foreach(var triangle in faces)
        {
            if(triangle.Count!=3 || triangle.Any(i=>i<0 || i>=points.Count))throw new InvalidDataException("Invalid solid triangle");
            using var face=new TessellatedFace(triangle.Select(i=>points[i]).ToList(),ElementId.InvalidElementId);
            if(!builder.DoesFaceHaveEnoughLoopsAndVertices(face))throw new InvalidDataException("Invalid native solid face");
            builder.AddFace(face);
        }
        builder.CloseConnectedFaceSet();builder.Target=TessellatedShapeBuilderTarget.Solid;builder.Fallback=TessellatedShapeBuilderFallback.Abort;builder.Build();
        var result=builder.GetBuildResult();var geometry=result.GetGeometricalObjects();
        if(result.HasInvalidData || geometry.Count==0 || geometry.Any(g=>g is not Solid))throw new InvalidDataException("Reference mesh cannot form native solids without salvage");
        return geometry.Cast<Solid>().ToList();
    }
    static List<Solid> BrepSolids(List<XYZ> points,List<List<int>> faces)
    {
        using var builder=new BRepBuilder(BRepType.Solid);builder.SetAllowShortEdges();
        var edges=new Dictionary<(int,int),BRepBuilderGeometryId>();
        double signed=faces.Sum(f=>points[f[0]].DotProduct(points[f[1]].CrossProduct(points[f[2]])))/6;
        foreach(var original in faces)
        {
            var f=signed<0?original.AsEnumerable().Reverse().ToList():original;
            var p=points[f[0]];var normal=(points[f[1]]-p).CrossProduct(points[f[2]]-p);
            if(normal.GetLength()<1e-16)throw new InvalidDataException("Degenerate exact BRep triangle");
            using var surface=BRepBuilderSurfaceGeometry.Create(Plane.CreateByNormalAndOrigin(normal.Normalize(),p),null);
            var face=builder.AddFace(surface,false);var loop=builder.AddLoop(face);
            for(int i=0;i<3;i++)
            {
                int a=f[i],b=f[(i+1)%3];var key=(Math.Min(a,b),Math.Max(a,b));
                if(!edges.TryGetValue(key,out var edge))
                {
                    using var geometry=BRepBuilderEdgeGeometry.Create(points[key.Item1],points[key.Item2]);edge=builder.AddEdge(geometry);edges[key]=edge;
                }
                builder.AddCoEdge(loop,edge,a>b);
            }
            builder.FinishLoop(loop);builder.FinishFace(face);
        }
        if(builder.Finish()!=BRepBuilderOutcome.Success || builder.RemovedSomeFaces() || !builder.IsResultAvailable())throw new InvalidDataException("Exact short-edge BRep could not be built without removing faces");
        var result=builder.GetResult();
        if(Math.Abs(result.Volume-Math.Abs(signed))>Math.Max(Geometry.Feet(1e-6)*Geometry.Feet(1)*Geometry.Feet(1),Math.Abs(signed)*1e-5))throw new InvalidDataException("Exact BRep volume differs from source triangles");
        return SolidUtils.SplitVolumes(result).ToList();
    }
    public static double DifferenceVolume(IEnumerable<Solid> from,IEnumerable<Solid> subtract)
    {
        double total=0;
        foreach(var input in from)
        {
            var remainder=SolidUtils.CreateTransformed(input,Transform.Identity);
            foreach(var cut in subtract)
            {
                if(remainder.Volume<1e-12)break;
                remainder=BooleanOperationsUtils.ExecuteBooleanOperation(remainder,cut,BooleanOperationsType.Difference);
            }
            total+=remainder.Volume;
        }
        return UnitUtils.ConvertFromInternalUnits(total,UnitTypeId.CubicMeters);
    }
    public static void VerifySolids(IEnumerable<Solid> actual,IEnumerable<Solid> expected,double toleranceM3=1e-6)
    {
        var a=actual.ToList();var b=expected.ToList();
        if(a.Count==0 || b.Count==0 || DifferenceVolume(a,b)>toleranceM3 || DifferenceVolume(b,a)>toleranceM3)
            throw new InvalidDataException("Native solids do not match reference in both Boolean directions");
    }
    public static void DeleteMeshOnly(Document doc,DirectShape source)
    {
        var id=source.Id;
        var allowed=source.GetDependentElements(null).Select(id=>doc.GetElement(id)).Where(e=>e!=null && e.GetType()==typeof(Element)
            && e.Category==null && string.IsNullOrEmpty(e.Name) && !e.ViewSpecific && Storage.Read(e)==null
            && e.Parameters.Cast<Parameter>().All(p=>p.IsReadOnly) && e.get_BoundingBox(null)==null).Select(e=>e.Id).ToHashSet();
        var removed=doc.Delete(id);
        if(!removed.Contains(id) || removed.Any(other=>other!=id && !allowed.Contains(other)))throw new InvalidDataException("Replacement would delete user dependents");
    }
    public static void Configure(Transaction transaction,Report report,string key,string kind)
    {
        var options=transaction.GetFailureHandlingOptions();options.SetFailuresPreprocessor(new Failures(report,key,kind));options.SetClearAfterRollback(true);transaction.SetFailureHandlingOptions(options);
    }
    sealed class Failures(Report report,string key,string kind):IFailuresPreprocessor
    {
        public FailureProcessingResult PreprocessFailures(FailuresAccessor accessor)
        {
            bool error=false;
            foreach(var f in accessor.GetFailureMessages())
            {
                report.Add(key,kind,f.GetSeverity()==FailureSeverity.Warning?"warning":"revitError",f.GetDescriptionText());
                if(f.GetSeverity()==FailureSeverity.Warning)accessor.DeleteWarning(f);else error=true;
            }
            return error?FailureProcessingResult.ProceedWithRollBack:FailureProcessingResult.Continue;
        }
    }
}
