using Autodesk.Revit.DB;
using System.Text.Json;

namespace Ac2Revit;

/// <summary>Read-only wall geometry snapshot; meshes are tessellated, volumes come from native solids.</summary>
public static class GeometryAudit
{
    public static object Write(Document doc,string output,ISet<string>? meshKeys,Action<int,int>? progress=null,BuiltInCategory category=BuiltInCategory.OST_Walls)
    {
        if(File.Exists(output) || File.Exists(output+".partial"))throw new IOException("Audit output already exists");
        Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        var elements=new FilteredElementCollector(doc).OfCategory(category).WhereElementIsNotElementType().ToList();
        var nativeIds=elements.ToDictionary(e=>e.Id,e=>Storage.Read(e)?.Key??e.UniqueId);
        var options=new Options{DetailLevel=ViewDetailLevel.Fine,IncludeNonVisibleObjects=false,ComputeReferences=false};
        var json=new JsonSerializerOptions(Json.Options){WriteIndented=false};
        int failures=0,done=0;
        using(var writer=new StreamWriter(new FileStream(output+".partial",FileMode.CreateNew,FileAccess.Write)))
        foreach(var element in elements)
        {
            var tag=Storage.Read(element);string source=tag?.Key??"";
            bool saveMesh=meshKeys==null || meshKeys.Contains(source);
            var vertices=new List<double[]>();var faces=new List<int[]>();
            double[] lo=[double.PositiveInfinity,double.PositiveInfinity,double.PositiveInfinity],hi=[double.NegativeInfinity,double.NegativeInfinity,double.NegativeInfinity];
            double volume=0;int solids=0,meshOnly=0;string error="";
            double[] Point(XYZ p)=>[UnitUtils.ConvertFromInternalUnits(p.X,UnitTypeId.Meters),UnitUtils.ConvertFromInternalUnits(p.Y,UnitTypeId.Meters),UnitUtils.ConvertFromInternalUnits(p.Z,UnitTypeId.Meters)];
            void AddMesh(Mesh mesh)
            {
                for(int i=0;i<mesh.NumTriangles;i++)
                {
                    var triangle=mesh.get_Triangle(i);int start=vertices.Count;
                    for(int k=0;k<3;k++)
                    {
                        var p=Point(triangle.get_Vertex(k));
                        for(int axis=0;axis<3;axis++){lo[axis]=Math.Min(lo[axis],p[axis]);hi[axis]=Math.Max(hi[axis],p[axis]);}
                        if(saveMesh)vertices.Add(p);
                    }
                    if(saveMesh)faces.Add([start,start+1,start+2]);
                }
            }
            void Read(GeometryElement geometry)
            {
                foreach(var item in geometry)
                    if(item is GeometryInstance instance)Read(instance.GetInstanceGeometry());
                    else if(item is Solid solid && solid.Faces.Size>0)
                    {
                        solids++;volume+=UnitUtils.ConvertFromInternalUnits(solid.Volume,UnitTypeId.CubicMeters);
                        foreach(Face face in solid.Faces)AddMesh(face.Triangulate());
                    }
                    else if(item is Mesh mesh){meshOnly++;AddMesh(mesh);}
            }
            var parameters=new Dictionary<string,string>();
            foreach(Parameter parameter in element.Parameters)
                if(parameter.StorageType==StorageType.String && parameter.AsString() is string value && value.Length>0)
                    parameters[parameter.Definition.Name]=value;
            try { var geometry=element.get_Geometry(options);if(geometry!=null)Read(geometry);else error="No geometry"; }
            catch(Exception ex){error=ex.Message;failures++;}
            bool bounded=lo.All(double.IsFinite)&&hi.All(double.IsFinite);
            var ends=new List<object>();
            if(element is Wall wall && wall.Location is LocationCurve curve)
                for(int end=0;end<2;end++)
                    ends.Add(new{end,allowed=WallUtils.IsWallJoinAllowedAtEnd(wall,end),neighbors=curve.get_ElementsAtJoin(end).Cast<Element>().Where(n=>n.Id!=wall.Id).Select(n=>nativeIds.GetValueOrDefault(n.Id,n.UniqueId)).ToArray()});
            writer.WriteLine(JsonSerializer.Serialize(new{sourceGuid=source,projectId=tag?.Project??"",elementId=element.Id.Value,revitUniqueId=element.UniqueId,
                elementClass=element.GetType().Name,name=element.Name,parameters,boundsMinMeters=bounded?lo:null,boundsMaxMeters=bounded?hi:null,
                nativeSolidVolumeM3=volume,solidCount=solids,meshOnlyCount=meshOnly,vertices=saveMesh?vertices:null,faces=saveMesh?faces:null,
                meshSaved=saveMesh,geometryStatus=error.Length>0?"error":bounded?"extracted":"empty",error,wallEndJoins=ends},json));
            if(++done%100==0){writer.Flush();progress?.Invoke(done,elements.Count);}
        }
        File.Move(output+".partial",output);
        return new{documentPath=doc.PathName,wallCategoryElements=elements.Count,nativeWalls=elements.Count(e=>e is Wall),
            category=category.ToString(),categoryElements=elements.Count,nativeFamilyInstances=elements.Count(e=>e is FamilyInstance),
            directShapes=elements.Count(e=>e is DirectShape),failures,output,geometryEqualityProven=false};
    }
}
