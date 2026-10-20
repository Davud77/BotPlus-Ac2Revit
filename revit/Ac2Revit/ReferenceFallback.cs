using Autodesk.Revit.DB;
using System.Text.Json;

namespace Ac2Revit;

public static class ReferenceFallback
{
    public static Report Run(Document doc,string meshManifest,Func<string,string> workspacePath,Action<int,int>? progress=null,
        BuiltInCategory category=BuiltInCategory.OST_Walls,string recordKind="WallGeometry",bool uniqueMarks=false)
    {
        using var json=JsonDocument.Parse(File.ReadAllText(meshManifest));var root=json.RootElement;
        string project=root.GetProperty("projectId").GetString()!,ifcHash=root.GetProperty("ifcSha256").GetString()!;
        VerifiedReferenceScope.Verify(workspacePath(root.GetProperty("ifcPath").GetString()!),ifcHash);
        var records=(root.TryGetProperty("elements",out var recordsProperty)?recordsProperty:root.GetProperty("walls")).EnumerateArray().ToList();
        var keys=records.Select(r=>r.GetProperty("sourceGuid").GetString()!).ToList();
        if(keys.Count!=keys.Distinct(StringComparer.OrdinalIgnoreCase).Count())throw new InvalidDataException("Duplicate source GUIDs");
        var existing=Storage.Index(doc,project);var geometry=new Geometry(doc,root.GetProperty("coordinates").Read<CoordinateInfo>().Transform);
        var report=new Report{ProjectId=project,Mode="referenceFallbackCopy"};int done=0;
        if(!DirectShape.IsValidCategoryId(new ElementId(category),doc))throw new InvalidDataException("Category does not support DirectShape: "+category);
        foreach(var record in records)
        {
            string key=record.GetProperty("sourceGuid").GetString()!;
            if(existing.ContainsKey(key))throw new InvalidDataException("Fallback would duplicate source element "+key);
        }
        foreach(var record in records)
        {
            string key=record.GetProperty("sourceGuid").GetString()!;
            using var transaction=new Transaction(doc,"IFC "+recordKind+" geometry fallback");
            string stage="readMesh";
            try
            {
                var points=record.GetProperty("vertices").Read<List<Point>>().Select(geometry.Xyz).ToList();
                var faces=record.GetProperty("faces").Read<List<List<int>>>();
                if(points.Count<3 || faces.Count==0)throw new InvalidDataException("Empty reference mesh");
                stage="buildFaces";using var builder=new TessellatedShapeBuilder();builder.OpenConnectedFaceSet(false);
                foreach(var indices in faces)
                {
                    if(indices.Count!=3 || indices.Any(i=>i<0||i>=points.Count))throw new InvalidDataException("Invalid triangle indices");
                    using var face=new TessellatedFace(indices.Select(i=>points[i]).ToList(),ElementId.InvalidElementId);
                    if(!builder.DoesFaceHaveEnoughLoopsAndVertices(face))throw new InvalidDataException("Revit rejected reference triangle");
                    builder.AddFace(face);
                }
                stage="buildMesh";builder.CloseConnectedFaceSet();builder.Target=TessellatedShapeBuilderTarget.Mesh;builder.Fallback=TessellatedShapeBuilderFallback.Salvage;builder.Build();
                var built=builder.GetBuildResult();
                // This method transfers ownership and clears the result. It must
                // be called once; repeating it throws an exception without text.
                var objects=built.GetGeometricalObjects();
                if(objects.Count==0 || built.HasInvalidData)throw new InvalidDataException("Invalid or empty tessellated result");
                if(objects.Any(o=>o is not Mesh) || objects.OfType<Mesh>().Sum(m=>m.NumTriangles)!=faces.Count)
                    throw new InvalidDataException("Revit changed the source triangle count");
                // Bounds/count alone can miss moved or replaced faces. Compare
                // all unoriented triangles as a multiset, at one micrometre.
                double quantum=Geometry.Feet(0.000001);
                string VertexKey(XYZ p)=>$"{Math.Round(p.X/quantum)},{Math.Round(p.Y/quantum)},{Math.Round(p.Z/quantum)}";
                string TriangleKey(IEnumerable<XYZ> triangle)=>string.Join("|",triangle.Select(VertexKey).OrderBy(v=>v,StringComparer.Ordinal));
                double vertexDeviation=0;
                double searchCell=Geometry.Feet(0.001);
                (long,long,long) Cell(XYZ p)=>((long)Math.Floor(p.X/searchCell),(long)Math.Floor(p.Y/searchCell),(long)Math.Floor(p.Z/searchCell));
                var cells=points.GroupBy(Cell).ToDictionary(g=>g.Key,g=>g.ToList());
                XYZ MatchVertex(XYZ value)
                {
                    // Revit Mesh stores coordinates less precisely than XYZ.
                    // Match the returned vertex to an actual source vertex, then
                    // compare triangle topology. Never round or move the input.
                    XYZ? nearest=null;double distance=double.PositiveInfinity;
                    var cell=Cell(value);
                    for(int x=-1;x<=1;x++)for(int y=-1;y<=1;y++)for(int z=-1;z<=1;z++)
                        if(cells.TryGetValue((cell.Item1+x,cell.Item2+y,cell.Item3+z),out var neighbours))
                            foreach(var point in neighbours){double delta=point.DistanceTo(value);if(delta<distance){distance=delta;nearest=point;}}
                    if(nearest==null || distance>Geometry.Feet(0.001))throw new InvalidDataException("Revit mesh vertex moved by more than 1 mm");
                    vertexDeviation=Math.Max(vertexDeviation,distance);return nearest;
                }
                var expectedTriangles=faces.GroupBy(f=>TriangleKey(f.Select(i=>points[i]))).ToDictionary(g=>g.Key,g=>g.Count(),StringComparer.Ordinal);
                var actualTriangles=new Dictionary<string,int>(StringComparer.Ordinal);
                foreach(var mesh in objects.OfType<Mesh>())for(int i=0;i<mesh.NumTriangles;i++)
                {var tri=mesh.get_Triangle(i);string tk=TriangleKey(Enumerable.Range(0,3).Select(j=>MatchVertex(tri.get_Vertex(j))));actualTriangles[tk]=actualTriangles.GetValueOrDefault(tk)+1;}
                if(expectedTriangles.Count!=actualTriangles.Count || expectedTriangles.Any(k=>actualTriangles.GetValueOrDefault(k.Key)!=k.Value))
                    throw new InvalidDataException("Revit changed the reference triangles beyond the 1 micrometre quantisation");
                stage="createElement";transaction.Start();var options=transaction.GetFailureHandlingOptions();options.SetFailuresPreprocessor(new Failures(report,key,recordKind));options.SetClearAfterRollback(true);transaction.SetFailureHandlingOptions(options);
                var shape=DirectShape.CreateElement(doc,new ElementId(category));
                shape.ApplicationId="Ac2Revit.IFC.ReferenceFallback";shape.ApplicationDataId=key;shape.SetShape(objects);
                var comments=shape.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS);
                comments?.Set("IFC geometry fallback; source "+key+"; "+record.GetProperty("reason").GetString()+(uniqueMarks?"; Исходная марка: "+record.S("mark"):""));
                if(record.TryGetProperty("mark",out var mark))shape.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.Set(uniqueMarks?$"{mark.GetString()} [AC {key}]":mark.GetString());
                stage="tagAndRegenerate";Storage.Tag(shape,project,key,"ifc-mesh:"+ifcHash,record.GetRawText());doc.Regenerate();
                var box=shape.get_BoundingBox(null);if(box==null)throw new InvalidDataException("Fallback has no bounds");
                var expectedLo=new XYZ(points.Min(p=>p.X),points.Min(p=>p.Y),points.Min(p=>p.Z));var expectedHi=new XYZ(points.Max(p=>p.X),points.Max(p=>p.Y),points.Max(p=>p.Z));
                double error=Math.Max(box.Min.DistanceTo(expectedLo),box.Max.DistanceTo(expectedHi));
                if(error>Geometry.Feet(0.001))throw new InvalidDataException("Fallback bounds differ by more than 1 mm");
                stage="commit";if(transaction.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Fallback transaction rolled back");
                double deviationMeters=UnitUtils.ConvertFromInternalUnits(vertexDeviation,UnitTypeId.Meters);
                report.Add(key,recordKind,"created",$"DirectShape: source triangle multiset verified after vertex correspondence; maximum vertex deviation {deviationMeters:G9} m, bounds within 1 mm. IFC tessellation is used; native parametric functionality/material layers are not assigned.",shape.UniqueId);
            }
            catch(Exception ex){if(transaction.GetStatus()==TransactionStatus.Started)transaction.RollBack();report.Add(key,recordKind,"blocked",stage+": "+ex);}
            progress?.Invoke(++done,records.Count);
        }
        return report;
    }
    sealed class Failures(Report report,string key,string kind):IFailuresPreprocessor
    {
        public FailureProcessingResult PreprocessFailures(FailuresAccessor accessor)
        {
            bool error=false;foreach(var failure in accessor.GetFailureMessages())
            {report.Add(key,kind,failure.GetSeverity()==FailureSeverity.Warning?"warning":"revitError",failure.GetDescriptionText());if(failure.GetSeverity()==FailureSeverity.Warning)accessor.DeleteWarning(failure);else error=true;}
            return error?FailureProcessingResult.ProceedWithRollBack:FailureProcessingResult.Continue;
        }
    }
}
