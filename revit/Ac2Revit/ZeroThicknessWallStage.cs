using Autodesk.Revit.DB;
using System.Text.Json;

namespace Ac2Revit;

/// <summary>Keep an explicit zero-width source wall as a nominal sheet and native zone boundary.</summary>
public static class ZeroThicknessWallStage
{
    public const string ApplicationId="Ac2Revit.ZeroThicknessWall";
    public static string BoundaryKey(string wall,string level)=>"zero-wall-separator:"+wall+":"+level;
    public static Report Run(Document doc,Manifest manifest,string output,Action<string,int,int>? progress=null)
    {
        var report=new Report{ProjectId=manifest.Source.ProjectId,Mode="explicitZeroThicknessWalls"};
        var records=manifest.Elements.Where(SourceZeroThicknessWall.IsCandidate).ToArray();
        var index=Storage.Index(doc,manifest.Source.ProjectId);var geometry=new Geometry(doc,manifest.Coordinates.Transform);
        var views=new Dictionary<ElementId,ViewPlan>();int done=0;
        foreach(var record in records)
        {
            try
            {
                var source=SourceZeroThicknessWall.Read(record);var axis=geometry.Curve(source.Curve);
                var a=axis.GetEndPoint(0);var b=axis.GetEndPoint(1);var height=Geometry.Feet(source.Height);
                XYZ[] points=[a,b,b+XYZ.BasisZ*height,a+XYZ.BasisZ*height];
                if(points.Any(p=>!XYZ.IsWithinLengthLimits(p)))throw new InvalidDataException("Zero-thickness wall exceeds Revit design limits");
                string hash=Json.Hash(new{record.Raw,manifest.Coordinates.Transform,representation="zeroThicknessNominalSheet-v1"});
                if(index.TryGetValue(record.SourceGuid,out var existing))
                {
                    if(existing is DirectShape reference && reference.ApplicationId=="Ac2Revit.IFC.ReferenceFallback")
                    {
                        if(!Coplanar(reference,axis,points))
                        {report.Add(record.SourceGuid,"ZeroThicknessWall","retained","IFC body retained: it is not a zero-width sheet on the API axis. No nominal replacement or guessed room boundary.",reference.UniqueId);continue;}
                        report.Add(record.SourceGuid,"ZeroThicknessWall","retained","Verified IFC sheet retained; source zero-width boundary is handled separately.",reference.UniqueId);
                    }
                    else
                    {
                    if(existing is not DirectShape owned || owned.ApplicationId!=ApplicationId || Storage.Read(owned)?.Hash!=hash)
                        throw new InvalidDataException("Existing zero-thickness representation differs; no overwrite");
                    Verify(owned,points);report.Add(record.SourceGuid,"ZeroThicknessWall","unchanged","Nominal source sheet verified",owned.UniqueId);
                    }
                }
                else
                {
                    var objects=BuildSheet(points);
                    using var transaction=new Transaction(doc,"Source zero-thickness wall sheet");transaction.Start();NativeGeometry.Configure(transaction,report,record.SourceGuid,"ZeroThicknessWall");
                    var shape=DirectShape.CreateElement(doc,new ElementId(BuiltInCategory.OST_Walls));shape.SetShape(objects);
                    shape.ApplicationId=ApplicationId;shape.ApplicationDataId=record.SourceGuid;
                    shape.Name=WallCompletion.Incomplete+" — исходная нулевая толщина";
                    shape.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.Set(record.Mark);
                    shape.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)?.Set(WallCompletion.Incomplete+": толщина в Archicad 0 м. Поверхность по исходной оси и высоте; объём не добавлен. Профиль после подрезок, проёмы и слои не подтверждены. Границы зон переносятся отдельными нативными линиями. GUID "+record.SourceGuid);
                    Storage.Tag(shape,manifest.Source.ProjectId,record.SourceGuid,hash,record.Raw.GetRawText());doc.Regenerate();Verify(shape,points);
                    if(transaction.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Zero-thickness sheet rolled back");
                    index[record.SourceGuid]=shape;report.Add(record.SourceGuid,"ZeroThicknessWall","incomplete","Source width is exactly zero; nominal API sheet retained without invented volume. Native Wall, cuts and editable layers are unavailable.",shape.UniqueId);
                }
                if(source.RoomBoundary)
                foreach(var levelRecord in manifest.Levels)
                {
                    if(index.GetValueOrDefault(levelRecord.Key) is not Level level)throw new InvalidDataException("Imported source level missing");
                    double plane=level.Elevation+(level.get_Parameter(BuiltInParameter.LEVEL_ROOM_COMPUTATION_HEIGHT)?.AsDouble()??0);
                    if(plane<a.Z-1e-7 || plane>=a.Z+height-1e-7)continue;
                    string key=BoundaryKey(record.SourceGuid,levelRecord.Key);var expected=Line.CreateBound(new XYZ(a.X,a.Y,level.Elevation),new XYZ(b.X,b.Y,level.Elevation));
                    string boundaryHash=Json.Hash(new{hash,levelRecord.Key,plane});
                    if(index.TryGetValue(key,out var stored))
                    {
                        if(stored is not ModelCurve curve || Storage.Read(stored)?.Hash!=boundaryHash || Deviation(curve.GeometryCurve,expected)>Geometry.Feet(1e-6))
                            throw new InvalidDataException("Existing zero-thickness room boundary changed; no overwrite");
                        report.Add(key,"ZeroWallBoundary","unchanged","Native curve verified",stored.UniqueId);continue;
                    }
                    using var transaction=new Transaction(doc,"Room boundary of source zero-thickness wall");transaction.Start();NativeGeometry.Configure(transaction,report,key,"ZeroWallBoundary");
                    if(!views.TryGetValue(level.Id,out var view))
                    {
                        view=new FilteredElementCollector(doc).OfClass(typeof(ViewPlan)).Cast<ViewPlan>().FirstOrDefault(v=>!v.IsTemplate && v.ViewType==ViewType.FloorPlan && v.GenLevel?.Id==level.Id);
                        if(view==null)
                        {
                            var type=new FilteredElementCollector(doc).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>().First(v=>v.ViewFamily==ViewFamily.FloorPlan);
                            view=ViewPlan.Create(doc,type.Id,level.Id);view.Name="AC Zero width boundaries "+level.Id.Value;
                        }
                        views[level.Id]=view;
                    }
                    using var planeDefinition=Plane.CreateByNormalAndOrigin(XYZ.BasisZ,new XYZ(0,0,level.Elevation));var sketch=SketchPlane.Create(doc,planeDefinition);
                    using var curves=new CurveArray();curves.Append(expected);var created=doc.Create.NewRoomBoundaryLines(sketch,curves,view);
                    if(created.Size!=1)throw new InvalidDataException("Unexpected zero-thickness boundary count");
                    var boundary=created.get_Item(0);Storage.Tag(boundary,manifest.Source.ProjectId,key,boundaryHash,JsonSerializer.Serialize(new{sourceGuid=record.SourceGuid,levelKey=levelRecord.Key,source.Curve},Json.Options));
                    doc.Regenerate();if(Deviation(boundary.GeometryCurve,expected)>Geometry.Feet(1e-6))throw new InvalidDataException("Native boundary moved from source axis");
                    if(transaction.Commit()!=TransactionStatus.Committed)throw new InvalidOperationException("Zero-thickness boundary rolled back");
                    index[key]=boundary;report.Add(key,"ZeroWallBoundary","created","Source zero-width axis; native room separator at an intersected source level",boundary.UniqueId);
                }
            }
            catch(Exception ex){views.Clear();report.Add(record.SourceGuid,"ZeroThicknessWall","failed",ex.ToString());}
            if(++done%25==0 || done==records.Length)progress?.Invoke("zeroThicknessWalls",done,records.Length);
        }
        Directory.CreateDirectory(output);report.Save(Path.Combine(output,"zero-thickness-walls.json"));return report;
    }
    static double Deviation(Curve a,Curve b)=>Math.Min(Math.Max(a.GetEndPoint(0).DistanceTo(b.GetEndPoint(0)),a.GetEndPoint(1).DistanceTo(b.GetEndPoint(1))),Math.Max(a.GetEndPoint(0).DistanceTo(b.GetEndPoint(1)),a.GetEndPoint(1).DistanceTo(b.GetEndPoint(0))));
    static bool Coplanar(DirectShape shape,Curve axis,XYZ[] source)
    {
        var vertices=shape.get_Geometry(new Options{DetailLevel=ViewDetailLevel.Fine}).OfType<Mesh>().SelectMany(m=>m.Vertices.Cast<XYZ>()).ToArray();
        var origin=axis.GetEndPoint(0);var direction=(axis.GetEndPoint(1)-origin).Normalize();var normal=direction.CrossProduct(XYZ.BasisZ);double tolerance=Geometry.Feet(.001);
        return vertices.Length>0 && vertices.All(p=>Math.Abs((p-origin).DotProduct(normal))<=tolerance
            && (p-origin).DotProduct(direction)>=-tolerance && (p-origin).DotProduct(direction)<=axis.Length+tolerance
            && p.Z>=source[0].Z-tolerance && p.Z<=source[2].Z+tolerance);
    }
    internal static IList<GeometryObject> BuildSheet(XYZ[] points)
    {
        using var builder=new TessellatedShapeBuilder();builder.OpenConnectedFaceSet(false);
        foreach(int[] triangle in new int[][]{[0,1,2],[0,2,3]})
        {
            using var face=new TessellatedFace(triangle.Select(i=>points[i]).ToList(),ElementId.InvalidElementId);
            if(!builder.DoesFaceHaveEnoughLoopsAndVertices(face))throw new InvalidDataException("Nominal source face rejected");builder.AddFace(face);
        }
        builder.CloseConnectedFaceSet();builder.Target=TessellatedShapeBuilderTarget.Mesh;builder.Fallback=TessellatedShapeBuilderFallback.Salvage;builder.Build();
        var built=builder.GetBuildResult();var objects=built.GetGeometricalObjects();
        if(built.HasInvalidData || objects.Count==0)throw new InvalidDataException("Invalid nominal sheet mesh");return objects;
    }
    internal static void Verify(DirectShape shape,XYZ[] points)
    {
        var meshes=shape.get_Geometry(new Options{DetailLevel=ViewDetailLevel.Fine}).OfType<Mesh>().ToList();
        if(meshes.Sum(m=>m.NumTriangles)!=2)throw new InvalidDataException("Nominal sheet triangle count changed");
        var actual=meshes.SelectMany(m=>Enumerable.Range(0,m.NumTriangles).SelectMany(i=>Enumerable.Range(0,3).Select(j=>m.get_Triangle(i).get_Vertex(j)))).ToArray();
        double tolerance=Geometry.Feet(.001);
        if(points.Any(p=>actual.All(q=>p.DistanceTo(q)>tolerance)) || actual.Any(p=>points.All(q=>p.DistanceTo(q)>tolerance)))throw new InvalidDataException("Nominal sheet vertices differ from source by more than 1 mm");
        var triangles=new List<string>();
        foreach(var mesh in meshes)for(int i=0;i<mesh.NumTriangles;i++)
            triangles.Add(string.Join(",",Enumerable.Range(0,3).Select(j=>Enumerable.Range(0,4).MinBy(k=>points[k].DistanceTo(mesh.get_Triangle(i).get_Vertex(j)))).Order()));
        if(!triangles.Order().SequenceEqual(new[]{"0,1,2","0,2,3"}))throw new InvalidDataException("Nominal sheet triangle topology differs from source");
    }
}
